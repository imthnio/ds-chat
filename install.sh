#!/bin/bash
# ds-chat 一键安装脚本（Debian / Ubuntu / CentOS / Alpine）
#
#   安装或更新：curl -fsSL https://raw.githubusercontent.com/imthnio/ds-chat/main/install.sh | bash
#   重新设置：  curl -fsSL https://raw.githubusercontent.com/imthnio/ds-chat/main/install.sh | bash -s reconfig
#   卸载：      curl -fsSL https://raw.githubusercontent.com/imthnio/ds-chat/main/install.sh | bash -s uninstall
#
# 第一次运行会问：域名（可以不填）、端口、管理员密码。
# 有域名就自动申请免费证书；没有就生成自签名证书，用 IP 访问。
# 再运行一次 = 更新到最新版，设置和聊天记录都保留。
set -e

# 全部写在 main 里、最后一行才调用：curl | bash 下载到一半断网时，不会执行半截脚本
main() {

REPO="imthnio/ds-chat"
APP_DIR=/opt/ds-chat
CONF_DIR=/etc/ds-chat
CONF=$CONF_DIR/env
DATA_DIR=/var/lib/ds-chat
SVC=ds-chat
FILES="server.py static/index.html static/admin.html static/app.js static/admin.js static/ui.js static/style.css"

blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[1;33m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
die()   { red "$*"; exit 1; }

# curl | bash 时标准输入是管道，问问题要从终端读
ask() {
    local ans=""
    if (exec < /dev/tty) 2>/dev/null; then read -r -p "$1" ans < /dev/tty || true; fi
    echo "${ans:-$2}"
}

[ "$(id -u)" = 0 ] || die "请用 root 运行：先输入 sudo -i 回车，再粘贴安装命令。"

# ---------- 系统类型 ----------
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    INIT=systemd
elif command -v rc-service >/dev/null 2>&1; then
    INIT=openrc
else
    die "不认识这个系统的服务管理方式（需要 systemd 或 OpenRC）。"
fi

svc() {  # svc start|stop|restart|enable|disable
    if [ $INIT = systemd ]; then
        case $1 in enable) systemctl enable -q $SVC ;; disable) systemctl disable -q $SVC 2>/dev/null || true ;; *) systemctl "$1" $SVC ;; esac
    else
        case $1 in enable) rc-update add $SVC default >/dev/null ;; disable) rc-update del $SVC default >/dev/null 2>&1 || true ;; *) rc-service $SVC "$1" ;; esac
    fi
}

svc_logs() {
    if [ $INIT = systemd ]; then journalctl -u $SVC -n 20 --no-pager 2>/dev/null || true
    else tail -n 20 /var/log/ds-chat.log 2>/dev/null || true; fi
}

pkg_install() {
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq >/dev/null 2>&1 || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache -q "$@" >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q "$@" >/dev/null
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q "$@" >/dev/null
    else
        die "找不到软件包管理器（apt / apk / dnf / yum），请先手动安装：$*"
    fi
}

# ---------- 卸载 ----------
if [ "${1:-}" = uninstall ]; then
    blue "==> 卸载 ds-chat"
    svc stop 2>/dev/null || true
    svc disable
    rm -f /etc/systemd/system/$SVC.service /etc/init.d/$SVC
    [ $INIT = systemd ] && systemctl daemon-reload || true
    rm -rf "$APP_DIR"
    if [ -d "$DATA_DIR" ]; then
        if [ "$(ask '同时删除全部房间和聊天记录吗？[y/N] ' n)" = y ]; then
            D=$(sed -n 's/^DOMAIN=//p' "$CONF" 2>/dev/null || true)
            [ -n "$D" ] && [ -x ~/.acme.sh/acme.sh ] && ~/.acme.sh/acme.sh --remove -d "$D" --ecc >/dev/null 2>&1 || true
            rm -rf "$DATA_DIR" "$CONF_DIR"
            echo "    聊天记录已删除"
        else
            echo "    聊天记录保留在 $DATA_DIR，设置保留在 $CONF_DIR，以后重新安装会接着用"
        fi
    fi
    green "==> 已卸载"
    exit 0
fi

OLD=""; [ -f "$CONF" ] && OLD=1
if [ -n "$OLD" ]; then blue "==> 检测到已安装 ds-chat，开始更新"; else blue "==> 安装 ds-chat"; fi

# ---------- 1. Python 等 ----------
need=""
command -v python3 >/dev/null 2>&1 || need="$need python3"
command -v curl    >/dev/null 2>&1 || need="$need curl"
command -v openssl >/dev/null 2>&1 || need="$need openssl"
command -v tar     >/dev/null 2>&1 || need="$need tar"
if [ -n "$need" ]; then
    echo "    安装：$need"
    pkg_install $need ca-certificates
fi
python3 -c 'import sys, sqlite3, ssl, asyncio; sys.exit(sys.version_info < (3, 6))' 2>/dev/null \
    || die "Python 版本太旧或缺少 sqlite3 / ssl 模块，需要 Python 3.6 以上。当前：$(python3 --version 2>&1)"
PY=$(command -v python3)
echo "    $(python3 --version 2>&1)"

# ---------- 2. 程序文件：先下载到临时目录检查，没问题再替换 ----------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
SRC=""  # 在仓库目录里直接运行 bash install.sh 时用本地文件；curl | bash 时没有脚本路径，总是下载
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); fi
if [ -n "$SRC" ] && [ "$SRC" != "$APP_DIR" ] && [ -f "$SRC/server.py" ] && [ -f "$SRC/static/app.js" ]; then
    echo "    使用本地文件：$SRC"
    mkdir -p "$TMP/src/static"
    for f in $FILES; do cp "$SRC/$f" "$TMP/src/$f"; done
else
    echo "    从 GitHub 下载最新版"
    if curl -fsSL --retry 2 -m 60 "https://github.com/$REPO/archive/refs/heads/main.tar.gz" -o "$TMP/src.tgz" \
        && mkdir -p "$TMP/x" && tar -xzf "$TMP/src.tgz" -C "$TMP/x"; then
        mv "$TMP/x"/*/ "$TMP/src"
    else
        # 有的机器连不上 github.com 但连得上 raw.githubusercontent.com
        mkdir -p "$TMP/src/static"
        for f in $FILES; do
            curl -fsSL --retry 2 -m 60 "https://raw.githubusercontent.com/$REPO/main/$f?t=$(date +%s)" -o "$TMP/src/$f" \
                || die "下载失败：连不上 GitHub。检查网络后再试。"
        done
    fi
fi
for f in $FILES; do [ -s "$TMP/src/$f" ] || die "下载的文件不完整（缺 $f），已取消，原来的版本没有改动。"; done
python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$TMP/src/server.py" 2>/dev/null \
    || die "下载的文件不完整，已取消，原来的版本没有改动。"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/static"
mkdir -p "$APP_DIR/static"
for f in $FILES; do cp "$TMP/src/$f" "$APP_DIR/$f"; done
chmod -R a+rX "$APP_DIR"
echo "    程序位置：$APP_DIR"

# ---------- 3. 设置（第一次安装，或 reconfig） ----------
# 低于 229 的 systemd 不支持给普通用户开 443 端口的权限，只能用 root 跑
RUN_USER=root
if [ $INIT = systemd ]; then
    SDV=$(systemctl --version 2>/dev/null | head -n1 | awk '{print $2}')
    if [ "${SDV:-0}" -ge 229 ] 2>/dev/null; then
        RUN_USER=dschat
        id dschat >/dev/null 2>&1 || useradd -r -M -d "$DATA_DIR" -s /usr/sbin/nologin dschat 2>/dev/null \
            || useradd -r -M -d "$DATA_DIR" -s /sbin/nologin dschat 2>/dev/null || RUN_USER=root
    fi
fi
mkdir -p "$DATA_DIR" "$CONF_DIR"
chmod 700 "$CONF_DIR"
[ $RUN_USER != root ] && chown -R $RUN_USER "$DATA_DIR" "$CONF_DIR"

if [ -z "$OLD" ] || [ "${1:-}" = reconfig ]; then
    echo
    blue "==> 回答几个问题（直接回车 = 用括号里的默认值）"
    echo "    有域名并且已经解析到这台服务器，就输入域名，会自动申请免费证书。"
    echo "    没有域名直接回车，用 IP 访问（浏览器第一次会提示不安全，点“继续访问”即可）。"
    DOMAIN=$(ask "    域名（可不填）: " "")
    DOMAIN=$(echo "$DOMAIN" | tr -d ' ' | sed 's#^https\?://##; s#/.*##')
    echo
    echo "    NAT 小鸡只能用商家分配的端口，就填分配给你的端口。"
    OLD_PORT=$(sed -n 's/^PORT=//p' "$CONF" 2>/dev/null || true)
    PORT=$(ask "    端口 [${OLD_PORT:-443}]: " "${OLD_PORT:-443}")
    case "$PORT" in ''|*[!0-9]*) die "端口只能是数字" ;; esac
    [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "端口要在 1～65535 之间"
    echo
    OLD_PASS=$(sed -n 's/^ADMIN_PASSWORD=//p' "$CONF" 2>/dev/null || true)
    if [ -n "$OLD_PASS" ]; then
        PASS=$(ask "    管理员密码 [不变]: " "$OLD_PASS")
    else
        PASS=$(ask "    管理员密码 [自动生成]: " "$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-16)")
    fi
    case "$PASS" in *[!A-Za-z0-9_@%+=.,:-]*) die "密码只能用字母、数字和 _@%+=.,:- 这些符号" ;; esac
    [ ${#PASS} -ge 8 ] || die "管理员密码至少 8 位"

    # ---------- 证书 ----------
    CERT_OK=""
    if [ -n "$DOMAIN" ]; then
        blue "==> 为 $DOMAIN 申请免费证书（Let's Encrypt）"
        echo "    需要这台服务器的 80 端口能从外网访问，并且没有被别的程序占用。"
        pkg_install socat >/dev/null 2>&1 || true
        if ! command -v crontab >/dev/null 2>&1; then pkg_install cron >/dev/null 2>&1 || pkg_install cronie >/dev/null 2>&1 || true; fi
        if [ $INIT = openrc ]; then rc-update add crond default >/dev/null 2>&1 || true; rc-service crond start >/dev/null 2>&1 || true; fi
        [ -x ~/.acme.sh/acme.sh ] || curl -fsSL https://get.acme.sh | sh >/dev/null 2>&1 || true
        if [ -x ~/.acme.sh/acme.sh ]; then
            svc stop >/dev/null 2>&1 || true
            if ~/.acme.sh/acme.sh --issue --standalone -d "$DOMAIN" --server letsencrypt --keylength ec-256 >"$TMP/acme.log" 2>&1 \
                || grep -qi "skip" "$TMP/acme.log"; then
                if [ $INIT = systemd ]; then RELOAD="systemctl restart $SVC"; else RELOAD="rc-service $SVC restart"; fi
                [ $RUN_USER != root ] && RELOAD="chown $RUN_USER $CONF_DIR/cert.pem $CONF_DIR/key.pem; $RELOAD"
                ~/.acme.sh/acme.sh --install-cert -d "$DOMAIN" --ecc \
                    --key-file "$CONF_DIR/key.pem" --fullchain-file "$CONF_DIR/cert.pem" \
                    --reloadcmd "$RELOAD || true" >/dev/null 2>&1 || true
                [ -s "$CONF_DIR/cert.pem" ] && CERT_OK=1 && echo "    证书已申请，到期前会自动续期"
            fi
        fi
        if [ -z "$CERT_OK" ]; then
            yellow "    证书申请失败（常见原因：域名还没解析到这台服务器，或 80 端口不通/被占用）。"
            [ -f "$TMP/acme.log" ] && tail -n 5 "$TMP/acme.log" | sed 's/^/      /'
            yellow "    先改用自签名证书，修好后运行 reconfig 重新申请。"
            DOMAIN=""
        fi
    fi
    if [ -z "$CERT_OK" ]; then
        IP=$(curl -4 -fsS -m 8 https://api.ipify.org 2>/dev/null || curl -4 -fsS -m 8 https://ifconfig.me 2>/dev/null || true)
        case "$IP" in *[!0-9.]*|"") IP="" ;; esac
        {
            echo "[req]"; echo "distinguished_name=dn"; echo "x509_extensions=ext"; echo "prompt=no"
            echo "[dn]"; echo "CN=${IP:-ds-chat}"
            echo "[ext]"; echo "basicConstraints=CA:FALSE"
            [ -n "$IP" ] && echo "subjectAltName=IP:$IP"
        } > "$TMP/ssl.cnf"
        openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout "$CONF_DIR/key.pem" -out "$CONF_DIR/cert.pem" \
            -config "$TMP/ssl.cnf" >/dev/null 2>&1 || die "生成证书失败"
        echo "    已生成自签名证书（10 年有效）"
    fi
    chmod 600 "$CONF_DIR/key.pem" "$CONF_DIR/cert.pem"
    [ $RUN_USER != root ] && chown $RUN_USER "$CONF_DIR/key.pem" "$CONF_DIR/cert.pem"

    cat > "$CONF" <<CONF
HOST=0.0.0.0
PORT=$PORT
ADMIN_PASSWORD=$PASS
DATA_DIR=$DATA_DIR
CERT_FILE=$CONF_DIR/cert.pem
KEY_FILE=$CONF_DIR/key.pem
DOMAIN=$DOMAIN
CONF
    chmod 600 "$CONF"
    [ $RUN_USER != root ] && chown $RUN_USER "$CONF"
else
    echo "    已有设置，保留不变（想改域名、端口或密码就运行 reconfig）"
fi

PORT=$(sed -n 's/^PORT=//p' "$CONF")
PASS=$(sed -n 's/^ADMIN_PASSWORD=//p' "$CONF")
DOMAIN=$(sed -n 's/^DOMAIN=//p' "$CONF")

# ---------- 4. 开机自启的服务 ----------
if [ $INIT = systemd ]; then
    CAPS=""; [ $RUN_USER != root ] && CAPS="AmbientCapabilities=CAP_NET_BIND_SERVICE"
    cat > /etc/systemd/system/$SVC.service <<UNIT
[Unit]
Description=ds-chat 密钥聊天室
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
EnvironmentFile=$CONF
ExecStart=$PY $APP_DIR/server.py
$CAPS
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
else
    cat > /etc/init.d/$SVC <<RC
#!/sbin/openrc-run
name="ds-chat"
command="$PY"
command_args="$APP_DIR/server.py"
command_background=true
pidfile="/run/$SVC.pid"
output_log="/var/log/ds-chat.log"
error_log="/var/log/ds-chat.log"
depend() { need net; }
start_pre() { set -a; . $CONF; set +a; }
RC
    chmod +x /etc/init.d/$SVC
fi
svc enable
svc restart >/dev/null 2>&1 || svc start >/dev/null 2>&1 || true

# 防火墙（有在用才处理）
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
fi
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd -q --permanent --add-port="$PORT/tcp" 2>/dev/null && firewall-cmd -q --reload 2>/dev/null || true
fi

# ---------- 5. 检查是否启动成功 ----------
ok=""
for i in 1 2 3 4 5 6 7 8; do
    sleep 1
    if curl -kfsS -m 3 "https://127.0.0.1:$PORT/" -o /dev/null 2>/dev/null; then ok=1; break; fi
done
if [ -z "$ok" ]; then
    red "==> 服务没有启动成功，最近的日志："
    svc_logs
    die "常见原因：端口 $PORT 被别的程序占用了。运行 reconfig 换一个端口。"
fi

if [ -n "$DOMAIN" ]; then HOSTNAME_SHOW=$DOMAIN
else HOSTNAME_SHOW=$(curl -4 -fsS -m 8 https://api.ipify.org 2>/dev/null || echo "服务器IP"); fi
if [ "$PORT" = 443 ]; then URL="https://$HOSTNAME_SHOW"; else URL="https://$HOSTNAME_SHOW:$PORT"; fi

echo
green "==> 完成！"
echo
echo "    聊天地址：  $URL"
echo "    管理后台：  $URL/admin"
echo "    管理员密码：$PASS"
if [ -z "$DOMAIN" ]; then
    echo
    yellow "    用的是自签名证书：浏览器第一次打开会提示“不安全/不是私密连接”，"
    yellow "    点“高级”→“继续访问”即可，消息照样是加密的。"
fi
echo
echo "    更新到最新版：再运行一次安装命令"
echo "    改域名/端口/密码：安装命令末尾加  -s reconfig"
if [ $INIT = systemd ]; then
    echo "    查看状态：systemctl status $SVC     查看日志：journalctl -u $SVC -f"
else
    echo "    查看状态：rc-service $SVC status     查看日志：tail -f /var/log/ds-chat.log"
fi

}

main "$@"
