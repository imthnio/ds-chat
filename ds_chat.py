"""ds-chat —— 终端里的 AI 聊天，界面仿 Grok 风格。

配置保存在 ~/.config/ds-chat/config.json，第一次运行（或输入 /setup）时会引导填写。
"""
import json
import os
import re
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

VERSION = "1.0.0"
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

console = Console(highlight=False)
plain = PromptSession()  # 菜单、向导用的简单输入


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


def ask_choice(title, rows, default=1):
    """rows: [(名称, 说明)]，返回 1 开始的序号。"""
    table = Table(box=None, padding=(0, 2), show_header=False)
    for i, (name, desc) in enumerate(rows, 1):
        table.add_row(Text(f"{i}", style=f"bold {ACCENT}"), Text(name, style="bold"), Text(desc, style=DIM))
    console.print(Panel(table, title=title, title_align="left", border_style=ACCENT))
    while True:
        ans = plain.prompt(HTML(f"<b>输入序号</b> (直接回车 = {default}): ")).strip()
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
                url = plain.prompt(HTML("<b>接口地址</b> (例如 https://example.com): ")).strip()
            base = normalize_base(url)
        backup = next((u for _, u in PRESET_LINES if u != base), "") if n <= len(PRESET_LINES) else ""

        # 第 2 步：密钥
        console.print()
        hint = "（直接回车 = 沿用原来的密钥）" if old.get("api_key") else ""
        console.print(Text(f"第 2 步 · 粘贴 API 密钥{hint}，输入时不会显示出来", style=f"bold {ACCENT}"))
        key = ""
        while not key:
            key = plain.prompt("🔑 ", is_password=True).strip() or old.get("api_key", "")

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

    # 第 3 步：默认模型
    default = next((i for i, m in enumerate(models, 1) if "pro" in m.lower()), 1)
    if old.get("model") in models:
        default = models.index(old["model"]) + 1
    n = ask_choice("第 3 步 · 选择默认模型", [(m, "") for m in models], default)

    cfg = {"base_url": base, "backup_url": backup, "api_key": key, "model": models[n - 1]}
    save_config(cfg)
    console.print(Panel(Text.assemble(
        ("✓ 设置完成！\n\n", "bold green"),
        ("以后在终端输入 ", ""), ("ai", f"bold {ACCENT}"), (" 就能开始聊天。", ""),
    ), border_style="green", padding=(1, 2)))
    plain.prompt(HTML("<grey>按回车开始…</grey>"))
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
    info.add_row("目录", os.getcwd().replace(os.path.expanduser("~"), "~"))
    tips = Text.assemble(
        ("提示  ", DIM),
        ("/model", ACCENT), (" 切换模型   ", DIM),
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
        ("/line", "切换到备用线路（主线路连不上时用）"),
        ("/clear", "清空对话，重新开始"),
        ("/setup", "重新设置线路、密钥、默认模型"),
        ("/help", "显示这个帮助"),
        ("/exit", "退出（也可以按 Ctrl+D）"),
        ("Esc 然后 回车", "换行（写多行内容）"),
        ("Ctrl+C", "回答时按：停止生成"),
    ]:
        table.add_row(Text(cmd, style=ACCENT), desc)
    console.print(Panel(table, title=f"帮助 · ds-chat {VERSION}", title_align="left", border_style=ACCENT), "\n")


# ───────────────────────── 对话 ─────────────────────────

def stream_chat(base, key, model, messages):
    """逐块产出 ("reasoning"|"content"|"usage", 数据)。"""
    body = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
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


def render(reasoning, answer, started, thinking_done_at):
    parts = []
    if reasoning and not answer:
        tail = reasoning.strip().splitlines()[-1][-80:] if reasoning.strip() else ""
        parts.append(Spinner("dots", text=Text.assemble(
            (f" 思考中 {time.time() - started:.0f}s  ", f"italic {ACCENT}"), (tail, f"italic {DIM}"))))
    elif not answer:
        parts.append(Spinner("dots", text=Text(" 正在连接…", style=f"italic {ACCENT}")))
    if reasoning and answer:
        secs = (thinking_done_at or time.time()) - started
        parts.append(Text(f"✻ 已思考 {secs:.1f} 秒", style=f"italic {DIM}"))
    if answer:
        parts.append(Markdown(answer, code_theme="monokai"))
    return Padding(Group(*parts), (0, 0, 0, 2))


def ask(cfg, history, stats):
    started = time.time()
    raw_content, reasoning_field, usage = "", "", None
    thinking_done_at = None
    with Live(render("", "", started, None), console=console, refresh_per_second=12,
              transient=False, vertical_overflow="visible") as live:
        try:
            for kind, data in stream_chat(cfg["base_url"], cfg["api_key"], cfg["model"], history):
                if kind == "reasoning":
                    reasoning_field += data
                elif kind == "content":
                    raw_content += data
                elif kind == "usage":
                    usage = data
                think_tag, answer = split_think(raw_content)
                reasoning = reasoning_field + think_tag
                if answer and thinking_done_at is None:
                    thinking_done_at = time.time()
                live.update(render(reasoning, answer, started, thinking_done_at))
        except KeyboardInterrupt:
            live.update(render("", split_think(raw_content)[1] + "\n\n*（已中断）*", started, None))
    answer = split_think(raw_content)[1].strip()
    if answer:
        history.append({"role": "assistant", "content": answer})
    if usage:
        stats["tokens"] += usage.get("total_tokens", 0)
    console.print(Text(f"  ⏱ {time.time() - started:.1f}s", style=DIM)
                  + (Text(f"  ·  {usage.get('total_tokens', 0)} tokens", style=DIM) if usage else Text()))
    console.print()


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
        left_accent = f"  ◆ {cfg['model']}"
        left = f"   {len(history) // 2} 轮对话   {stats['tokens']} tokens   /help 查看命令"
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
            if cmd == "/clear":
                history.clear()
                stats["tokens"] = 0
                show_banner(cfg)
                continue
            if cmd in ("/model", "/m"):
                pick_model(cfg)
                continue
            if cmd == "/line":
                switch_line(cfg)
                refresh_balance(cfg, stats)
                continue
            if cmd == "/setup":
                cfg = setup_wizard(cfg)
                history.clear()
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
        history.append({"role": "user", "content": text})
        console.print()
        try:
            ask(cfg, history, stats)
            refresh_balance(cfg, stats)
        except urllib.error.HTTPError as e:
            history.pop()
            detail = e.read().decode("utf-8", "replace")[:300]
            console.print(Panel(f"HTTP {e.code}：{detail}", title="出错了", border_style="red"), "\n")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            history.pop()
            tip = "输入 /line 切换到备用线路再试试。" if cfg.get("backup_url") else "检查网络，或输入 /setup 换线路。"
            console.print(Panel(f"网络连接失败：{e}\n{tip}", title="出错了", border_style="red"), "\n")
    console.print(Text("\n  再见 👋\n", style=ACCENT))


if __name__ == "__main__":
    main()
