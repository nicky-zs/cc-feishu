# cc-feishu

Claude Code ↔ 飞书桥：等待人工响应时把生成内容分片发送到飞书；在飞书回复
通知消息，反向驱动本地 Claude Code 会话。

- 设计文档：[docs/specs/2026-10-15-design.md](docs/specs/2026-10-15-design.md)
- M2 启动上下文：[docs/m2-kickoff.md](docs/m2-kickoff.md)（目标、前置实验、M1 地基、开放问题）
- Python ≥ 3.11，零运行时依赖，mypy strict
- 安装：`pipx install git+<repo>` → `cc-feishu install`

状态：M1 完成（通知链路已迁移并通过逐字节比对验收，旧 notify_feishu.sh 已退役）。
M2（listen + 路由表 + tmux 后端）待启动——一周至一月后，先读 [docs/m2-kickoff.md](docs/m2-kickoff.md)。

## Agent 安装指引

> 本节写给 AI Agent（Claude Code 等）。你的用户要求你在这台机器上安装并配置 cc-feishu。
> 按序执行；每步有明确判据。**步骤 3 由你驱动流程、用户只在手机/浏览器里操作——
> 到那一步把「用户操作」清单原样转述给用户，等用户说完成后继续。**

前置认知：
- cc-feishu 是 CLI + Claude Code Notification hook（不是 skill）；hook 把 Claude 等待人工输入时的
  输出（含 AskUserQuestion 问题与选项）分片发到用户飞书
- 仅支持 Linux（M2 起依赖 systemd user unit 与 tmux）
- lark_uid 语义：**只服务单一用户**——所有通知发给这一个人的飞书，多用户不在 M1–M3 范围

### 1. 安装 CLI（自动）

```bash
pipx install git+https://github.com/nicky-zs/cc-feishu.git
cc-feishu --help        # 期望：输出 {notify,install} 子命令帮助
lark-cli --version || npm install -g @larksuite/cli   # 飞书执行层（npm 源不可达时问用户）
```

判据：`cc-feishu --help` 与 `lark-cli --version` 都退出码 0。pipx 不存在则先装 pipx
（`python3 -m pip install --user pipx` 或发行版包管理器）。Python < 3.11 时先升级。

### 2. 集成到 Claude Code（自动）

```bash
cc-feishu install
```

自动且幂等：生成 `~/.config/cc-feishu/config.toml`（存在则不覆盖）、patch
`~/.claude/settings.json` 的 Notification hook（保留既有配置；损坏时备份原件为
`settings.json.broken-<ts>` 再重建）。此时 `lark_uid` 还是空的——步骤 4 填。

判据：命令退出码 0；`grep cc-feishu ~/.claude/settings.json` 命中。

### 3. 配置飞书应用（Agent 驱动，用户手机/浏览器操作）

cc-feishu 通过 lark-cli 调飞书 OpenAPI，需要**用户自己的飞书自建应用**（bot 身份发
消息 + user 身份查 open_id）。应用配置流程官方就是为 Agent 设计的：你后台发起、
转发链接与二维码、等用户在浏览器完成。

**3a. 应用配置**（后台运行，阻塞至用户完成；从输出提取 URL）：

```bash
lark-cli config init --new          # 创建新应用；已有应用见备注
lark-cli auth qrcode "<url>" --output auth-qr.png   # 二维码必须与链接一起展示给用户
```

**用户操作**（把下面清单原样转述给用户，等用户说完成后继续）：
1. 扫二维码（或浏览器打开链接）登录飞书开放平台，按引导创建企业自建应用
2. 应用后台「权限管理」开通 `im:message:send_as_bot`（界面名「以应用的身份发消息」）；
   M2 反向通道届时再加 `im:message.p2p_msg:readonly` 与事件订阅 `im.message.receive_v1`
3. 「版本管理与发布」创建版本并发布（权限变更需发版生效）
4. 飞书客户端搜索应用名，给它发一条消息（不开天窗 bot 无法主动私发用户）

> 备注：用户已有应用时改用非交互方式：`lark-cli config init --app-id <id> --app-secret-stdin`
> （Secret 从 stdin 传入，避免进进程列表）。

**3b. 用户身份授权**（查本人 open_id 用；同样链接 + 二维码流程）：

```bash
lark-cli auth login --no-wait --json
lark-cli auth qrcode "<url>" --output login-qr.png   # 同样转述给用户、等待完成
```

**3c. 验证**（Agent 执行）：

```bash
lark-cli auth status --json --verify
# 期望：identities.bot.status == ready 且 identities.user.status == ready
```

bot 缺 scope 时 lark-cli 报错通常带 `console_url`——直接把该链接转给用户去后台开通。

### 4. 填 lark_uid（自动）

lark-cli 就绪后，查用户自己的 open_id 并写入配置：

```bash
lark-cli contact +get-user --jq '.data.user.open_id'   # 期望：ou_ 前缀
lark_uid=$(lark-cli contact +get-user --jq '.data.user.open_id')
sed -i "s/^lark_uid = \"\"/lark_uid = \"$lark_uid\"/" ~/.config/cc-feishu/config.toml
```

同时建议给本机设一个好认的 `from_label`（通知头会显示 `from: <label> (<会话短 ID>)`，
多机多会话时用于辨识是哪台机器的 claude 在等待）：

```bash
sed -i "s/^from_label = \"\"/from_label = \"<问用户起个好认的名字>\"/" ~/.config/cc-feishu/config.toml
```

判据：`grep lark_uid ~/.config/cc-feishu/config.toml` 显示非空 ou_ 开头的值。

### 5. 端到端自检（自动，会真实发消息）

```bash
printf '{"message":"cc-feishu 安装自检","cwd":"'"$PWD"'"}' | cc-feishu notify
```

判据：退出码 0、stderr 无「片发送失败」。**让用户确认飞书收到该消息**（应含
`from:` 行）后再宣告完成。未收到则回到步骤 3/4 排查（lark-cli `auth status`、
会话是否已开通、uid 是否本人）。

### 故障速查

| 症状 | 原因 → 处理 |
|---|---|
| notify 后 stderr「配置缺 lark_uid」 | 步骤 4 未完成 |
| 「lark-cli not found」/ 调用失败 | 步骤 1 的 lark-cli 未装，或步骤 3a 的 `config init` 未完成 |
| bot 发送报缺 scope | 把报错里的 `console_url` 转给用户，去开发者后台开通并发版 |
| lark-cli 成功但用户没收到 | 步骤 3 用户操作 4 未做（未给应用发消息开天窗）/ uid 不是本人 / 看错会话 |
| 多片消息只见部分 | 个别片失败会记 stderr 不中断，检查频率限制（chunk_bytes 可调小） |
