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
> 按序执行；每步有明确判据。**步骤 3 需要用户人工参与（飞书后台建应用），到那一步停下来问。**

前置认知：
- cc-feishu 是 CLI + Claude Code Notification hook（不是 skill）；hook 把 Claude 等待人工输入时的
  输出（含 AskUserQuestion 问题与选项）分片发到用户飞书
- 仅支持 Linux（M2 起依赖 systemd user unit 与 tmux）
- lark_uid 语义：**只服务单一用户**——所有通知发给这一个人的飞书，多用户不在 M1–M3 范围

### 1. 安装 CLI（自动）

```bash
pipx install git+https://github.com/nicky-zs/cc-feishu.git
cc-feishu --help        # 期望：输出 {notify,install} 子命令帮助
```

判据：`cc-feishu --help` 退出码 0。pipx 不存在则先装 pipx（`python3 -m pip install --user pipx`
或发行版包管理器）。Python < 3.11 时先升级。

### 2. 集成到 Claude Code（自动）

```bash
cc-feishu install
```

自动且幂等：生成 `~/.config/cc-feishu/config.toml`（存在则不覆盖）、patch
`~/.claude/settings.json` 的 Notification hook（保留既有配置；损坏时备份原件为
`settings.json.broken-<ts>` 再重建）。此时 `lark_uid` 还是空的——步骤 4 填。

判据：命令退出码 0；`grep cc-feishu ~/.claude/settings.json` 命中。

### 3. 配置飞书应用（⛔ 人工，停下问用户）

cc-feishu 通过 lark-cli 调飞书 OpenAPI，lark-cli 需要一个**用户自己的飞书自建应用**：

1. [飞书开放平台](https://open.feishu.cn) → 开发者后台 → 创建企业自建应用
2. 开通权限：`im:message:send_as_bot`（以 bot 身份发消息；界面名可能显示为
   「以应用的身份发消息」）。M2 反向通道需额外 `im:message.p2p_msg:readonly` 与
   事件订阅 `im.message.receive_v1`，M1 阶段可以先不开
3. 发布应用版本（权限变更需发版生效）
4. 把应用的 App ID / App Secret 配置到 lark-cli：
   `lark-cli auth login`（交互式，跟随提示；`lark-cli auth status` 验证，期望 bot 状态 ready）
5. 在飞书客户端给该应用发一条消息（开通会话，否则 bot 无法主动私发用户）

**这五小步是平台侧流程，Agent 无法代办**（需要用户登录开发者后台与飞书客户端）。
向用户说明上面 1–5 并等完成后继续。

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
| 「lark-cli not found」/ 调用失败 | 步骤 3 的 lark-cli 未安装或未 auth |
| lark-cli 成功但用户没收到 | 步骤 3-5 未做（未给应用发过消息开天窗）/ uid 不是本人 / 用户看错会话 |
| 多片消息只见部分 | 个别片失败会记 stderr 不中断，检查频率限制（chunk_bytes 可调小） |
