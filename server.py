"""ds-chat —— 用一段长密钥进入的私密聊天室。

双方在网页里输入同一段密钥，就会进入同一个房间。密钥只在浏览器里使用：
房间号和加密消息用的钥匙都由它推导出来，服务器只见到房间号和密文。

第一个进入房间的人是创建者。创建者和管理员可以立即销毁房间，
或者设一个 1～12 小时的倒计时，到点自动销毁（房间和全部消息一起删除）。
每个房间最多 7 人（含创建者，创建者的位置一直保留），创建者和管理员可以调成 2～7 人。

只用 Python 标准库（3.6 及以上），不用装任何依赖，64 MB 内存的小机器也能跑：
    python3 server.py
设了 CERT_FILE 和 KEY_FILE 就直接提供 HTTPS，不需要 nginx。
在服务器上安装用 install.sh（一键），设置项见 README。
"""
import asyncio
import base64
import gzip
import hashlib
import hmac
import http
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sqlite3
import ssl
import struct
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
TRUST_PROXY = os.environ.get("TRUST_PROXY", "") == "1"  # 在 nginx 等反向代理后面时打开，才认 X-Forwarded-* 头
CLOUDFLARE = os.environ.get("CLOUDFLARE", "") == "1"    # 挂在 Cloudflare 后面时打开，从 CF-Connecting-IP 读访问者真实 IP
IDLE_DAYS = float(os.environ.get("ROOM_IDLE_DAYS", "30"))  # 没人进出这么多天的房间自动清掉，0 = 永不
CERT_FILE = os.environ.get("CERT_FILE", "")  # 证书和私钥都设了就直接用 HTTPS
KEY_FILE = os.environ.get("KEY_FILE", "")
MAX_CONN = int(os.environ.get("MAX_CONN", "200"))  # 同时最多多少个连接，防止小机器被挤爆
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

MAX_HOURS = 12
MAX_POLICY_HOURS = 168  # 全局自动销毁最长一周
MIN_PEOPLE, MAX_PEOPLE = 2, 7  # 房间人数上限可调的范围，新房间默认 MAX_PEOPLE
HISTORY = 300          # 进入房间时发回最近多少条
KEEP = 1000            # 每个房间最多保存多少条
MAX_CIPHER = 16_000    # 一条密文最长多少字符（约 1 万字明文）
MAX_ROOMS = 20_000
MAX_BODY = 64 * 1024   # HTTP 请求体和 WebSocket 单条消息的上限
CHUNK_MAX = 2 * 1024 * 1024  # 上传文件时每一块（已加密）的上限；浏览器每块传 1 MB
MIN_FREE_MB = int(os.environ.get("MIN_FREE_MB", "300"))  # 硬盘剩余少于这么多 MB 就不收新文件，免得把机器塞满
FILE_ID = re.compile(r"^[0-9a-f]{32}$")
ADMIN_TTL = 12 * 3600
ROOM_ID = re.compile(r"^[0-9a-f]{64}$")

# Cloudflare 的服务器网段（https://www.cloudflare.com/ips/）。只有从这些地址来的请求，
# 才相信它带的 CF-Connecting-IP；否则谁都能直连服务器、伪造 IP 绕过登录次数限制。
CF_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)]


def from_cloudflare(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr in net for net in CF_NETS)

log = logging.getLogger("ds-chat")


# ---------- 存储 ----------

def open_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(os.path.join(DATA_DIR, "chat.db"), isolation_level=None)
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")  # 新数据库：删掉的数据腾出的空间可以还给硬盘
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA journal_size_limit=1048576")  # WAL 临时文件清理后最多留 1 MB
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA cache_size=-1024")  # 页缓存只给 1 MB
    db.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            creator_hash TEXT NOT NULL,
            created REAL NOT NULL,
            last_active REAL NOT NULL,
            destroy_at REAL,
            max_people INTEGER NOT NULL DEFAULT 7
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            ts REAL NOT NULL,
            data TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS messages_room ON messages(room, id);
        CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            room TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            size INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL,
            updated REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS files_room ON files(room);
    """)
    if "max_people" not in [c[1] for c in db.execute("PRAGMA table_info(rooms)")]:
        db.execute(f"ALTER TABLE rooms ADD COLUMN max_people INTEGER NOT NULL DEFAULT {MAX_PEOPLE}")
    if "file" not in [c[1] for c in db.execute("PRAGMA table_info(messages)")]:
        db.execute("ALTER TABLE messages ADD COLUMN file TEXT")  # 这条消息带的文件，删消息时一起删
    db.execute("CREATE INDEX IF NOT EXISTS messages_file ON messages(file) WHERE file IS NOT NULL")
    if db.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
        # 旧版本建的数据库没开自动瘦身：整理一次就开上了（数据库很小，一眨眼的事）
        db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        db.execute("VACUUM")
    return db


def read_or_create(name, make):
    """data 目录里的一个小文件：有就读，没有就生成并保存。"""
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path) as f:
            value = f.read().strip()
        if value:
            return value, False
    except FileNotFoundError:
        pass
    value = make()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    return value, True


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- HTTP ----------

class HTTPError(Exception):
    def __init__(self, status, text=""):
        super().__init__(text)
        self.status = status
        self.text = text


class Request:
    def __init__(self, method, path, headers, body, remote, tls=False, query=""):
        self.tls = tls
        self.query = dict(p.partition("=")[::2] for p in query.split("&") if p)
        self.method = method
        self.path = path
        self.headers = headers  # 键都是小写
        self.body = body
        self.remote = remote
        self.cookies = {}
        if "cookie" in headers:
            try:
                self.cookies = {k: m.value for k, m in SimpleCookie(headers["cookie"]).items()}
            except Exception:
                pass

    @property
    def host(self):
        return (self.headers.get("x-forwarded-host") if TRUST_PROXY else None) or self.headers.get("host", "")

    @property
    def secure(self):
        return self.tls or (TRUST_PROXY and self.headers.get("x-forwarded-proto") == "https")

    def json(self):
        try:
            data = json.loads(self.body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


class Response:
    def __init__(self, body=b"", status=200, ctype="text/plain; charset=utf-8", headers=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.headers = {"Content-Type": ctype}
        self.headers.update(headers or {})
        self.cookies = []


def json_response(data, status=200):
    return Response(json.dumps(data, ensure_ascii=False), status, "application/json; charset=utf-8")


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'"
    ),
}


def encode_response(resp, head_only=False, keep_alive=True, length=None):
    phrase = http.HTTPStatus(resp.status).phrase
    lines = [f"HTTP/1.1 {resp.status} {phrase}"]
    headers = dict(SECURITY_HEADERS)
    headers.update(resp.headers)
    headers["Content-Length"] = str(len(resp.body) if length is None else length)
    headers["Connection"] = "keep-alive" if keep_alive else "close"
    lines += [f"{k}: {v}" for k, v in headers.items()]
    lines += [f"Set-Cookie: {c}" for c in resp.cookies]
    data = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")
    return data if head_only else data + resp.body


class Static:
    """static 目录里的文件启动时读进内存（一共几十 KB），顺带准备好 gzip 版本。"""

    TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
             ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}

    def __init__(self, root):
        self.files = {}
        for name in os.listdir(root):
            ext = os.path.splitext(name)[1]
            if ext not in self.TYPES:
                continue
            with open(os.path.join(root, name), "rb") as f:
                raw = f.read()
            gz = gzip.compress(raw, 9) if ext != ".png" else None
            etag = '"' + hashlib.sha256(raw).hexdigest()[:16] + '"'
            self.files[name] = (raw, gz, self.TYPES[ext], etag)

    def serve(self, request, name):
        if name not in self.files:
            raise HTTPError(404, "没有这个文件")
        raw, gz, ctype, etag = self.files[name]
        headers = {"Cache-Control": "no-cache", "ETag": etag, "Vary": "Accept-Encoding"}
        if request.headers.get("if-none-match") == etag:
            resp = Response(b"", 304, ctype, headers)
            return resp
        if gz and "gzip" in request.headers.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
            return Response(gz, 200, ctype, headers)
        return Response(raw, 200, ctype, headers)


# ---------- WebSocket（RFC 6455，只做本程序用得到的部分） ----------

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocket:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.closed = False
        self._drain = asyncio.Lock()

    async def _write(self, opcode, payload=b""):
        if self.closed and opcode != 0x8:
            raise ConnectionError("closed")
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        self.writer.write(head + payload)
        async with self._drain:  # 3.9 以前并发 drain 会出错，排个队
            try:
                await asyncio.wait_for(self.writer.drain(), 15)
            except asyncio.TimeoutError:
                self.abort()  # 对方一直不收：别让数据堆在内存里
                raise ConnectionError("slow client")

    async def send_str(self, text):
        await self._write(0x1, text.encode())

    async def ping(self):
        await self._write(0x9)

    async def close(self, code=1000, message=b""):
        if self.closed:
            return
        self.closed = True
        try:
            await self._write(0x8, struct.pack("!H", code) + message[:120])
        except (ConnectionError, OSError):
            pass
        self.abort()

    def abort(self):
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass

    async def _frame(self):
        b1, b2 = await self.reader.readexactly(2)
        fin, opcode, masked, n = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", await self.reader.readexactly(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", await self.reader.readexactly(8))
        if not masked or n > MAX_BODY:
            raise ValueError("bad frame")
        mask = await self.reader.readexactly(4)
        data = await self.reader.readexactly(n)
        if n:
            key = (mask * (n // 4 + 1))[:n]
            data = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")
        return fin, opcode, data

    async def recv(self):
        """下一条文本消息；连接断了返回 None。70 秒没任何动静（包括心跳回应）也算断了。"""
        parts, size = [], 0
        while not self.closed:
            try:
                fin, opcode, data = await asyncio.wait_for(self._frame(), 70)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
                self.abort()
                return None
            except ValueError:
                await self.close(1009)
                return None
            if opcode == 0x8:
                await self.close()
                return None
            if opcode == 0x9:
                try:
                    await self._write(0xA, data)
                except ConnectionError:
                    return None
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2) and parts:
                await self.close(1002)
                return None
            parts.append(data)
            size += len(data)
            if size > MAX_BODY:
                await self.close(1009)
                return None
            if fin:
                msg = b"".join(parts)
                parts, size = [], 0
                try:
                    return msg.decode()
                except UnicodeDecodeError:
                    continue
        return None


# ---------- 房间 ----------

class Hub:
    def __init__(self, db):
        self.db = db
        self.online = {}  # 房间号 -> 在线连接
        self.kind = {}    # 连接 -> "creator" / "admin" / "member"
        self.dirty = False  # 删过东西，等着把空间还给硬盘
        conf = dict(db.execute("SELECT k, v FROM settings"))
        self.policy_hours = int(conf["policy_hours"]) if conf.get("policy_hours") else None
        self.policy_since = float(conf.get("policy_since") or 0)
        # 新建房间开关：关闭时只能进已有的房间（管理员除外）；开放时可以设一个自动关闭的时间
        self.create_open = conf.get("create_open", "1") == "1"
        self.create_close_at = float(conf["create_close_at"]) if conf.get("create_close_at") else None

    def set_creation(self, open_, close_at):
        self.create_open = open_
        self.create_close_at = close_at if open_ else None
        self.db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('create_open', ?)", ("1" if open_ else "0",))
        self.db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('create_close_at', ?)",
                        (str(self.create_close_at) if self.create_close_at else "",))

    def creation_info(self):
        return {"open": self.create_open, "closeAt": self.create_close_at}

    # --- 全局自动销毁：所有房间（含以后新开的）最多活 policy_hours 小时。
    #     开启前就有的房间从开启时算起，之后新开的从创建时算起。
    def policy_at(self, created):
        if not self.policy_hours:
            return None
        return max(created, self.policy_since) + self.policy_hours * 3600

    def timer_info(self, row):
        """房间什么时候销毁：房主/管理员单独设的倒计时和全局自动销毁，哪个早算哪个。"""
        own, pol = row[4], self.policy_at(row[2])
        times = [t for t in (own, pol) if t is not None]
        return {"destroyAt": min(times) if times else None, "own": own, "policyAt": pol}

    async def set_policy(self, hours):
        self.policy_hours = hours
        self.policy_since = time.time()
        self.db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('policy_hours', ?)", (str(hours or ""),))
        self.db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('policy_since', ?)", (str(self.policy_since),))
        for rid in list(self.online):
            row = self.room(rid)
            if row:
                await self.broadcast(rid, {"t": "timer", **self.timer_info(row), "by": "policy"})

    def compact(self):
        """把删掉的房间和消息腾出的空间还给硬盘，并清掉 WAL 临时文件。"""
        self.db.execute("PRAGMA incremental_vacuum").fetchall()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        self.dirty = False

    def room(self, rid):
        return self.db.execute(
            "SELECT id, creator_hash, created, last_active, destroy_at, max_people FROM rooms WHERE id=?", (rid,)
        ).fetchone()

    def create(self, rid):
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute(
            "INSERT INTO rooms (id, creator_hash, created, last_active, max_people) VALUES (?,?,?,?,?)",
            (rid, token_hash(token), now, now, MAX_PEOPLE),
        )
        return token

    def touch(self, rid):
        self.db.execute("UPDATE rooms SET last_active=? WHERE id=?", (time.time(), rid))

    def history(self, rid):
        rows = self.db.execute(
            "SELECT id, ts, data FROM messages WHERE room=? ORDER BY id DESC LIMIT ?", (rid, HISTORY)
        ).fetchall()
        return [{"id": i, "ts": ts, "data": d} for i, ts, d in reversed(rows)]

    def add_message(self, rid, data, file=None):
        now = time.time()
        cur = self.db.execute("INSERT INTO messages (room, ts, data, file) VALUES (?,?,?,?)", (rid, now, data, file))
        old = cur.lastrowid - KEEP
        for (fid,) in self.db.execute(
                "SELECT file FROM messages WHERE room=? AND id <= ? AND file IS NOT NULL", (rid, old)).fetchall():
            self.delete_file(rid, fid)  # 太旧被清掉的消息，带的文件也删
        if self.db.execute("DELETE FROM messages WHERE room=? AND id <= ?", (rid, old)).rowcount:
            self.dirty = True
        self.db.execute("UPDATE rooms SET last_active=? WHERE id=?", (now, rid))
        return {"id": cur.lastrowid, "ts": now, "data": data}

    async def broadcast(self, rid, payload, skip=None):
        text = json.dumps(payload)
        targets = [ws for ws in self.online.get(rid, ()) if ws is not skip and not ws.closed]
        if targets:
            await asyncio.gather(*(ws.send_str(text) for ws in targets), return_exceptions=True)

    async def presence(self, rid):
        await self.broadcast(rid, {"t": "presence", "online": len(self.online.get(rid, ()))})

    async def set_timer(self, rid, hours, by):
        """hours: 0 = 立即销毁，1～12 = 倒计时，None = 取消倒计时。返回 False 表示房间不存在。"""
        if not self.room(rid):
            return False
        if hours == 0:
            await self.destroy(rid, by)
            return True
        at = None if hours is None else time.time() + hours * 3600
        self.db.execute("UPDATE rooms SET destroy_at=? WHERE id=?", (at, rid))
        await self.broadcast(rid, {"t": "timer", **self.timer_info(self.room(rid)), "by": by})
        return True

    def members(self, rid):
        """占名额的人数：管理员不占，创建者的位置另外保留。"""
        return sum(1 for ws in self.online.get(rid, ()) if self.kind.get(ws) == "member")

    async def set_limit(self, rid, n, by):
        if not self.room(rid):
            return False
        self.db.execute("UPDATE rooms SET max_people=? WHERE id=?", (n, rid))
        await self.broadcast(rid, {"t": "limit", "value": n, "by": by})
        return True

    # --- 文件：存在 data/files/<房间号>/<文件号>，内容是浏览器加密过的 ---
    @staticmethod
    def file_path(rid, fid):
        return os.path.join(DATA_DIR, "files", rid, fid)

    def delete_file(self, rid, fid):
        self.db.execute("DELETE FROM files WHERE id=? AND room=?", (fid, rid))
        try:
            os.remove(self.file_path(rid, fid))
        except OSError:
            pass
        self.dirty = True

    def sweep_files(self):
        """清理文件垃圾：传到一半放弃的、传完但没发出去的、房间已经没了的。"""
        now = time.time()
        for rid, fid in self.db.execute(
                "SELECT room, id FROM files WHERE done=0 AND updated < ?", (now - 6 * 3600,)).fetchall():
            self.delete_file(rid, fid)
        for rid, fid in self.db.execute("""
                SELECT f.room, f.id FROM files f WHERE f.done=1 AND f.updated < ?
                AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.file = f.id)""", (now - 3600,)).fetchall():
            self.delete_file(rid, fid)
        root = os.path.join(DATA_DIR, "files")
        if os.path.isdir(root):
            rooms = {r for (r,) in self.db.execute("SELECT id FROM rooms")}
            for rid in os.listdir(root):
                if rid not in rooms:
                    shutil.rmtree(os.path.join(root, rid), ignore_errors=True)
                    continue
                known = {f for (f,) in self.db.execute("SELECT id FROM files WHERE room=?", (rid,))}
                for fid in os.listdir(os.path.join(root, rid)):
                    if fid not in known:
                        try:
                            os.remove(os.path.join(root, rid, fid))
                        except OSError:
                            pass

    async def destroy(self, rid, by):
        self.db.execute("DELETE FROM rooms WHERE id=?", (rid,))  # 消息和文件记录跟着一起删（ON DELETE CASCADE）
        shutil.rmtree(os.path.join(DATA_DIR, "files", rid), ignore_errors=True)
        self.dirty = True
        await self.broadcast(rid, {"t": "destroyed", "by": by})
        for ws in list(self.online.pop(rid, ())):
            await ws.close(4000, b"destroyed")
        log.debug("房间 %s 已销毁（%s）", rid[:6], by)

    async def sweep(self):
        """到点的倒计时、太久没人用的房间。"""
        now = time.time()
        due = [r for (r,) in self.db.execute(
            "SELECT id FROM rooms WHERE destroy_at IS NOT NULL AND destroy_at <= ?", (now,))]
        for rid in due:
            await self.destroy(rid, "timer")
        if self.create_open and self.create_close_at and self.create_close_at <= now:
            self.set_creation(False, None)  # 限时开放到点了
        if self.policy_hours:
            due = [r for (r,) in self.db.execute(
                "SELECT id FROM rooms WHERE MAX(created, ?) <= ?",
                (self.policy_since, now - self.policy_hours * 3600))]
            for rid in due:
                await self.destroy(rid, "timer")
        if IDLE_DAYS > 0:
            stale = [r for (r,) in self.db.execute(
                "SELECT id FROM rooms WHERE last_active < ?", (now - IDLE_DAYS * 86400,))]
            for rid in stale:
                if not self.online.get(rid):
                    await self.destroy(rid, "idle")


# ---------- 应用 ----------

class App:
    def __init__(self):
        self.hub = Hub(open_db())
        self.static = Static(STATIC_DIR)
        self.secret, _ = read_or_create("secret.txt", lambda: secrets.token_hex(32))
        env_pw = os.environ.get("ADMIN_PASSWORD", "")
        if env_pw:
            self.admin_password = env_pw
        else:
            self.admin_password, new = read_or_create("admin_password.txt", lambda: secrets.token_urlsafe(18))
            log.info("管理员密码%s保存在 %s（设置环境变量 ADMIN_PASSWORD 可以改用自己的）",
                     "已生成并" if new else "", os.path.join(DATA_DIR, "admin_password.txt"))
        self.login_fails = {}
        self.conns = 0

    # --- 工具 ---

    def client_ip(self, request):
        if CLOUDFLARE and from_cloudflare(request.remote):
            real = request.headers.get("cf-connecting-ip", "").strip()
            if real:
                return real
        if TRUST_PROXY:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.remote

    def admin_cookie(self):
        exp = str(int(time.time()) + ADMIN_TTL)
        sig = hmac.new(self.secret.encode(), exp.encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{sig}"

    def is_admin(self, request):
        raw = request.cookies.get("dsadmin", "")
        exp, _, sig = raw.partition(".")
        if not exp.isdigit() or int(exp) < time.time():
            return False
        good = hmac.new(self.secret.encode(), exp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good)

    @staticmethod
    def same_origin(request):
        """拦掉别的网站借用户浏览器发来的请求。没有 Origin 头的（非浏览器）放行。"""
        origin = request.headers.get("origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == request.host

    def need_admin(self, request):
        if not self.same_origin(request):
            raise HTTPError(403, "bad origin")
        if not self.is_admin(request):
            raise HTTPError(401, "需要管理员登录")

    # --- 路由 ---

    async def route(self, request):
        path, method = request.path, request.method
        if method in ("GET", "HEAD"):
            if path == "/":
                return self.static.serve(request, "index.html")
            if path == "/admin":
                return self.static.serve(request, "admin.html")
            if path.startswith("/static/"):
                return self.static.serve(request, path[len("/static/"):])
            if path == "/api/status":
                return json_response({"creation": self.hub.creation_info()})
            if path == "/api/admin/me":
                return json_response({"admin": self.is_admin(request)})
            if path == "/api/admin/rooms":
                self.need_admin(request)
                return self.admin_rooms()
        if method == "POST":
            if path == "/api/admin/login":
                return self.admin_login(request)
            if path == "/api/admin/logout":
                self.need_admin(request)
                resp = json_response({"ok": True})
                resp.cookies.append("dsadmin=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
                return resp
            if path == "/api/admin/rooms/batch":
                self.need_admin(request)
                return await self.admin_batch(request.json())
            if path == "/api/admin/creation":
                self.need_admin(request)
                return self.admin_creation(request.json())
            if path == "/api/admin/policy":
                self.need_admin(request)
                return await self.admin_policy(request.json())
            m = re.match(r"^/api/admin/rooms/([0-9a-f]{64})/(timer|limit)$", path)
            if m:
                self.need_admin(request)
                return await self.admin_room_action(m.group(1), m.group(2), request.json())
        raise HTTPError(404, "没有这个页面")

    def admin_login(self, request):
        if not self.same_origin(request):
            raise HTTPError(403, "bad origin")
        ip = self.client_ip(request)
        now = time.time()
        recent = [t for t in self.login_fails.get(ip, []) if now - t < 600]
        if len(recent) >= 5:
            return json_response({"ok": False, "error": "尝试太多次，请 10 分钟后再试"}, 429)
        password = str(request.json().get("password", ""))
        if not hmac.compare_digest(password.encode(), self.admin_password.encode()):
            recent.append(now)
            self.login_fails[ip] = recent
            if len(self.login_fails) > 10_000:
                self.login_fails.clear()
            left = 5 - len(recent)
            return json_response({"ok": False, "error": f"密码不对，还能试 {left} 次", "left": left}, 401)
        self.login_fails.pop(ip, None)
        resp = json_response({"ok": True})
        resp.cookies.append(f"dsadmin={self.admin_cookie()}; Path=/; Max-Age={ADMIN_TTL}; HttpOnly; SameSite=Strict"
                            + ("; Secure" if request.secure else ""))
        return resp

    def admin_rooms(self):
        hub = self.hub
        rows = hub.db.execute("""
            SELECT r.id, r.created, r.last_active, r.destroy_at, r.max_people,
                   (SELECT COUNT(*) FROM messages m WHERE m.room = r.id),
                   (SELECT COUNT(*) FROM files f WHERE f.room = r.id AND f.done = 1),
                   (SELECT COALESCE(SUM(size), 0) FROM files f WHERE f.room = r.id)
            FROM rooms r ORDER BY r.last_active DESC
        """).fetchall()
        try:
            db_bytes = sum(os.path.getsize(os.path.join(DATA_DIR, n)) for n in os.listdir(DATA_DIR)
                           if n.startswith("chat.db"))
        except OSError:
            db_bytes = 0
        return json_response({
            "now": time.time(),
            "policy": {"hours": hub.policy_hours, "since": hub.policy_since if hub.policy_hours else None},
            "creation": hub.creation_info(),
            "storage": {"files": sum(r[7] for r in rows), "db": db_bytes, "freeMb": self.free_mb()},
            "rooms": [{
                "id": rid, "code": rid[:6].upper(), "created": created, "lastActive": active,
                **hub.timer_info((rid, None, created, active, at, limit)),
                "limit": limit, "messages": n, "files": nf, "fileBytes": fb,
                "online": len(hub.online.get(rid, ())),
            } for rid, created, active, at, limit, n, nf, fb in rows],
        })

    async def admin_batch(self, body):
        ids = body.get("ids")
        hours = parse_hours(body.get("hours"))
        if hours is False or not isinstance(ids, list) or not 0 < len(ids) <= 5000 \
                or not all(isinstance(i, str) and ROOM_ID.match(i) for i in ids):
            raise HTTPError(400, "参数不对")
        done = 0
        for rid in dict.fromkeys(ids):
            if await self.hub.set_timer(rid, hours, "admin"):
                done += 1
        return json_response({"ok": True, "done": done})

    def admin_creation(self, body):
        """{"open": true, "hours": 6} 开放 6 小时后自动关闭；{"open": true} 一直开放；{"open": false} 关闭。"""
        open_, hours = body.get("open"), body.get("hours")
        if not isinstance(open_, bool) or (hours is not None and not (
                isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= MAX_POLICY_HOURS)):
            raise HTTPError(400, "参数不对")
        self.hub.set_creation(open_, time.time() + hours * 3600 if open_ and hours else None)
        return json_response({"ok": True, "creation": self.hub.creation_info()})

    async def admin_policy(self, body):
        hours = body.get("hours")
        if hours is not None and not (isinstance(hours, int) and not isinstance(hours, bool)
                                      and 1 <= hours <= MAX_POLICY_HOURS):
            raise HTTPError(400, f"时长只能是 1～{MAX_POLICY_HOURS} 小时")
        await self.hub.set_policy(hours)
        await self.hub.sweep()
        return json_response({"ok": True})

    async def admin_room_action(self, rid, action, body):
        if action == "timer":
            hours = parse_hours(body.get("hours"))
            if hours is False:
                raise HTTPError(400, "参数不对")
            done = await self.hub.set_timer(rid, hours, "admin")
        else:
            n = parse_limit(body.get("value"))
            if n is None:
                raise HTTPError(400, "参数不对")
            done = await self.hub.set_limit(rid, n, "admin")
        if not done:
            raise HTTPError(404, "房间不存在")
        return json_response({"ok": True})

    # --- 连接 ---

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        remote = peer[0] if peer else "?"
        if self.conns >= MAX_CONN:
            writer.close()
            return
        self.conns += 1
        try:
            while True:
                request = await self.read_request(reader, writer, remote)
                if request is None:
                    return
                if request.path == "/ws" and request.headers.get("upgrade", "").lower() == "websocket":
                    await self.websocket(request, reader, writer)
                    return
                keep = request.headers.get("connection", "").lower() != "close"
                if request.path.startswith("/api/files/"):
                    try:
                        keep = await self.files(request, reader, writer) and keep
                    except HTTPError as e:
                        writer.write(encode_response(Response(e.text, e.status), keep_alive=False))
                        await asyncio.wait_for(writer.drain(), 30)
                        return
                    if not keep:
                        return
                    continue
                try:
                    resp = await self.route(request)
                except HTTPError as e:
                    resp = Response(e.text, e.status)
                except Exception:
                    log.exception("处理 %s %s 出错", request.method, request.path)
                    resp = Response("服务器出错了", 500)
                writer.write(encode_response(resp, request.method == "HEAD", keep))
                await asyncio.wait_for(writer.drain(), 30)
                if not keep:
                    return
        except (ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            self.conns -= 1
            try:
                writer.close()
            except Exception:
                pass

    async def read_request(self, reader, writer, remote):
        """读一个 HTTP 请求；连接空闲 30 秒或者请求不合规矩就返回 None。"""
        try:
            line = await asyncio.wait_for(reader.readline(), 30)
        except (asyncio.TimeoutError, ValueError):
            return None
        if not line:
            return None
        try:
            method, target, _ = line.decode("latin-1").split()
        except ValueError:
            return None
        headers = {}
        for _ in range(100):
            raw = await asyncio.wait_for(reader.readline(), 15)
            if raw in (b"\r\n", b"\n", b""):
                break
            k, _, v = raw.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        else:
            return None
        url = urlsplit(target)
        path = url.path or "/"
        length = int(headers.get("content-length", "0") or 0)
        # 上传文件的那一块不在这里读：交给 files() 边收边写硬盘，不占内存
        upload = method.upper() == "PUT" and path.startswith("/api/files/")
        if length > (CHUNK_MAX if upload else MAX_BODY) or "chunked" in headers.get("transfer-encoding", ""):
            writer.write(encode_response(Response("请求太大", 413), keep_alive=False))
            return None
        if upload:
            body = None
        else:
            body = await asyncio.wait_for(reader.readexactly(length), 15) if length else b""
        tls = writer.get_extra_info("sslcontext") is not None
        request = Request(method.upper(), path, headers, body, remote, tls, url.query)
        request.length = length
        return request

    # --- 文件上传 / 下载 ---
    #   POST /api/files/<房间>                    开始一个上传，返回文件号
    #   PUT  /api/files/<房间>/<文件>?offset=N    追加一块（断了可以从 offset 接着传）
    #   POST /api/files/<房间>/<文件>/done        传完了
    #   POST /api/files/<房间>/<文件>/cancel      不传了
    #   GET  /api/files/<房间>/<文件>             下载，支持 Range
    # 知道房间号（也就是知道密钥）的人才能用；文件内容是浏览器加密过的，服务器看不懂。

    async def files(self, request, reader, writer):
        """处理文件请求并自己写回应；返回 False 表示这条连接不能再用了。"""
        hub = self.hub
        if not self.same_origin(request):
            raise HTTPError(403, "bad origin")
        parts = request.path.split("/")[3:]
        if not parts or not ROOM_ID.match(parts[0]) or not hub.room(parts[0]):
            raise HTTPError(404, "房间不存在")
        rid = parts[0]
        method = request.method

        def reply(resp):
            writer.write(encode_response(resp, method == "HEAD"))

        if len(parts) == 1 and method == "POST":
            if self.free_mb() < MIN_FREE_MB:
                raise HTTPError(507, "服务器硬盘空间不够了，暂时不能发文件")
            if hub.db.execute("SELECT COUNT(*) FROM files WHERE room=? AND done=0", (rid,)).fetchone()[0] >= 20:
                raise HTTPError(429, "同时上传的文件太多了，等一会儿")
            fid = secrets.token_hex(16)
            os.makedirs(os.path.dirname(hub.file_path(rid, fid)), exist_ok=True)
            open(hub.file_path(rid, fid), "wb").close()
            now = time.time()
            hub.db.execute("INSERT INTO files (id, room, created, updated) VALUES (?,?,?,?)", (fid, rid, now, now))
            reply(json_response({"id": fid}))
            return True

        if len(parts) < 2 or not FILE_ID.match(parts[1]):
            raise HTTPError(404, "没有这个文件")
        fid = parts[1]
        row = hub.db.execute("SELECT size, done FROM files WHERE id=? AND room=?", (fid, rid)).fetchone()
        if not row:
            raise HTTPError(404, "文件不存在或已被清理")
        size, done = row
        path = hub.file_path(rid, fid)

        if len(parts) == 2 and method == "PUT":
            if done:
                raise HTTPError(409, "文件已经传完了")
            if request.query.get("offset") != str(size):
                # 位置对不上（比如断线重传）：告诉浏览器从哪接着传。这一块的数据不要了
                await self.discard(reader, request.length)
                reply(json_response({"size": size}, 409))
                return True
            if self.free_mb() < MIN_FREE_MB // 3:
                raise HTTPError(507, "服务器硬盘空间不够了")
            left = request.length
            with open(path, "r+b") as f:
                f.seek(size)
                try:
                    while left:
                        data = await asyncio.wait_for(reader.read(min(65536, left)), 30)
                        if not data:
                            raise ConnectionError("upload cut off")
                        f.write(data)
                        left -= len(data)
                except BaseException:
                    f.truncate(size)  # 只收到半块：退回去，下次从原位置接着传
                    raise
            hub.db.execute("UPDATE files SET size=?, updated=? WHERE id=?", (size + request.length, time.time(), fid))
            reply(json_response({"size": size + request.length}))
            return True

        if len(parts) == 3 and method == "POST" and parts[2] in ("done", "cancel"):
            if parts[2] == "done":
                hub.db.execute("UPDATE files SET done=1, updated=? WHERE id=?", (time.time(), fid))
            elif not done:
                hub.delete_file(rid, fid)
            reply(json_response({"ok": True}))
            return True

        if len(parts) == 2 and method in ("GET", "HEAD"):
            if not done:
                raise HTTPError(404, "文件还没传完")
            start, end = 0, size - 1
            status = 200
            m = re.match(r"^bytes=(\d+)-(\d*)$", request.headers.get("range", ""))
            if m and size:
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
                if start > end:
                    raise HTTPError(416, "范围不对")
                status = 206
            headers = {"Cache-Control": "private, no-store", "Accept-Ranges": "bytes"}
            if status == 206:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            writer.write(encode_response(Response(b"", status, "application/octet-stream", headers), True,
                                         length=end - start + 1 if size else 0))
            if method == "GET" and size:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left:
                        data = f.read(min(65536, left))
                        if not data:
                            break
                        writer.write(data)
                        left -= len(data)
                        await asyncio.wait_for(writer.drain(), 60)
            return True

        raise HTTPError(404, "没有这个页面")

    @staticmethod
    async def discard(reader, n):
        while n > 0:
            data = await asyncio.wait_for(reader.read(min(65536, n)), 30)
            if not data:
                raise ConnectionError("cut off")
            n -= len(data)

    @staticmethod
    def free_mb():
        try:
            return shutil.disk_usage(DATA_DIR).free // (1024 * 1024)
        except OSError:
            return 1 << 30

    async def websocket(self, request, reader, writer):
        key = request.headers.get("sec-websocket-key", "")
        if not self.same_origin(request) or not key or request.headers.get("sec-websocket-version") != "13":
            writer.write(encode_response(Response("bad websocket request", 403), keep_alive=False))
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await writer.drain()
        ws = WebSocket(reader, writer)

        async def heartbeat():
            while not ws.closed:
                await asyncio.sleep(25)
                try:
                    await ws.ping()
                except (ConnectionError, OSError):
                    return

        beat = asyncio.ensure_future(heartbeat())
        try:
            await self.chat(ws, self.is_admin(request))
        finally:
            beat.cancel()
            ws.abort()

    async def chat(self, ws, admin):
        hub = self.hub
        rid = None
        creator = False
        bucket, last = 20.0, time.monotonic()  # 发消息限速：每秒补 2 条，最多攒 20 条

        async def send(payload):
            await ws.send_str(json.dumps(payload))

        try:
            while True:
                text = await ws.recv()
                if text is None:
                    break
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("t")

                if rid is None:
                    if kind != "join" or not isinstance(data.get("room"), str) or not ROOM_ID.match(data["room"]):
                        await send({"t": "error", "error": "房间号不对"})
                        break
                    row = hub.room(data["room"])
                    token = None
                    if row is None and not data.get("create"):
                        # 断线重连时房间已经没了：别悄悄建一个新的
                        await send({"t": "gone"})
                        break
                    if row is None:
                        if not hub.create_open and not admin:
                            await send({"t": "error", "error": "管理员暂时关闭了新建房间，现在只能用已有房间的密钥进入"})
                            break
                        if hub.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] >= MAX_ROOMS:
                            await send({"t": "error", "error": "服务器上的房间太多了，请稍后再试"})
                            break
                        token = hub.create(data["room"])
                        creator = True
                        row = hub.room(data["room"])
                    else:
                        given = data.get("token")
                        creator = isinstance(given, str) and hmac.compare_digest(token_hash(given), row[1])
                        # 上限含创建者：创建者不在也给他留一个位置，所以其他人最多 上限-1 个
                        if not creator and not admin and hub.members(row[0]) >= row[5] - 1:
                            await send({"t": "full", "limit": row[5]})
                            break
                        hub.touch(row[0])
                    rid = row[0]
                    hub.kind[ws] = "creator" if creator else "admin" if admin else "member"
                    hub.online.setdefault(rid, set()).add(ws)
                    await send({
                        "t": "joined", "created": token is not None, "token": token,
                        "creator": creator, "admin": admin, **hub.timer_info(row),
                        "online": len(hub.online[rid]), "limit": row[5],
                        "history": hub.history(rid), "now": time.time(),
                    })
                    await hub.presence(rid)
                    continue

                if not hub.room(rid):
                    break  # 房间刚被销毁

                if kind == "msg":
                    now = time.monotonic()
                    bucket = min(20.0, bucket + (now - last) * 2)
                    last = now
                    body = data.get("data")
                    if not isinstance(body, str) or not body or len(body) > MAX_CIPHER:
                        await send({"t": "error", "error": "消息太长了"})
                        continue
                    if bucket < 1:
                        await send({"t": "error", "error": "发得太快了，歇一下"})
                        continue
                    fid = data.get("file")
                    if fid is not None and not (isinstance(fid, str) and FILE_ID.match(fid) and hub.db.execute(
                            "SELECT 1 FROM files WHERE id=? AND room=? AND done=1", (fid, rid)).fetchone()):
                        await send({"t": "error", "error": "文件不存在或还没传完"})
                        continue
                    bucket -= 1
                    item = hub.add_message(rid, body, fid)
                    await hub.broadcast(rid, {"t": "msg", **item})
                elif kind == "typing":
                    await hub.broadcast(rid, {"t": "typing"}, skip=ws)
                elif kind == "timer":
                    if not (creator or admin):
                        await send({"t": "error", "error": "只有创建者和管理员可以销毁房间"})
                        continue
                    hours = parse_hours(data.get("hours"))
                    if hours is False:
                        await send({"t": "error", "error": "倒计时只能是 1～12 小时"})
                        continue
                    await hub.set_timer(rid, hours, "admin" if admin and not creator else "creator")
                elif kind == "limit":
                    if not (creator or admin):
                        await send({"t": "error", "error": "只有创建者和管理员可以改人数上限"})
                        continue
                    n = parse_limit(data.get("value"))
                    if n is None:
                        await send({"t": "error", "error": f"人数上限只能是 {MIN_PEOPLE}～{MAX_PEOPLE} 人"})
                        continue
                    await hub.set_limit(rid, n, "admin" if admin and not creator else "creator")
        except (ConnectionError, OSError):
            pass
        finally:
            hub.kind.pop(ws, None)
            if rid and ws in hub.online.get(rid, ()):
                hub.online[rid].discard(ws)
                if not hub.online[rid]:
                    hub.online.pop(rid, None)
                if hub.room(rid):
                    hub.touch(rid)
                await hub.presence(rid)


def parse_hours(value):
    """None 取消；0 立即；1～12 倒计时。其他值返回 False。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if value == 0 or (1 <= value <= MAX_HOURS and float(value).is_integer()):
        return int(value)
    return False


def parse_limit(value):
    if isinstance(value, int) and not isinstance(value, bool) and MIN_PEOPLE <= value <= MAX_PEOPLE:
        return value
    return None


def tls_context():
    if not (CERT_FILE and KEY_FILE):
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    return ctx


async def main():
    app = App()
    ctx = tls_context()
    loop = asyncio.get_event_loop()

    def quiet(loop, context):
        # 扫描器、证书不被信任的浏览器会让 TLS 握手失败，这些不值得写进日志
        exc = context.get("exception")
        if isinstance(exc, (ssl.SSLError, ConnectionError, OSError)):
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(quiet)
    server = await asyncio.start_server(app.handle, HOST, PORT, limit=MAX_BODY + 1024, ssl=ctx)
    log.info("ds-chat 启动：%s://%s:%d", "https" if ctx else "http",
             "localhost" if HOST in ("0.0.0.0", "::") else HOST, PORT)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    async def sweeper():
        rounds = 0
        while True:
            try:
                await app.hub.sweep()
                rounds += 1
                if rounds >= 120:  # 每 10 分钟：清理文件垃圾、WAL 临时文件
                    app.hub.sweep_files()
                    app.hub.compact()
                    rounds = 0
                elif app.hub.dirty:  # 删过东西就把空间还给硬盘
                    app.hub.compact()
            except Exception:
                log.exception("清理房间出错")
            await asyncio.sleep(5)

    sweep = asyncio.ensure_future(sweeper())
    await stop.wait()
    sweep.cancel()
    server.close()
    app.hub.db.close()
    log.info("ds-chat 已停止")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # 自己建事件循环：Python 3.6 没有 asyncio.run，3.14 又不再自动创建循环，这样写新老版本都能跑
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
