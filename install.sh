#!/bin/bash
# ds-chat 一键安装脚本
#   curl -fsSL https://raw.githubusercontent.com/imthnio/ds-chat/main/install.sh | bash
# 已经装过再运行一次 = 更新到最新版（也可以直接运行 ai --update），已有的设置会保留。
set -e

REPO_RAW="https://raw.githubusercontent.com/imthnio/ds-chat/main"
APP_DIR="$HOME/.local/share/ds-chat"
BIN_DIR="$HOME/.local/bin"
CONFIG_FILE="$HOME/.config/ds-chat/config.json"

blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

ver() { [ -f "$1" ] && sed -n 's/^VERSION = "\(.*\)"/\1/p' "$1" || true; }
OLD_VERSION="$(ver "$APP_DIR/ds_chat.py")"
if [ -n "$OLD_VERSION" ]; then
    blue "==> 检测到已安装 ds-chat ${OLD_VERSION}，开始更新"
else
    blue "==> 安装 ds-chat"
fi

# 1. Python（macOS 自带的 python3 需要先装“命令行开发工具”）
if [ "$(uname)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
    red "需要先安装苹果的“命令行开发工具”（里面带 Python）。"
    echo "马上会弹出安装窗口，点「安装」，装完后重新运行这条安装命令即可。"
    xcode-select --install >/dev/null 2>&1 || true
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    red "没找到 python3，请先安装 Python 3.9 或更新版本。"
    exit 1
fi
echo "    Python: $(python3 --version 2>&1)"

# 2. 程序文件：在仓库目录里运行就用本地文件，否则从 GitHub 下载
mkdir -p "$APP_DIR"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/ds_chat.py" ] && [ -f "$SRC_DIR/requirements.txt" ]; then
    cp "$SRC_DIR/ds_chat.py" "$SRC_DIR/requirements.txt" "$APP_DIR/"
else
    for f in ds_chat.py requirements.txt; do
        curl -fsSL "$REPO_RAW/$f" -o "$APP_DIR/$f"
    done
fi
echo "    程序位置: $APP_DIR"

# 3. 独立的 Python 环境 + 界面库（PyPI 太慢时自动换清华镜像）
blue "==> 安装界面库（第一次可能要一两分钟）"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
PIP="$APP_DIR/venv/bin/pip"
if ! "$PIP" install -q --disable-pip-version-check --timeout 20 -r "$APP_DIR/requirements.txt"; then
    echo "    官方源失败，换用清华镜像重试…"
    "$PIP" install -q --disable-pip-version-check --timeout 30 \
        -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$APP_DIR/requirements.txt"
fi

# 4. 命令 ai
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/ai" <<'LAUNCHER'
#!/bin/sh
exec "$HOME/.local/share/ds-chat/venv/bin/python" "$HOME/.local/share/ds-chat/ds_chat.py" "$@"
LAUNCHER
chmod +x "$BIN_DIR/ai"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
            if [ -f "$rc" ] || [ "$rc" = "$HOME/.zshrc" ]; then
                grep -qs '.local/bin' "$rc" || printf '\n# ds-chat\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
            fi
        done
        NEED_NEW_TERMINAL=1
        ;;
esac

NEW_VERSION="$(ver "$APP_DIR/ds_chat.py")"
if [ -z "$OLD_VERSION" ]; then
    green "==> 安装完成（版本 ${NEW_VERSION}）"
elif [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
    green "==> 已经是最新版 ${NEW_VERSION}"
else
    green "==> 更新完成：${OLD_VERSION} → ${NEW_VERSION}"
fi

# 5. 第一次安装：进入设置向导（填线路、密钥、模型）
if [ ! -f "$CONFIG_FILE" ]; then
    # curl | bash 时标准输入是管道，改从终端读键盘
    if [ -t 0 ]; then
        "$BIN_DIR/ai" --setup
    elif [ -r /dev/tty ]; then
        "$BIN_DIR/ai" --setup < /dev/tty
    else
        echo "运行 ai --setup 完成设置。"
    fi
else
    echo "    已有设置，保留不变（想重新设置就运行 ai --setup）"
fi

if [ -n "$NEED_NEW_TERMINAL" ]; then
    echo
    green "请关掉这个终端窗口，打开一个新的，然后输入  ai  开始使用。"
else
    echo
    green "输入  ai  开始使用。"
fi
