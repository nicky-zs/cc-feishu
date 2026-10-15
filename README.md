# cc-feishu

Claude Code ↔ 飞书桥：等待人工响应时把生成内容分片发送到飞书；在飞书回复
通知消息，反向驱动本地 Claude Code 会话。

- 设计文档：[docs/specs/2026-10-15-design.md](docs/specs/2026-10-15-design.md)
- Python ≥ 3.11，零运行时依赖，mypy strict
- 安装：`pipx install git+<repo>` → `cc-feishu install`

状态：M1 完成（通知链路已迁移并通过逐字节比对验收，旧 notify_feishu.sh 已退役）。
M2（listen + 路由表 + tmux 后端）待启动。
