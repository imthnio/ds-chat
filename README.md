# ds-chat

在 macOS 终端里和 AI 聊天，界面仿 Grok 风格：渐变大标题、带边框的输入框、流式输出、Markdown 渲染、思考过程动画，右下角实时显示余额。

支持任何 OpenAI 兼容的接口（默认预设了 DeepSeek 中转站线路）。

## 安装

打开「终端」，粘贴下面这一行，回车：

```bash
curl -fsSL https://raw.githubusercontent.com/imthnio/ds-chat/main/install.sh | bash
```

安装过程中会让你填 3 样东西：

1. **线路**：主线路 / 备用线路 / 自定义地址
2. **API 密钥**：粘贴进去即可（输入时不显示）
3. **默认模型**：从你的密钥能用的模型里选

填完会自动测试连接。然后**打开一个新的终端窗口**，输入 `ai` 就能用。

> 第一次在新 Mac 上运行，可能会弹窗要求安装「命令行开发工具」（里面带 Python），点「安装」，装完后再运行一次上面的命令。

## 使用

```bash
ai            # 开始聊天
ai --setup    # 重新设置线路、密钥、默认模型
ai --update   # 更新到最新版
ai --version  # 查看版本
```

聊天时可用的命令：

| 命令 | 作用 |
|---|---|
| `/model` | 切换模型 |
| `/line` | 切换到备用线路（主线路连不上时用） |
| `/clear` | 清空对话 |
| `/setup` | 重新设置 |
| `/help` | 帮助 |
| `/exit` | 退出（或按 Ctrl+D） |

回答时按 `Ctrl+C` 停止生成；按 `Esc` 再按回车可以换行。

## 更新

已经装过的电脑，再运行一次上面的安装命令就会自动更新，或者直接：

```bash
ai --update
```

线路、密钥、默认模型等设置都会保留。

## 文件位置

| 内容 | 位置 |
|---|---|
| 程序 | `~/.local/share/ds-chat/` |
| 设置（含密钥，仅自己可读） | `~/.config/ds-chat/config.json` |
| 命令 | `~/.local/bin/ai` |

卸载：

```bash
rm -rf ~/.local/share/ds-chat ~/.config/ds-chat ~/.local/bin/ai
```

## License

MIT © imthnio
