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
MIN_PEOPLE, MAX_PEOPLE = 2, 7  # 房间人数上限可调的范围，新房间默认 MAX_PEOPLE
HISTORY = 300          # 进入房间时发回最近多少条
KEEP = 1000            # 每个房间最多保存多少条
MAX_CIPHER = 16_000    # 一条密文最长多少字符（约 1 万字明文）
MAX_ROOMS = 20_000
MAX_BODY = 64 * 1024   # HTTP 请求体和 WebSocket 单条消息的上限
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
    db.execute("PRAGMA journal_mode=WAL")
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
    """)
    if "max_people" not in [c[1] for c in db.execute("PRAGMA table_info(rooms)")]:
        db.execute(f"ALTER TABLE rooms ADD COLUMN max_people INTEGER NOT NULL DEFAULT {MAX_PEOPLE}")
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
    def __init__(self, method, path, headers, body, remote, tls=False):
        self.tls = tls
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
        "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'"
    ),
}


def encode_response(resp, head_only=False, keep_alive=True):
    phrase = http.HTTPStatus(resp.status).phrase
    lines = [f"HTTP/1.1 {resp.status} {phrase}"]
    headers = dict(SECURITY_HEADERS)
    headers.update(resp.headers)
    headers["Content-Length"] = str(len(resp.body))
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

    def add_message(self, rid, data):
        now = time.time()
        cur = self.db.execute("INSERT INTO messages (room, ts, data) VALUES (?,?,?)", (rid, now, data))
        self.db.execute(
            "DELETE FROM messages WHERE room=? AND id <= ?", (rid, cur.lastrowid - KEEP)
        )
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
        await self.broadcast(rid, {"t": "timer", "destroyAt": at, "by": by})
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

    async def destroy(self, rid, by):
        self.db.execute("DELETE FROM rooms WHERE id=?", (rid,))
        await self.broadcast(rid, {"t": "destroyed", "by": by})
        for ws in list(self.online.pop(rid, ())):
            await ws.close(4000, b"destroyed")
        log.info("房间 %s 已销毁（%s）", rid[:6], by)

    async def sweep(self):
        """到点的倒计时、太久没人用的房间。"""
        now = time.time()
        due = [r for (r,) in self.db.execute(
            "SELECT id FROM rooms WHERE destroy_at IS NOT NULL AND destroy_at <= ?", (now,))]
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
            SELECT r.id, r.created, r.last_active, r.destroy_at, r.max_people, COUNT(m.id)
            FROM rooms r LEFT JOIN messages m ON m.room = r.id
            GROUP BY r.id ORDER BY r.last_active DESC
        """).fetchall()
        return json_response({"now": time.time(), "rooms": [{
            "id": rid, "code": rid[:6].upper(), "created": created, "lastActive": active,
            "destroyAt": at, "limit": limit, "messages": n, "online": len(hub.online.get(rid, ())),
        } for rid, created, active, at, limit, n in rows]})

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
        length = int(headers.get("content-length", "0") or 0)
        if length > MAX_BODY or "chunked" in headers.get("transfer-encoding", ""):
            writer.write(encode_response(Response("请求太大", 413), keep_alive=False))
            return None
        body = await asyncio.wait_for(reader.readexactly(length), 15) if length else b""
        path = urlsplit(target).path or "/"
        tls = writer.get_extra_info("sslcontext") is not None
        return Request(method.upper(), path, headers, body, remote, tls)

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
                        "creator": creator, "admin": admin, "destroyAt": row[4],
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
                    bucket -= 1
                    item = hub.add_message(rid, body)
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
        while True:
            try:
                await app.hub.sweep()
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
