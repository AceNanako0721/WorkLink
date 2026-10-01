# WorkLink

通过 Teams 自聊与本地 OMP 交互的桥接项目，计划提供 Telegram Bot API 兼容接口。

目前已整理需求和网页操作技能，并建立隐私检查与发布基础；桥接服务尚未实现。本仓库的版本包当前包含文档、技能和维护脚本，不是可运行的桥接服务。

## 交互约定

- `/chat 内容`：提交任务。
- `/chat /原有指令 参数`：保留原有指令，具体支持范围按最终插件核验。
- `/choose 菜单编号 选项编号`：选择回复末尾列出的选项。
- `omp：内容`：服务回复；不会被当作用户输入。
- 其他普通消息忽略。

完整约束见 [需求文档](doc/Teams自聊接入OMP_需求与约束.md)。

## 本地隐私隔离

账号、企业站点、密钥、浏览器登录状态、真实消息、数据库、日志和截图放在仓库外的私有目录。`config.example.json` 只有虚构值；真实配置不得提交。启动配置和私有目录加载方式将在服务实现时确定。

公开文档和技能中的 `SELF_CHAT_DISPLAY_NAME`、`SITE_ID`、`FOLDER_ID`、`VIEW_ID`、`<PROJECT_DIR>` 均为占位值，不可直接当成真实目标。

本地额外的禁传词可通过 `WORKLINK_PRIVACY_DENY_FILE` 指向仓库外的 JSON 字符串数组。默认使用仓库同级 `WorkLink-private/privacy-deny.json`（存在时）。真实禁传值不进入 CI。

Git 忽略规则不是隐私保证。检查在提交、推送和发布前运行；公开 Issue、PR、提交说明和手动上传的附件也应避免真实业务信息。

## 开发与检查

需要 Git、Python 3.11+；创建 Release 的本地入口还需要已登录的 GitHub CLI。

```powershell
python scripts/install-hooks.py
python scripts/check-public.py --history
python -m unittest discover -s tests -v
```

真实账号联调只在本地进行，GitHub Actions 使用虚构测试数据。自动检查只覆盖项目明确的文件规则、隐私模式和本地禁传词，不能识别所有可能的敏感信息。

## 发布

在 `main` 分支且工作区干净时执行：

```powershell
.\scripts\release.ps1 0.1.0
```

脚本检查公开内容和测试，必要时更新唯一版本文件 `VERSION` 并提交，随后推送主分支及版本标签。标签触发 CI 检查和打包，再自动创建 GitHub Release，附 ZIP 包及 SHA-256。检查失败不会创建发布包；已存在版本不可覆盖。发布代码不更新本地私有数据。

本仓库尚未选定开源许可证，暂不附加许可证授权。公开可见不代表已选择 MIT 或其他开源许可。
