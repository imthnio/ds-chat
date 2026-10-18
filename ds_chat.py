"""ds-chat —— 终端里的 AI 聊天 + Agent，界面仿 Grok 风格。

Agent 模式下 AI 可以运行命令、读写文件；运行命令和改文件前都会先问用户。

配置保存在 ~/.config/ds-chat/config.json，第一次运行（或输入 /setup）时会引导填写。
"""
import asyncio
import json
import os
import re
import selectors
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

from prompt_toolkit import PromptSession
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

VERSION = "1.1.1"
INSTALL_CMD = "curl -fsSL https://raw.githubusercontent.com/imthnio/ds-chat/main/install.sh | bash"
APP_DIR = os.path.expanduser("~/.local/share/ds-chat")
CONFIG_DIR = os.path.expanduser("~/.config/ds-chat")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# 安装向导里可以直接选的线路
PRESET_LINES = [
    ("主线路", "https://ai.clol.site/v1"),
    ("备用线路", "https://ai.bcol.cc/v1"),
]

ACCENT = "#7aa2ff"
DIM = "grey50"

LOGO = r"""
 ██████╗ ███████╗███████╗██████╗ ███████╗███████╗███████╗██╗  ██╗
 ██╔══██╗██╔════╝██╔════╝██╔══██╗██╔════╝██╔════╝██╔════╝██║ ██╔╝
 ██║  ██║█████╗  █████╗  ██████╔╝███████╗█████╗  █████╗  █████╔╝
 ██║  ██║██╔══╝  ██╔══╝  ██╔═══╝ ╚════██║██╔══╝  ██╔══╝  ██╔═██╗
 ██████╔╝███████╗███████╗██║     ███████║███████╗███████╗██║  ██╗
 ╚═════╝ ╚══════╝╚══════╝╚═╝     ╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝
"""

if sys.platform == "darwin":
    # macOS 的 kqueue 不支持 /dev/tty（curl | bash 安装时就是从它读键盘），改用 select
    class _SelectLoopPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

    asyncio.set_event_loop_policy(_SelectLoopPolicy())

console = Console(highlight=False)
_plain = None


def plain_prompt(*args, **kwargs):
    """菜单、向导用的简单输入（用到时才创建，免得 ai --version 之类也去接管终端）。"""
    global _plain
    if _plain is None:
        _plain = PromptSession()
    return _plain.prompt(*args, **kwargs)


# ───────────────────────── 配置 ─────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not cfg.get("base_url") or not cfg.get("api_key"):
        return None
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_FILE)


def normalize_base(url):
    url = url.strip().rstrip("/")
    if not re.match(r"https?://", url):
        url = "https://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def fetch_models(base, key):
    req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    return sorted(m["id"] for m in data.get("data", []) if m.get("id"))


def test_model(base, key, model):
    """用和聊天一样的流式请求试一次，收到第一块数据就算成功。"""
    for _ in stream_chat(base, key, model, [{"role": "user", "content": "hi"}]):
        return


def ask_choice(title, rows, default=1):
    """rows: [(名称, 说明)]，返回 1 开始的序号。"""
    table = Table(box=None, padding=(0, 2), show_header=False)
    for i, (name, desc) in enumerate(rows, 1):
        table.add_row(Text(f"{i}", style=f"bold {ACCENT}"), Text(name, style="bold"), Text(desc, style=DIM))
    console.print(Panel(table, title=title, title_align="left", border_style=ACCENT))
    while True:
        ans = plain_prompt(HTML(f"<b>输入序号</b> (直接回车 = {default}): ")).strip()
        if not ans:
            return default
        if ans.isdigit() and 1 <= int(ans) <= len(rows):
            return int(ans)
        console.print(Text("  请输入列表里的数字", style="red"))


def setup_wizard(old=None):
    console.clear()
    console.print(Panel(Text.assemble(
        ("欢迎使用 ds-chat\n\n", f"bold {ACCENT}"),
        ("接下来需要填 3 样东西：线路、API 密钥、默认模型。\n", ""),
        ("随时按 Ctrl+C 可以退出，之后输入 ", DIM), ("ai --setup", ACCENT), (" 重新设置。", DIM),
    ), border_style=ACCENT, padding=(1, 2)))
    old = old or {}

    while True:
        # 第 1 步：线路
        rows = [(name, url) for name, url in PRESET_LINES] + [("自定义", "填写其他中转站地址")]
        n = ask_choice("第 1 步 · 选择线路", rows)
        if n <= len(PRESET_LINES):
            base = PRESET_LINES[n - 1][1]
        else:
            url = ""
            while not url:
                url = plain_prompt(HTML("<b>接口地址</b> (例如 https://example.com): ")).strip()
            base = normalize_base(url)
        backup = next((u for _, u in PRESET_LINES if u != base), "") if n <= len(PRESET_LINES) else ""

        # 第 2 步：密钥
        console.print()
        hint = "（直接回车 = 沿用原来的密钥）" if old.get("api_key") else ""
        console.print(Text(f"第 2 步 · 粘贴 API 密钥{hint}，输入时不会显示出来", style=f"bold {ACCENT}"))
        key = ""
        while not key:
            key = plain_prompt("🔑 ", is_password=True).strip() or old.get("api_key", "")

        # 测试连接
        try:
            with console.status("正在测试连接…", spinner="dots"):
                models = fetch_models(base, key)
        except urllib.error.HTTPError as e:
            msg = "密钥不对，或者这个密钥没有权限" if e.code in (401, 403) else f"服务器返回错误 HTTP {e.code}"
            console.print(Panel(msg + "\n请重新填写。", title="连接失败", border_style="red"))
            continue
        except Exception as e:
            console.print(Panel(f"连不上 {base}\n{e}\n换一条线路再试试。", title="连接失败", border_style="red"))
            continue
        if not models:
            console.print(Panel("连接成功，但这个密钥没有可用的模型，请检查分组设置。",
                                title="没有模型", border_style="red"))
            continue
        console.print(Text(f"  ✓ 连接成功，找到 {len(models)} 个模型\n", style="green"))
        break

    # 第 3 步：默认模型（选完实际试一次，有的中转站会列出用不了的模型）
    default = next((i for i, m in enumerate(models, 1) if "pro" in m.lower() and "/" not in m), 1)
    if old.get("model") in models:
        default = models.index(old["model"]) + 1
    while True:
        n = ask_choice("第 3 步 · 选择默认模型", [(m, "") for m in models], default)
        model = models[n - 1]
        try:
            with console.status(f"正在测试 {model}…", spinner="dots"):
                test_model(base, key, model)
            break
        except Exception as e:
            console.print(Panel(f"{model} 现在用不了（{e}），请换一个。",
                                title="模型不可用", border_style="red"))
            default = 1 if n != 1 else min(2, len(models))

    cfg = {"base_url": base, "backup_url": backup, "api_key": key, "model": model}
    save_config(cfg)
    console.print(Panel(Text.assemble(
        ("✓ 设置完成！\n\n", "bold green"),
        ("以后在终端输入 ", ""), ("ai", f"bold {ACCENT}"), (" 就能开始聊天。", ""),
    ), border_style="green", padding=(1, 2)))
    plain_prompt(HTML("<grey>按回车开始…</grey>"))
    return cfg


# ───────────────────────── 界面 ─────────────────────────

def gradient_logo():
    lines = LOGO.strip("\n").splitlines()
    start, end = (0x5B, 0x8C, 0xFF), (0xB4, 0x7C, 0xFF)
    out = Text()
    width = max(len(l) for l in lines)
    for line in lines:
        for i, ch in enumerate(line):
            t = i / max(width - 1, 1)
            r, g, b = (int(s + (e - s) * t) for s, e in zip(start, end))
            out.append(ch, style=f"bold #{r:02x}{g:02x}{b:02x}")
        out.append("\n")
    return out


def show_banner(cfg):
    console.clear()
    if console.width >= 68:
        console.print(gradient_logo())
    else:
        console.print(Text("DeepSeek", style=f"bold {ACCENT}"), "\n")
    info = Table.grid(padding=(0, 2))
    info.add_column(style=DIM)
    info.add_column()
    info.add_row("模型", Text(cfg["model"], style=f"bold {ACCENT}"))
    info.add_row("线路", re.sub(r"^https?://|/v1$", "", cfg["base_url"]))
    info.add_row("模式", Text("Agent · 可以运行命令、读写文件（改动前会问你）", style="yellow")
                 if cfg.get("agent", True) else Text("纯聊天", style=DIM))
    info.add_row("目录", os.getcwd().replace(os.path.expanduser("~"), "~"))
    tips = Text.assemble(
        ("提示  ", DIM),
        ("/model", ACCENT), (" 切换模型   ", DIM),
        ("/his", ACCENT), (" 历史会话   ", DIM),
        ("/agent", ACCENT), (" 开关 Agent   ", DIM),
        ("/clear", ACCENT), (" 新对话   ", DIM),
        ("/help", ACCENT), (" 帮助   ", DIM),
        ("/exit", ACCENT), (" 退出", DIM),
    )
    console.print(Panel(Group(info, Text(), tips), border_style=ACCENT,
                        title="[bold]欢迎[/]", title_align="left", padding=(1, 2)))
    console.print()


def show_help():
    table = Table(box=None, padding=(0, 2), show_header=False)
    for cmd, desc in [
        ("/model", "切换模型"),
        ("/his", "历史会话：打开、删除一个、删除全部"),
        ("/agent", "打开 / 关闭 Agent 模式（AI 能否操作电脑）"),
        ("/line", "切换到备用线路（主线路连不上时用）"),
        ("/clear", "开始新对话（当前对话会留在历史里）"),
        ("/setup", "重新设置线路、密钥、默认模型"),
        ("ai -c", "（在终端里运行）接着上次的对话聊"),
        ("ai --update", "（在终端里运行）更新到最新版"),
        ("/help", "显示这个帮助"),
        ("/exit", "退出（也可以按 Ctrl+D）"),
        ("Esc 然后 回车", "换行（写多行内容）"),
        ("Ctrl+C", "回答或操作时按：停止"),
    ]:
        table.add_row(Text(cmd, style=ACCENT), desc)
    console.print(Panel(table, title=f"帮助 · ds-chat {VERSION}", title_align="left", border_style=ACCENT), "\n")


# ───────────────────────── Agent 工具 ─────────────────────────

MAX_STEPS = 30          # 一次提问里最多连续调用多少次工具
MAX_OUTPUT = 12000      # 工具结果最多返回给 AI 多少字符
COMMAND_TIMEOUT = 120   # 命令最长运行秒数


def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


TOOLS = [
    _tool("run_command", "在用户的 Mac 上运行一条 shell 命令（zsh），返回输出。不能运行需要键盘交互的命令。",
          {"command": {"type": "string", "description": "要运行的命令"}}, ["command"]),
    _tool("read_file", "读取一个文本文件的内容。",
          {"path": {"type": "string", "description": "文件路径，可以用 ~ 表示主目录"}}, ["path"]),
    _tool("list_dir", "列出一个文件夹里的文件和子文件夹（含大小、修改时间）。",
          {"path": {"type": "string", "description": "文件夹路径，默认当前目录"}}, []),
    _tool("write_file", "创建或整个覆盖一个文件。",
          {"path": {"type": "string"}, "content": {"type": "string", "description": "完整的文件内容"}},
          ["path", "content"]),
    _tool("edit_file", "把文件里的一段文字替换成新文字（old_text 必须在文件里只出现一次）。",
          {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
          ["path", "old_text", "new_text"]),
]

# 这些工具会改动电脑，执行前要用户同意
NEEDS_APPROVAL = {"run_command", "write_file", "edit_file"}


def system_prompt():
    home = os.path.expanduser("~")
    return (
        "你是运行在用户 Mac 终端里的 AI 助手，可以用工具查看文件、运行命令、写文件，帮用户把事情真正做完。\n"
        f"当前目录：{os.getcwd()}\n用户主目录：{home}\n今天：{time.strftime('%Y-%m-%d')}\n"
        "规则：\n"
        "- 用中文回答。用户是电脑新手，解释要通俗，少用术语。\n"
        "- 需要了解电脑上的情况时，自己用工具去查，不要让用户去运行命令。\n"
        "- 运行命令、写文件前，用户会看到并确认；被拒绝就换个办法或询问用户。\n"
        "- 删除、覆盖重要文件、sudo、修改系统设置这类有风险的操作，先说明风险再做。\n"
        "- 不要运行需要键盘交互或一直不结束的命令（如 vim、top、不带 -c 的 ping）。\n"
        "- 任务完成后，简短告诉用户做了什么、结果如何。"
    )


def _path(p):
    return os.path.abspath(os.path.expanduser(p or "."))


def _clip(text):
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return text[:half] + f"\n\n…（中间省略 {len(text) - MAX_OUTPUT} 个字符）…\n\n" + text[-half:]


def _short(p):
    return p.replace(os.path.expanduser("~"), "~", 1)


def tool_run_command(command):
    import subprocess
    try:
        r = subprocess.run(command, shell=True, executable="/bin/zsh", cwd=os.getcwd(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           errors="replace", timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"命令运行超过 {COMMAND_TIMEOUT} 秒，已停止。", False
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    return f"退出码 {r.returncode}\n{out}".strip(), r.returncode == 0


def tool_read_file(path):
    p = _path(path)
    if os.path.isdir(p):
        return f"{p} 是文件夹，请用 list_dir", False
    with open(p, "rb") as f:
        data = f.read(400_000)
    if b"\0" in data[:4000]:
        return f"{p} 是二进制文件（{os.path.getsize(p)} 字节），无法按文本读取", False
    return data.decode("utf-8", "replace"), True


def tool_list_dir(path=None):
    p = _path(path)
    rows = []
    for name in sorted(os.listdir(p), key=str.lower)[:500]:
        full = os.path.join(p, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        kind = "📁" if os.path.isdir(full) else "  "
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        rows.append(f"{kind} {name}  ({st.st_size} 字节, {when})")
    return f"{p}：\n" + ("\n".join(rows) or "（空文件夹）"), True


def tool_write_file(path, content):
    p = _path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {p}（{len(content.splitlines())} 行）", True


def tool_edit_file(path, old_text, new_text):
    p = _path(path)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    n = text.count(old_text)
    if n != 1:
        return f"old_text 在文件里出现了 {n} 次，必须正好 1 次，请提供更长、唯一的片段", False
    with open(p, "w", encoding="utf-8") as f:
        f.write(text.replace(old_text, new_text, 1))
    return f"已修改 {p}", True


TOOL_FUNCS = {
    "run_command": tool_run_command, "read_file": tool_read_file, "list_dir": tool_list_dir,
    "write_file": tool_write_file, "edit_file": tool_edit_file,
}


def preview_tool(name, args):
    """执行前在屏幕上展示 AI 要做什么。"""
    if name == "run_command":
        console.print(Panel(Text("$ " + args.get("command", ""), style="bold"),
                            title="⚡ 运行命令", title_align="left", border_style="yellow"))
    elif name == "write_file":
        content = args.get("content", "")
        lines = content.splitlines()
        body = "\n".join(lines[:15]) + (f"\n…（共 {len(lines)} 行）" if len(lines) > 15 else "")
        exists = "覆盖" if os.path.exists(_path(args.get("path"))) else "新建"
        console.print(Panel(Text(body, style=DIM),
                            title=f"✎ {exists}文件 {_short(_path(args.get('path')))}",
                            title_align="left", border_style="yellow"))
    elif name == "edit_file":
        diff = Text()
        for l in args.get("old_text", "").splitlines()[:12]:
            diff.append("- " + l + "\n", style="red")
        for l in args.get("new_text", "").splitlines()[:12]:
            diff.append("+ " + l + "\n", style="green")
        console.print(Panel(diff, title=f"✎ 修改文件 {_short(_path(args.get('path')))}",
                            title_align="left", border_style="yellow"))
    elif name == "read_file":
        console.print(Text(f"  📄 读取 {_short(_path(args.get('path')))}", style=DIM))
    elif name == "list_dir":
        console.print(Text(f"  📁 查看 {_short(_path(args.get('path')))}", style=DIM))


def approve(name, state):
    """返回 True 允许 / False 拒绝；Ctrl+C 往外抛，结束这一轮。"""
    if name not in NEEDS_APPROVAL or state.get("auto_approve"):
        return True
    while True:
        ans = plain_prompt(HTML(
            "  <b>允许吗？</b> <ansigreen>y</ansigreen> 允许 · "
            "<ansicyan>a</ansicyan> 本次打开期间都允许 · <ansired>n</ansired> 拒绝 : ")).strip().lower()
        if ans in ("y", "yes", "是", ""):
            return True
        if ans in ("a", "all"):
            state["auto_approve"] = True
            return True
        if ans in ("n", "no", "否"):
            return False
        console.print(Text("  请输入 y（允许）、a（都允许）或 n（拒绝）", style="red"))


class Denied(Exception):
    """用户拒绝了操作：整轮停下，等用户说怎么做。"""


def execute_tool(call, state):
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        return "参数不是合法的 JSON，请重试"
    func = TOOL_FUNCS.get(name)
    if func is None:
        return f"没有叫 {name} 的工具"
    preview_tool(name, args)
    if not approve(name, state):
        console.print(Text("  ✗ 已拒绝，AI 已停下。你可以告诉它换个做法。\n", style="red"))
        raise Denied
    try:
        if name == "run_command":
            with console.status("运行中…", spinner="dots"):
                result, ok = func(**args)
        else:
            result, ok = func(**args)
    except TypeError as e:
        return f"参数不对：{e}"
    except Exception as e:
        result, ok = f"出错：{e}", False
    if name in NEEDS_APPROVAL:
        lines = result.splitlines()
        shown = "\n".join(lines[-12:]) if len(lines) > 12 else result
        if len(lines) > 12:
            shown = f"…（只显示最后 12 行，共 {len(lines)} 行）\n" + shown
        console.print(Padding(Text(shown, style="green" if ok else "red"), (0, 0, 1, 4)))
    elif not ok:
        console.print(Padding(Text(result, style="red"), (0, 0, 0, 4)))
    return _clip(result)


# ───────────────────────── 对话 ─────────────────────────

def stream_chat(base, key, model, messages, tools=None):
    """逐块产出 ("reasoning"|"content"|"tool"|"usage", 数据)。"""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                yield "usage", chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    yield "reasoning", delta["reasoning_content"]
                if delta.get("content"):
                    yield "content", delta["content"]
                for tc in delta.get("tool_calls") or []:
                    yield "tool", tc


def refresh_balance(cfg, stats):
    """后台查询余额，结果写进 stats["balance"]；中转站不支持就一直不显示。"""
    def run():
        req = urllib.request.Request(f"{cfg['base_url']}/usage",
                                     headers={"Authorization": f"Bearer {cfg['api_key']}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                stats["balance"] = float(json.load(resp)["balance"])
        except Exception:
            if stats["balance"] is None:
                stats["balance"] = "n/a"
    threading.Thread(target=run, daemon=True).start()


def split_think(content):
    """有些模型把思考过程放在 <think>…</think> 里，拆出来。"""
    m = re.match(r"\s*<think>(.*?)(</think>|$)(.*)", content, re.S)
    if not m:
        return "", content
    return m.group(1), m.group(3) if m.group(2) else ""


def render(reasoning, answer, started, thinking_done_at, preparing_tool=False):
    parts = []
    if preparing_tool and not answer:
        parts.append(Spinner("dots", text=Text(" 正在准备操作…", style=f"italic {ACCENT}")))
    elif reasoning and not answer:
        tail = reasoning.strip().splitlines()[-1][-80:] if reasoning.strip() else ""
        parts.append(Spinner("dots", text=Text.assemble(
            (f" 思考中 {time.time() - started:.0f}s  ", f"italic {ACCENT}"), (tail, f"italic {DIM}"))))
    elif not answer:
        parts.append(Spinner("dots", text=Text(" 正在连接…", style=f"italic {ACCENT}")))
    if reasoning and (answer or preparing_tool):
        secs = (thinking_done_at or time.time()) - started
        parts.append(Text(f"✻ 已思考 {secs:.1f} 秒", style=f"italic {DIM}"))
    if answer:
        parts.append(Markdown(answer, code_theme="monokai"))
    return Padding(Group(*parts), (0, 0, 0, 2))


def stream_turn(cfg, messages, stats):
    """请求一次模型并实时显示。返回 (回答, 思考内容, 工具调用列表)。Ctrl+C 往外抛。"""
    started = time.time()
    raw_content, reasoning_field = "", ""
    calls = {}  # index -> {"id", "type", "function": {"name", "arguments"}}
    thinking_done_at = None
    tools = TOOLS if cfg.get("agent", True) else None
    with Live(render("", "", started, None), console=console, refresh_per_second=12,
              transient=False, vertical_overflow="visible") as live:
        try:
            for kind, data in stream_chat(cfg["base_url"], cfg["api_key"], cfg["model"], messages, tools):
                if kind == "reasoning":
                    reasoning_field += data
                elif kind == "content":
                    raw_content += data
                elif kind == "usage":
                    stats["tokens"] += data.get("total_tokens", 0)
                elif kind == "tool":
                    c = calls.setdefault(data.get("index", 0),
                                         {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    c["id"] = data.get("id") or c["id"]
                    fn = data.get("function") or {}
                    c["function"]["name"] += fn.get("name") or ""
                    c["function"]["arguments"] += fn.get("arguments") or ""
                think_tag, answer = split_think(raw_content)
                reasoning = reasoning_field + think_tag
                if (answer or calls) and thinking_done_at is None:
                    thinking_done_at = time.time()
                live.update(render(reasoning, answer, started, thinking_done_at, bool(calls)))
        except KeyboardInterrupt:
            live.update(render("", split_think(raw_content)[1] + "\n\n*（已中断）*", started, None))
            raise
        think_tag, answer = split_think(raw_content)
        # 定格：去掉转圈，只留“已思考”和回答
        final = []
        if reasoning_field + think_tag:
            secs = (thinking_done_at or time.time()) - started
            final.append(Text(f"✻ 已思考 {secs:.1f} 秒", style=f"italic {DIM}"))
        if answer:
            final.append(Markdown(answer, code_theme="monokai"))
        live.update(Padding(Group(*final), (0, 0, 0, 2)))
    return answer.strip(), reasoning_field + think_tag, [calls[i] for i in sorted(calls)]


def ask(cfg, history, stats, state):
    started = time.time()
    tokens_before = stats["tokens"]
    try:
        for _ in range(MAX_STEPS):
            messages = [{"role": "system", "content": system_prompt()}] + history
            answer, reasoning, calls = stream_turn(cfg, messages, stats)
            if not calls:
                if answer:
                    history.append({"role": "assistant", "content": answer})
                break
            msg = {"role": "assistant", "content": answer, "tool_calls": calls}
            if reasoning:
                msg["reasoning_content"] = reasoning  # DeepSeek 思考模式调用工具时要求带回
            history.append(msg)
            # 每个工具调用都必须有对应的结果，否则下一次请求会被拒绝
            pending = list(calls)
            try:
                while pending:
                    call = pending.pop(0)
                    result = execute_tool(call, state)
                    history.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            except (KeyboardInterrupt, EOFError, Denied) as e:
                reason = "用户拒绝了这个操作，等待用户进一步指示。" if isinstance(e, Denied) else "用户中断了操作。"
                for c in [call] + pending:
                    if not any(m.get("tool_call_id") == c["id"] for m in history if m["role"] == "tool"):
                        history.append({"role": "tool", "tool_call_id": c["id"], "content": reason})
                if not isinstance(e, Denied):
                    console.print(Text("\n  （已停止）", style=DIM))
                break
        else:
            console.print(Text(f"  已连续操作 {MAX_STEPS} 步，先停一下。可以说“继续”让它接着做。", style=DIM))
    except KeyboardInterrupt:
        pass
    used = stats["tokens"] - tokens_before
    console.print(Text(f"  ⏱ {time.time() - started:.1f}s", style=DIM)
                  + (Text(f"  ·  {used} tokens", style=DIM) if used else Text()))
    console.print()


# ───────────────────────── 会话记录 ─────────────────────────

SESSIONS_DIR = os.path.join(APP_DIR, "sessions")


def _session_path(sid):
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def save_session(sess, history, cfg):
    """每次回答完自动保存；sess["id"] 为空时新建一个会话。"""
    if not any(m["role"] == "user" for m in history):
        return
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    now = time.time()
    if not sess.get("id"):
        sess["id"] = time.strftime("%Y%m%d-%H%M%S") + f"-{int(now * 1000) % 1000:03d}"
        sess["created"] = now
    first = next(m["content"] for m in history if m["role"] == "user")
    data = {
        "id": sess["id"],
        "title": re.sub(r"\s+", " ", first).strip()[:40],
        "created": sess.get("created", now),
        "updated": now,
        "model": cfg["model"],
        "messages": history,
    }
    tmp = _session_path(sess["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _session_path(sess["id"]))


def list_sessions():
    items = []
    if not os.path.isdir(SESSIONS_DIR):
        return items
    for name in os.listdir(SESSIONS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, name), encoding="utf-8") as f:
                items.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda s: s.get("updated", 0), reverse=True)


def delete_session(sid):
    try:
        os.remove(_session_path(sid))
    except FileNotFoundError:
        pass


def _when(ts):
    t = time.localtime(ts)
    if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d"):
        return "今天 " + time.strftime("%H:%M", t)
    if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d", time.localtime(time.time() - 86400)):
        return "昨天 " + time.strftime("%H:%M", t)
    return time.strftime("%m-%d %H:%M", t)


def replay(history):
    """打开旧会话时，把聊天记录重新显示出来（工具调用只显示一行摘要）。"""
    results = {m.get("tool_call_id"): m.get("content", "") for m in history if m["role"] == "tool"}
    for m in history:
        if m["role"] == "user":
            console.print(Panel(Text(m["content"]), border_style=DIM, title="你", title_align="left"))
        elif m["role"] == "assistant":
            if m.get("content"):
                console.print(Padding(Markdown(m["content"], code_theme="monokai"), (0, 0, 0, 2)))
            for call in m.get("tool_calls") or []:
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = call["function"]["name"]
                what = args.get("command") or _short(_path(args.get("path")))
                icon = {"run_command": "⚡", "read_file": "📄", "list_dir": "📁"}.get(name, "✎")
                line = Text(f"  {icon} {what}", style=DIM)
                result = results.get(call.get("id"), "")
                if result.startswith("用户拒绝"):
                    line.append("  ✗ 已拒绝", style="red")
                elif result.startswith("用户中断"):
                    line.append("  （已中断）", style=DIM)
                console.print(line)
            if m.get("content"):
                console.print()


def history_menu(history, sess, cfg, stats):
    """/his：列出会话，可打开、删除一个、删除全部。返回 True 表示换了会话。"""
    while True:
        items = list_sessions()[:30]
        if not items:
            console.print(Text("  还没有保存的会话\n", style=DIM))
            return False
        table = Table(box=None, padding=(0, 2), show_header=False)
        for i, s in enumerate(items, 1):
            rounds = sum(1 for m in s.get("messages", []) if m["role"] == "user")
            mark = " ● 当前" if s["id"] == sess.get("id") else ""
            table.add_row(Text(f"{i}", style=f"bold {ACCENT}"), Text(s.get("title") or "（无标题）", style="bold"),
                          Text(f"{_when(s.get('updated', 0))} · {rounds} 轮{mark}", style=DIM))
        console.print(Panel(table, title="历史会话", title_align="left", border_style=ACCENT,
                            subtitle=f"共 {len(list_sessions())} 个", subtitle_align="right"))
        ans = plain_prompt(HTML(
            "  <b>序号</b> 打开 · <b>d序号</b> 删除（如 d2）· <b>D</b> 删除全部 · 回车 返回 : ")).strip()
        if not ans:
            console.print()
            return False
        if ans.isdigit() and 1 <= int(ans) <= len(items):
            s = items[int(ans) - 1]
            history[:] = s.get("messages", [])
            sess.clear()
            sess.update(id=s["id"], created=s.get("created"))
            stats["tokens"] = 0
            show_banner(cfg)
            console.print(Text(f"  ↺ 已打开会话「{s.get('title')}」，可以接着聊\n", style="green"))
            replay(history)
            return True
        m = re.fullmatch(r"[dD]\s*(\d+)", ans)
        if m and 1 <= int(m.group(1)) <= len(items):
            s = items[int(m.group(1)) - 1]
            if plain_prompt(HTML(f"  确定删除「{s.get('title')}」吗？(y/n) : ")).strip().lower() in ("y", "yes", "是"):
                delete_session(s["id"])
                console.print(Text("  ✓ 已删除\n", style="green"))
                if s["id"] == sess.get("id"):
                    # 删的是正在聊的会话：一起清空，开始新对话
                    history.clear()
                    sess.clear()
                    stats["tokens"] = 0
                    show_banner(cfg)
                    console.print(Text("  ✓ 已删除当前会话，开始新对话\n", style="green"))
                    return True
            continue
        if ans == "D":
            total = len(list_sessions())
            confirm = plain_prompt(HTML(f"  <ansired>确定删除全部 {total} 个会话吗？删除后无法恢复。</ansired>"
                                        "输入 <b>yes</b> 确认 : ")).strip().lower()
            if confirm == "yes":
                for s in list_sessions():
                    delete_session(s["id"])
                history.clear()
                sess.clear()
                stats["tokens"] = 0
                show_banner(cfg)
                console.print(Text(f"  ✓ 已删除全部 {total} 个会话，开始新对话\n", style="green"))
                return True
            console.print(Text("  已取消\n", style=DIM))
            continue
        console.print(Text("  没看懂，请按提示输入", style="red"))


def pick_model(cfg):
    try:
        with console.status("正在获取模型列表…", spinner="dots"):
            models = fetch_models(cfg["base_url"], cfg["api_key"])
    except Exception as e:
        console.print(Panel(f"获取模型列表失败：{e}", title="出错了", border_style="red"), "\n")
        return
    default = models.index(cfg["model"]) + 1 if cfg["model"] in models else 1
    n = ask_choice("选择模型", [(m, "● 当前" if m == cfg["model"] else "") for m in models], default)
    cfg["model"] = models[n - 1]
    save_config(cfg)
    console.print(Text(f"  ✓ 已切换到 {cfg['model']}（下次打开也会用它）", style="green"), "\n")


def switch_line(cfg):
    if not cfg.get("backup_url"):
        console.print(Text("  没有设置备用线路，输入 /setup 可以换线路\n", style=DIM))
        return
    cfg["base_url"], cfg["backup_url"] = cfg["backup_url"], cfg["base_url"]
    save_config(cfg)
    console.print(Text(f"  ✓ 已切换到 {cfg['base_url']}\n", style="green"))


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-v", "--version"):
        print(f"ds-chat {VERSION}")
        return
    if args and args[0] in ("--update", "update"):
        os.execvp("bash", ["bash", "-c", INSTALL_CMD])
    cfg = load_config()
    if cfg is None or (args and args[0] == "--setup"):
        try:
            cfg = setup_wizard(cfg)
        except (KeyboardInterrupt, EOFError):
            console.print(Text("\n  已取消设置\n", style=DIM))
            return
        if args and args[0] == "--setup":
            return

    history, stats = [], {"tokens": 0, "balance": None}
    state = {"auto_approve": False}  # 本次打开期间是否不再询问
    sess = {}  # 当前会话的 id，第一次保存时生成
    refresh_balance(cfg, stats)

    style = Style.from_dict({
        "prompt": f"bold {ACCENT}",
        "box": ACCENT,
        "bottom-toolbar": "noreverse #808080 bg:default",
        "bottom-toolbar.box": f"noreverse {ACCENT} bg:default",
        "bottom-toolbar.accent": f"noreverse {ACCENT} bg:default",
        "bottom-toolbar.balance": "noreverse bold #4ade80 bg:default",
    })
    os.makedirs(APP_DIR, exist_ok=True)
    session = PromptSession(history=FileHistory(os.path.join(APP_DIR, "history")), style=style,
                            cursor=CursorShape.BLINKING_BEAM, reserve_space_for_menu=0,
                            erase_when_done=True, refresh_interval=1)

    def width():
        return shutil.get_terminal_size().columns

    # 输入框：上边框单独打印，左边框在提示符里，右边框用 rprompt，下边框和状态栏放在 toolbar
    def box_left():
        return [("class:box", "│ "), ("class:prompt", "❯ ")]

    def toolbar():
        rounds = sum(1 for m in history if m["role"] == "user")
        left_accent = f"  ◆ {cfg['model']}" + ("  ⚡Agent" if cfg.get("agent", True) else "")
        left = f"   {rounds} 轮对话   {stats['tokens']} tokens   /help 查看命令"
        bal = stats["balance"]
        if bal is None:
            right = "余额 查询中…  "
        elif bal == "n/a":
            right = ""
        else:
            right = f"余额 ${bal:.2f}  "
        # 中文字符占两格，按显示宽度算右对齐的空格
        used = sum(2 if ord(c) > 0x2E7F else 1 for c in left_accent + left + right)
        return [
            ("class:box", "╰" + "─" * (width() - 2) + "╯\n"),
            ("class:accent", left_accent),
            ("", left + " " * max(width() - used, 2)),
            ("class:balance", right),
        ]

    show_banner(cfg)
    if args and args[0] in ("-c", "--continue"):
        last = next(iter(list_sessions()), None)
        if last:
            history[:] = last.get("messages", [])
            sess.update(id=last["id"], created=last.get("created"))
            console.print(Text(f"  ↺ 接着上次的会话「{last.get('title')}」\n", style="green"))
            replay(history)
        else:
            console.print(Text("  还没有保存的会话，开始新对话\n", style=DIM))
    elif args and args[0] in ("-r", "--resume", "--history"):
        history_menu(history, sess, cfg, stats)
    while True:
        console.print("╭" + "─" * (width() - 2) + "╮", style=ACCENT)
        try:
            text = session.prompt(box_left, rprompt=[("class:box", "│")],
                                  bottom_toolbar=toolbar).strip()
        except KeyboardInterrupt:
            text = None
        except EOFError:
            break
        finally:
            # 擦掉上边框（输入框其余部分 prompt_toolkit 自己会擦掉）
            sys.stdout.write("\x1b[1A\x1b[2K")
            sys.stdout.flush()
        if not text:
            continue
        # 输入框提交后被擦掉，把用户的话换成一个灰色框留在记录里
        console.print(Panel(Text(text), border_style=DIM, title="你", title_align="left"))
        cmd = text.lower()
        try:
            if cmd in ("/exit", "/quit", "exit", "quit"):
                break
            if cmd in ("/his", "/history", "/h", "/resume", "/sessions"):
                if history_menu(history, sess, cfg, stats):
                    state["auto_approve"] = False
                continue
            if cmd in ("/clear", "/new"):
                history.clear()
                sess.clear()
                stats["tokens"] = 0
                state["auto_approve"] = False
                show_banner(cfg)
                continue
            if cmd in ("/model", "/m"):
                pick_model(cfg)
                continue
            if cmd == "/agent":
                cfg["agent"] = not cfg.get("agent", True)
                save_config(cfg)
                console.print(Text("  ✓ Agent 模式已打开：AI 可以运行命令、读写文件（改动前会问你）\n"
                                   if cfg["agent"] else "  ✓ 已切换为纯聊天模式\n", style="green"))
                continue
            if cmd == "/line":
                switch_line(cfg)
                refresh_balance(cfg, stats)
                continue
            if cmd == "/setup":
                cfg = setup_wizard(cfg)
                history.clear()
                sess.clear()
                stats.update(tokens=0, balance=None)
                refresh_balance(cfg, stats)
                show_banner(cfg)
                continue
            if cmd == "/help":
                show_help()
                continue
        except KeyboardInterrupt:
            console.print()
            continue
        before = len(history)
        history.append({"role": "user", "content": text})
        console.print()
        try:
            ask(cfg, history, stats, state)
            save_session(sess, history, cfg)
            refresh_balance(cfg, stats)
        except urllib.error.HTTPError as e:
            del history[before:]
            detail = e.read().decode("utf-8", "replace")[:300]
            tip = ""
            if e.code >= 500 or e.code == 404:
                tip = "\n这个模型可能暂时不可用，输入 /model 换一个试试。"
            elif e.code in (401, 403):
                tip = "\n密钥无效或没有权限，输入 /setup 重新填写。"
            elif e.code == 429:
                tip = "\n请求太频繁或余额不足，稍等一下再试。"
            console.print(Panel(f"HTTP {e.code}：{detail}{tip}", title="出错了", border_style="red"), "\n")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            del history[before:]
            tip = "输入 /line 切换到备用线路再试试。" if cfg.get("backup_url") else "检查网络，或输入 /setup 换线路。"
            console.print(Panel(f"网络连接失败：{e}\n{tip}", title="出错了", border_style="red"), "\n")
    console.print(Text("\n  再见 👋\n", style=ACCENT))


if __name__ == "__main__":
    main()
