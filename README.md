# cc-feishu

Claude Code ↔ 飞书桥：等待人工响应时把生成内容分片发送到飞书；在飞书回复
通知消息，反向驱动本地 Claude Code 会话。

- 设计文档：[docs/specs/2026-10-15-design.md](docs/specs/2026-10-15-design.md)
- Python ≥ 3.11，零运行时依赖，mypy strict
- 安装：`pipx install git+<repo>` → `cc-feishu install`

状态：设计已确认，M1（现有通知功能的严格类型迁移）待实现。
