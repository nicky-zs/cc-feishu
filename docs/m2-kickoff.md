# M2 启动上下文（kickoff）

- **沉淀日期**：2026-10-15（M1 验收完成当日）
- **用途**：M2 预计一周至一个月后启动；本文档保存冷启动所需全部上下文，不需要考古 git log。
- **阅读顺序**：本文档 → [设计文档 §5–§8](specs/2026-10-15-design.md)（设计是权威，本文档是它的执行摘要 + M1 后的最新事实）。

## 1. 一句话目标

`cc-feishu listen` daemon 消费飞书 `im.message.receive_v1` 事件流 → 用回复的
`reply_to`（parent message_id）查路由表 → 把回复文本经 tmux send-keys 注入对应
pane，实现「飞书回复通知 → 驱动本地 Claude Code 会话」的反向通道。

## 2. M1 已交付的地基（M2 依赖点）

| 依赖 | 现状（代码指针） |
|---|---|
| 通知链路 | 已上线：`~/.claude/settings.json` hook 已切 `cc-feishu notify`，旧脚本已删除 |
| 配置键已预留 | `delivery="tmux"` / `resume_prefix="/go"` / `routing_ttl_hours=24` 已在 `config.py` 解析（当前无消费者） |
| session_id 取值口径 | `notify._session_hint`（`src/cc_feishu/notify.py`）：优先 hook stdin `session_id`，回退 transcript 文件名。**路由表存全量 session_id**，from 行只展示前 8 位，两者同源 |
| 原子写文件 | `install._atomic_write`（`src/cc_feishu/install.py`），state.py 写路由表可复用/抽出 |
| from 行 | `from: <from_label 或 hostname> (<会话短 ID8>)`——多机多会话辨识来源（2026-10-15 用户需求） |
| 工程门槛 | mypy strict / ruff / pytest 全绿，零运行时依赖，Python ≥3.11；61 项测试 |
| 安装 | 本机 `pipx install --editable ~/github.com/nicky-zs/cc-feishu`；install 子命令幂等（config + hook patch；**systemd unit 是 M2 要加的**） |

## 3. M2 范围与代码骨架（设计 §3/§4/§5 已定）

**做**：`state.py`（路由表）+ `listen.py`（daemon）+ `deliver/tmux.py` +
`assets/cc-feishu.service`（systemd user unit）+ notify 侧写路由表 + install 装 daemon。
**不做（M3）**：queue / resume 后端、doctor 完善。

### 3.1 路由表（state.py）

- 路径 `~/.local/state/cc-feishu/routing.json`，**发送通知成功时写入**：
  `message_id → { session_id, tmux_pane?, notify_type, cwd, ts }`
- `tmux_pane` 取 hook 进程继承的 `$TMUX_PANE`（Claude Code 跑在 tmux 里才有，缺省则该条只能走 queue/resume——M3）
- `notify_type` 仅信息记录：hook message 含 "permission" 记 `permission`，否则 `input`；交付后端**不**据此分支
- TTL 过期清理（默认 24h = `routing_ttl_hours`），防膨胀；写入原子（临时文件 + rename）
- ⚠️ **M2 必改两处 M1 代码**：
  1. `lark.send_markdown` 现在只返回 `(ok, err)`，需改为同时返回 **message_id**（lark-cli
     messages-send 真实发送的 stdout JSON 结构开工时实测；dry-run 只显示请求体，已初探无返回体）
  2. `notify.notify` 发送成功后写路由表（需把 `$TMUX_PANE` 与 session_id 传进去）

### 3.2 listen（daemon）

- transport：`lark-cli event consume im.message.receive_v1`（NDJSON 流常驻）
- 过滤链（设计 §5）：`sender_type == "bot"` 丢弃（见 §4 实验 2）→ 非本人 `sender_id` 丢弃 →
  `reply_to` 不在路由表丢弃 → 命中则交付
- 权限选项式回复（如 `1`/`2`）与普通文本统一走 tmux 注入（设计已定，不分支）
- daemon 化：systemd user unit（`assets/cc-feishu.service` 模板，install 幂等注册）

### 3.3 事件 schema（2026-10-15 已实测核实，`lark-cli event schema im.message.receive_v1`）

关键字段全部齐备：`reply_to`（直接回复的 parent message_id，**路由 key**）、`root_id`（
线程根）、`sender_id`（ou_ 前缀）与 `sender_type`（user|bot，**自激励过滤直接可用**）、
`message_id`（om_ 前缀，官方建议的幂等 key）、`content`（多数类型已预渲染为可读文本，
无需自己解包 JSON）、`chat_type`（p2p|group）。
前置条件：scope `im:message.p2p_msg:readonly` + 开发者后台启用 `im.message.receive_v1` 事件
（doctor 的检查项，M2 手工核对一次）。

## 4. 开工前必做的两个实验（设计 §5 前置验证项）

结论写回本文档此处，listen 架构据此定。

- [ ] **实验 1：多机广播 vs 分流**
  两台机器同时 `lark-cli event consume im.message.receive_v1 --max-events 5 --timeout 2m`，
  第三处（或手机）给 bot 发消息，观察两机是否都收到：
  - 都收到（广播）→ 各机按 `reply_to` 归属过滤即可，无 relay
  - 只一台收到（分流）→ 需要单点消费再分发（relay），架构加一层
  无第二台机器时可用同机第二个身份近似，但**结论必须真双机复验**
- [ ] **实验 2：自激励回路**
  一边 `event consume` 监听，一边 `cc-feishu notify` 发一条通知，观察是否收到该消息事件：
  - 触发 → listen 按 `sender_type == "bot"` 过滤（schema 已有此字段，无成本）
  - 不触发 → 过滤逻辑仍保留（防御未来变化）
- [ ] **实验 3（新增，§3.1 依赖）：message_id 返回结构**
  `lark-cli im +messages-send --as bot --user-id <uid> --text test`（真实发送）看 stdout
  JSON 的 message_id 字段名，定 `send_markdown` 的返回契约

## 5. M2 验收标准（设计 §8）

1. 飞书引用/回复一条通知消息 → 文本出现在对应 tmux pane 的 Claude Code 输入区并回车提交
2. `reply_to` 未命中路由表 / 非本人事件：安全忽略，daemon 不崩
3. 路由表 TTL 清理生效，条目数有上界
4. 多机（若实验 1 为广播）：A 机通知，B 机收到回复事件但表中无此 message_id → 忽略不误注入
5. M1 回归：56+ 测试全绿，通知链路行为不变

## 6. 开放问题（M2 brainstorming 先过一遍）

- 用户回复形态：只处理 `reply_to` 命中的引用回复？p2p 里非引用的裸文本要不要兜底（比如
  路由表里只有一条活跃会话时）？——建议 M2 保守：只认 reply_to
- `reply_to` vs `root_id`：回复「续 2/3」这类后续片时 parent 是该片 message_id，
  路由表按片全量登记即可命中；root_id 是否需要冗余查一轮（回复了线程根）？
- tmux send-keys 细节：`-l` 字面注入 + Enter 分开发送；多行文本如何处理（逐行 or 单条）；
  pane 已死（会话结束）时的报错路径与飞书侧反馈
- daemon 掉线自愈：systemd `Restart=on-failure`；lark-cli event daemon 自身的保活
- queue/resume 是 M3，但表结构现在就留位：同一 session 并发 resume 拒绝（M3 才用）；
  M2 阶段 listen 遇 `/go` 开头回复：提示「M3 未实现」还是静默忽略？
- 多机共享后的配置差异：from_label 每机设好认的名字（用户 2026-10-15 已确认此用法）

## 7. 冷启动清单（一周/一个月后照做）

1. 读本文档 + 设计文档 §5–§8
2. 跑 §4 三个实验，结论写回本文档
3. `git pull` + `pipx upgrade cc-feishu`（或重装 git+），跑 `pytest` 确认全绿
4. superpowers 流程：brainstorming（过 §6）→ writing-plans → 执行
   （计划放 `docs/superpowers/plans/`，M1 计划可作模板参考）

## 8. 环境事实快照（2026-10-15）

- 主机 hostname `xqheu7a3r6nn5eo`（Linux 6.8，tmux 3.4，Python 3.12.3）
- lark-cli 1.0.85（volta 安装），事件消费/发送能力如 §3/§4 所述
- 通知接收人 uid 在 `~/.config/cc-feishu/config.toml`（install 已从旧脚本迁移）
- 仓库 remote：github.com/nicky-zs/cc-feishu（私有），main 与 origin 同步
