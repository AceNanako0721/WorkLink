# WorkLink

通过 Teams 自聊与本地 OMP 交互，提供 Telegram Bot API 风格的本机接口。

第一版包含可运行的 Python 本地服务、Edge 扩展和固定版本 OMP Telegram 插件的连接补丁。

```text
Teams 自聊 ⇄ WorkLink Edge 扩展 ⇄ 本地服务 ⇄ OMP Telegram 插件 ⇄ OMP
```

## 交互约定

- `/chat 内容`：提交任务。
- `/chat /原有指令 参数`：保留原有指令，交给插件控制命令、已注册 Telegram 扩展命令及提示模板。未开放的命令明确拒绝，不交给模型模拟执行。
- `/choose 菜单编号 选项编号`：选择回复末尾列出的选项。
- `omp：内容`：服务回复；不会被当作用户输入。
- 其他普通消息忽略。

完整约束见 [需求文档](doc/Teams自聊接入OMP_需求与约束.md)。

## 启动第一版

服务需要 Python 3.11+，无需第三方 Python 包。先执行初始化，在外部配置中填入真实自聊标题，再启动服务：

```powershell
python -X utf8 scripts/setup-local.py
.\scripts\start.ps1
```

在 Edge 中加载 `src/edge-extension`，在扩展设置里填写 `http://127.0.0.1:8765` 和外部 `secrets/adapter-token.txt` 的密钥，刷新 Teams 自聊页并保持可见、停留在最新消息。OMP 插件接入、恢复处理及验证记录见 [第一版运行说明](doc/第一版运行说明.md)。

当前已通过服务与扩展模拟测试，并在真实 Teams 页面确认输入读取和测试回复的发送、编辑、删除。参考插件已通过本地身份/聊天接口及独立指令分发检查。扩展安装后的自动运行和实际 OMP 模型会话尚未完成完整联调。

本版仅支持单个自聊、单个 OMP 消费者；用户输入的修改/删除不自动重提或取消任务。附件、语音、Web App、群聊、跨主机部署及 SharePoint 自动传输未集成。不支持的方法返回明确错误。

## 本地隐私隔离

账号、企业站点、密钥、浏览器登录状态、真实消息、数据库、日志和截图放在仓库外。初始化默认创建同级 `WorkLink-private`，真实配置和两把独立密钥保存在其中；可显式指定其他外部目录。服务只监听 `127.0.0.1`，不记录请求路径或正文。`config.example.json` 只有占位值。

公开文档和技能中的 `SELF_CHAT_DISPLAY_NAME`、`SITE_ID`、`FOLDER_ID`、`VIEW_ID`、`<PROJECT_DIR>` 均为占位值，不可直接当成真实目标。

本地额外的禁传词可通过 `WORKLINK_PRIVACY_DENY_FILE` 指向仓库外的 JSON 字符串数组。默认使用仓库同级 `WorkLink-private/privacy-deny.json`（存在时）。真实禁传值不进入 CI。

Git 忽略规则不是隐私保证。检查在提交、推送和发布前运行；公开 Issue、PR、提交说明和手动上传的附件也应避免真实业务信息。

## 开发与检查

开发检查需要 Git、Python 3.11+、Node.js 22+；OMP 参考插件要求 Node.js 22.19+。创建 Release 的本地入口还需要已登录的 GitHub CLI。

```powershell
python scripts/install-hooks.py
python scripts/check-public.py --history
python -m unittest discover -s tests -v
node --test tests/edge-extension.test.cjs
```

真实账号联调只在本地进行，GitHub Actions 使用虚构测试数据。自动检查只覆盖项目明确的文件规则、隐私模式和本地禁传词，不能识别所有可能的敏感信息。

## 发布

在 `main` 分支且工作区干净时执行：

```powershell
.\scripts\release.ps1 0.1.0
```

脚本检查公开内容和测试，必要时更新唯一版本文件 `VERSION` 并提交，随后推送主分支及版本标签。标签触发 CI 检查和打包，再自动创建 GitHub Release，附 ZIP 包及 SHA-256。检查失败不会创建发布包；已存在版本不可覆盖。发布代码不更新本地私有数据。

本仓库尚未选定开源许可证，暂不附加许可证授权。公开可见不代表已选择 MIT 或其他开源许可。
