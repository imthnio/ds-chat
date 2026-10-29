# ds-chat 密钥聊天室

打开网页，输入一段长密钥就进入房间；对方输入同一段密钥，就进入同一个房间聊天。

- **密钥不出浏览器**：房间号和加密钥匙都在浏览器里由密钥推导出来（PBKDF2 + AES-GCM），服务器只见到房间号和密文，管理员也看不到聊天内容。
- **可以退出再回来**：聊天记录保存在服务器上（加密的），过段时间用同一段密钥进来还在。
- **创建者**：第一个进入房间的人。换浏览器或清了浏览器数据会失去创建者身份。
- **人数上限**：默认最多 7 人（含创建者，创建者的位置一直保留），创建者和管理员可以调成 2～7 人。满了之后新的人进不来，已在房间里的人不受影响。
- **销毁**：创建者和管理员可以立即销毁房间（要输入房间码确认），或者设 1～12 小时倒计时，到点自动销毁。销毁会删掉房间和全部聊天记录，在线的人会被请出。
- **管理后台**：`/admin`，查看全部房间，调人数上限、设倒计时、立即销毁。
- 双方看到的房间码（6 位）一样，就说明在同一个房间。

界面元素来自 [Lucian UX/UI Prompt](https://lucianlabs.dev/uxui-prompt/)（MIT License）：#062 登录卡片、#036 密钥强度、#078 聊天、#018 人数加减、#017 时长滑块、#269 输入名称确认删除、#070 提示条。

## 运行

只用 Python 标准库，**不用装任何依赖**，Python 3.7 及以上就行。64 MB 内存的小机器也能跑（空闲约 15～20 MB，150 个连接时约 28 MB）。

```bash
python3 server.py
```

打开 `http://服务器IP:8080`。第一次启动会生成管理员密码，保存在 `data/admin_password.txt`，日志里会提示位置。

> **必须用 HTTPS 访问**（本机测试用 `localhost` 除外）。浏览器只在 HTTPS 页面里开放加密功能，用 `http://IP` 打开会提示“这个网址不能加密”。下面有 nginx 配置。

### 设置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8080` | 监听端口 |
| `HOST` | `0.0.0.0` | 监听地址；放在 nginx 后面建议设 `127.0.0.1` |
| `ADMIN_PASSWORD` | 自动生成 | 管理员密码 |
| `DATA_DIR` | `./data` | 数据库和密钥文件放哪 |
| `TRUST_PROXY` | 关 | 在 nginx 后面时设 `1`，才认 `X-Forwarded-*` 头 |
| `ROOM_IDLE_DAYS` | `30` | 多少天没人进出的房间自动清掉，`0` = 永不 |
| `MAX_CONN` | `200` | 同时最多多少个连接 |

## 在小鸡上部署（Debian / Ubuntu）

```bash
apt install -y python3 nginx
git clone https://github.com/imthnio/ds-chat /opt/ds-chat
```

systemd 服务 `/etc/systemd/system/ds-chat.service`：

```ini
[Unit]
Description=ds-chat
After=network.target

[Service]
WorkingDirectory=/opt/ds-chat
ExecStart=/usr/bin/python3 /opt/ds-chat/server.py
Environment=HOST=127.0.0.1 PORT=8080 TRUST_PROXY=1 ADMIN_PASSWORD=换成你自己的密码
Restart=always
DynamicUser=yes
StateDirectory=ds-chat
Environment=DATA_DIR=/var/lib/ds-chat

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now ds-chat
```

nginx（证书可以用 `certbot --nginx` 申请）：

```nginx
server {
    listen 443 ssl;
    server_name chat.example.com;
    # ssl_certificate / ssl_certificate_key 由 certbot 填好

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Alpine 用 `apk add python3 nginx`，其他一样。

## 安全说明

- 密钥越长越难猜。至少 16 个字符，建议直接用页面上的“随机生成”。
- 别人拿到同一段密钥就能进同一个房间、看到全部聊天记录，所以密钥要私下告诉对方。
- 服务器能看到：房间号（密钥的哈希）、每条消息的时间和密文长度、在线人数、访问 IP。看不到密钥和消息内容。

## 赞赏支持
如果这个项目帮到了你，欢迎请我喝杯咖啡 ☕  
微信扫一扫下方赞赏码即可：

![赞赏码](./appreciate.png)
