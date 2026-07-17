# Torra 追更洗版与 RSS 唤醒实施计划

状态：待实施；必须在 SQLite/RSS 基础计划验收后开始

日期：2026-07-18

依据：

- `docs/superpowers/specs/2026-07-18-sqlite-torra-quality-upgrade-design.md`
- `docs/superpowers/specs/2026-07-18-private-pt-rss-seed-library-design.md`
- `docs/superpowers/plans/2026-07-18-sqlite-private-rss-seed-library-implementation-plan.md`

## 1. 前置条件

开始本计划前必须满足：

- `db/media_control_center.sqlite3` 已成为唯一生产订阅台账。
- 两个 JSON 已完成本地迁移演练和差异报告。
- 私人 RSS 来源、种子、FTS5、抓取记录和匹配表已经实现。
- RSS 测试全部使用夹具，真实收集器保持关闭。
- 现有候选镜像完成重启持久化验收。
- Torra 源码契约仍与设计记录一致。

## 2. 目标与边界

实现：

- 每条订阅独立 24/48 小时追更洗版窗口。
- RSS 新条目匹配订阅和具体集后即时唤醒 Torra。
- RSS 无可靠命中时只在 12/24 或 12/24/48 小时主动兜底。
- Torra 分析、候选下载和 job 终态持久化。
- 重启续查、幂等、冷却、qB 活动阻止和人工暂停。
- MoviePilot 独立开关和人工备用入口，默认关闭。

不实现：

- 修改 Torra 源码或数据库。
- 在媒体控制中心复制 Torra 质量评分器。
- RSS 种子直接推送 qB。
- MoviePilot 自动调度。
- Telegram 网盘能力。
- 实机真实下载。
- 影院大厅修改。

## 3. 阶段 0：重新记录基线

### 任务 1：确认基础计划交付物

执行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
git status --short
```

检查：

- SQLite schema version。
- RSS 来源和种子表存在。
- `MCC_PRIVATE_RSS_ENABLED=false` 时零网络请求。
- JSON 没有运行时写入。
- Git 工作区仅包含本计划文档允许的修改。

## 4. 阶段 1：质量观察和外部动作仓储

### 任务 2：增加追更洗版表和仓储

新增：

- `services/nasemby-core/app/quality_watch_repository.py`
- `services/nasemby-core/tests/test_quality_watch_repository.py`

修改：

- `services/nasemby-core/app/sqlite_runtime.py`

创建：

- `quality_watch_units`
- `provider_actions`
- `scheduler_state`

实现：

1. 观察单元按电影或 `subscription_key + season + episode` 唯一。
2. 保存：
   - `first_success_at`
   - `baseline_ready_at`
   - `window_hours`
   - `next_check_at`
   - `observation_ends_at`
   - `state`
   - `current_offset_index`
3. `provider_actions` 保存幂等键、租约、本地状态和 Torra `external_job_id`。
4. 本地状态固定为 `claimed/submitted/polling/succeeded/failed/cancelled/expired`。
5. 已有 external job ID 的动作只能续查，不能重复提交。
6. 领取到期动作使用短 `BEGIN IMMEDIATE`。
7. 截止点本身允许最后一次动作，终态后再标记 expired。
8. 更好版本下载不重置 `observation_ends_at`。

测试：

- 每集独立窗口。
- 24/48 小时截止计算。
- 乐观并发和动作租约。
- 幂等重复领取。
- 崩溃租约恢复。
- 已有 external job ID 时禁止重新提交。
- 新一集建立新窗口，不改旧集。

### 任务 3：把现有 Torra 推送幂等和冷却迁入 SQLite

修改：

- `services/nasemby-core/app/subscription_compat_runtime.py`
- `services/nasemby-core/tests/test_mcc_compat_runtime.py`

实现：

1. 移除只存在内存中的 v2 Torra 动作幂等和冷却所有权。
2. 使用 `provider_actions` 保存直接推送动作。
3. 保持现有 v1/v2 请求和响应兼容。
4. 服务重启后相同幂等键可以安全回放。
5. Torra 在线查重继续作为最终保护，不替代本地幂等。

建议提交：

```text
feat: persist torra action idempotency in sqlite
```

## 5. 阶段 2：Torra 追更洗版适配器

### 任务 4：扩展 Torra job 和洗版客户端

新增：

- `services/nasemby-core/app/torra_quality_runtime.py`
- `services/nasemby-core/tests/test_torra_quality_runtime.py`

复用：

- `services/nasemby-core/app/torra_read_runtime.py` 的认证、URL 规范化、重登和脱敏模式。

实现：

1. `POST /api/v1/subscriptions/rewash/{subscription_id}`。
2. `POST /api/v1/subscriptions/rewash/{subscription_id}/download`。
3. `GET /api/v1/jobs/{job_id}`。
4. 可选只读 `GET /api/v1/jobs` 用于诊断，不作为单任务权威状态。
5. 分析请求返回 Torra job ID，不把接收任务当作完成。
6. job 状态映射固定为 pending/running/success/failed/cancelled。
7. 分析成功后只读取真实结果字段：
   - `analysis_id`
   - 逐行候选
   - `is_upgrade`
   - `meta_weight_score`
   - `library_meta_weight_score`
8. 每个基准行只选择 `is_upgrade=true`、正分差且最高分候选。
9. 下载请求传入真实 `analysis_id`、`selected_candidates`、`force_push=true`。
10. 任何未知字段或结果结构进入 blocked，不猜测成功。
11. 日志不记录 Token、候选下载 URL或原始异常响应。

测试：

- Token 和账号密码两种认证。
- 401/403 后重登一次。
- 分析 job 五种状态。
- 分析成功但无正分差。
- 多行候选选择最高分。
- 下载 job 五种状态。
- 非法/缺失 analysis_id。
- 网络、超时和敏感异常脱敏。
- 测试 session 不连接真实 Torra。

### 任务 5：建立统一异步动作查询 API

新增：

- `services/nasemby-core/app/automation_action_runtime.py`
- `services/nasemby-core/tests/test_automation_action_runtime.py`

修改：

- `services/nasemby-core/app/main.py`
- `docs/contracts/http-api-contract-v2.json`

实现：

- `GET /api/v2/automation-actions/:actionId`
- 返回本地状态、外部 provider、脱敏 external job ID、创建/更新时间和安全结果摘要。
- 不返回 Torra 原始 payload、URL、Token 或堆栈。
- 未找到返回 404，未认证返回 401。当前产品没有多用户资源所有权模型，不增加虚假的“当前用户动作”403 规则。

## 6. 阶段 3：Emby 基准和观察单元

### 任务 6：从现有任务证据创建追更洗版窗口

新增：

- `services/nasemby-core/app/quality_watch_runtime.py`
- `services/nasemby-core/tests/test_quality_watch_runtime.py`

复用：

- `task_chain_runtime.py`
- `media_read_runtime.py`
- `symedia_read_runtime.py`
- `qbittorrent_runtime.py`

实现：

1. 首次有效 Torra/qB 下载证据创建 `waiting_library_baseline`。
2. 只有 Emby 中出现可供 Torra 比对的当前文件后写 `baseline_ready_at`。
3. 从订阅覆盖或全局默认读取 24/48 小时窗口。
4. 计算固定 `observation_ends_at`。
5. 迁移已有订阅时不补建历史观察单元。
6. 新证据只为新集创建窗口，不重置同集已有窗口。
7. 无可靠集号时进入季级 blocked/人工确认，不猜测自动下载。
8. 当前基准已命中版本最高目标时直接 `target_reached`。

测试：

- 首次下载但 Emby 未入库。
- Emby 入库后建立窗口。
- 同集重复证据不重置。
- 多集并行。
- 24/48 小时配置覆盖。
- 电影单一单元。
- 无集号和身份冲突。

## 7. 阶段 4：RSS 匹配与即时唤醒

### 任务 7：实现 RSS 条目和订阅季集匹配

新增：

- `services/nasemby-core/app/rss_subscription_match_runtime.py`
- `services/nasemby-core/tests/test_rss_subscription_match_runtime.py`

实现：

1. 新增 RSS 条目后只匹配活动 24/48 小时观察单元。
2. 身份优先级：
   - 已知 TMDB/标准媒体映射。
   - 订阅标题和别名。
   - 季号和明确集号。
3. 只有身份、季和集都可靠时创建 `candidate`。
4. 本地版本摘要只用于展示和粗过滤，不产生“更好版本”结论。
5. 同一 `item_id + unit_key` 唯一。
6. 过期窗口只保存种子库条目，不创建匹配。
7. 迁移历史 RSS 条目不反向匹配。

测试：

- 中文、英文、别名和制作组标题。
- 单集、连续集和合集。
- 同名不同年份。
- 电影/电视剧冲突。
- 匹配不可靠不触发。
- 同条目重复不触发。

### 任务 8：RSS 命中后创建一次 Torra 分析动作

修改：

- `services/nasemby-core/app/rss_subscription_match_runtime.py`
- `services/nasemby-core/app/quality_watch_runtime.py`

实现：

1. 匹配进入 `candidate` 后执行完整复查：
   - RSS 和追更总开关。
   - 窗口仍有效。
   - Torra 非 running/mutating。
   - qB 无同媒体活动下载。
   - 无相同幂等动作。
   - 冷却、每小时和每日上限。
2. 通过后创建 provider action 并调用 Torra 分析。
3. 保存 job ID 后匹配状态改为 `triggered`。
4. 分析无正分差改为 `ignored`，继续等待其他 RSS。
5. 下载成功改为 `confirmed`。
6. 上游失败保持可重试但同条目不无限重放。

测试：

- 每个阻塞条件零 Torra 调用。
- 同条目只提交一次。
- 分析失败、无升级、下载失败和成功。
- 服务重启续查相同 job。

## 8. 阶段 5：有限主动兜底调度

### 任务 9：实现默认关闭的追更洗版协调器

新增：

- `services/nasemby-core/app/quality_watch_scheduler.py`
- `services/nasemby-core/tests/test_quality_watch_scheduler.py`

修改：

- `services/nasemby-core/app/main.py`
- `services/nasemby-core/app/gunicorn.conf.py`（仅确认单 worker 约束，不增加 worker）

环境闸门：

- `MCC_TORRA_QUALITY_WATCH_ENABLED=false`

实现：

1. 单 worker 只启动一个调度线程。
2. 实际兜底点从 SQLite 配置读取；24 小时窗口默认 12、24 小时，48 小时窗口默认 12、24、48 小时。
3. 用户可自定义更早的时间点；时间点使用从 `baseline_ready_at` 起算的分钟数保存，最小 30 分钟、严格递增、不得重复或超过 24/48 小时窗口。
4. 截止点允许最后一次检查，完成后关闭窗口。
5. 每轮最多领取 2～3 条；实现配置但默认保守值 2。
6. Torra 分析全局并发固定 1。
7. 每小时和每天上限从 SQLite 配置读取；初始默认每小时 4、每天 30。
8. 同一订阅/单元最小间隔 60 分钟。
9. 0～15 分钟确定性错峰，避免所有窗口同秒到期。
10. RSS 已在当前时间段触发过分析时，兜底点可以记录跳过。
11. 任务失败不阻塞其他观察单元。

测试：

- 闸门关闭零线程/零调用。
- 到期领取、并发 1、批量 2。
- RSS 已触发后的兜底跳过。
- 自定义 30 分钟、2 小时等更早兜底点，以及乱序、重复和越界配置拒绝。
- 小时/每日上限。
- 截止点终态。
- 崩溃租约恢复。
- 多集公平轮询，不让单订阅占满队列。

## 9. 阶段 6：追更洗版 v2 API

### 任务 10：注册设置、状态、人工分析和下载 API

新增：

- `services/nasemby-core/app/subscription_automation_runtime.py`
- `services/nasemby-core/tests/test_subscription_automation_runtime.py`

修改：

- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_source_contract.py`
- `docs/contracts/http-api-contract-v2.json`

实现：

- `GET /api/v2/subscription-automation/settings`
- `PATCH /api/v2/subscription-automation/settings`
- `GET /api/v2/subscriptions/:id/quality-watch`
- `PATCH /api/v2/subscriptions/:id/quality-watch`
- `POST /api/v2/subscriptions/:id/torra-rewash-analyses`
- `POST /api/v2/subscriptions/:id/torra-rewashes`
- `POST /api/v2/rss-matches/:id/torra-rewash-analyses`

规则：

1. 读取返回 200；异步动作返回 202 + Location。
2. `window_hours` 只允许 24/48。
3. 兜底点以分钟数组提交，必须严格递增、最小 30 分钟且不能超过窗口。
4. 手工分析也要求分析闸门、冷却、幂等和服务端复查。
5. 候选下载要求独立下载闸门、`confirm=true` 和幂等键。
6. 409 并发/幂等冲突，422 语义错误，429 限流，502 上游失败，503 闸门关闭。
7. 不用 200 包装错误。
8. 浏览器不能提交任意 Torra subscription ID；服务端从订阅映射读取。
9. 新增 v2 接口统一使用 `{ "code", "error", "request_id" }` 错误包络，不延续 v1 的历史差异。

测试：

- 认证、Origin、状态码和 Location。
- 全局与单条窗口设置。
- 暂停、恢复和立即检查。
- 分析/下载双闸门。
- RSS 匹配动作。
- 原始 Torra 响应和 Token 不泄露。

建议提交：

```text
feat: add torra follow-up rewash coordinator
```

## 10. 阶段 7：MoviePilot 人工备用

### 任务 11：复用 NasEmby MoviePilot 源码建立安全入口

新增：

- `services/nasemby-core/app/moviepilot_backup_runtime.py`
- `services/nasemby-core/tests/test_moviepilot_backup_runtime.py`

复用：

- `services.py` 中的：
  - `moviepilot_status()`
  - `_moviepilot_list_subscribes()`
  - `_moviepilot_find_subscribe()`
  - `_moviepilot_trigger_subscribe_search()`
  - `moviepilot_subscribe()`

实现：

- `POST /api/v2/subscriptions/:id/moviepilot-previews`
- `POST /api/v2/subscriptions/:id/moviepilot-pushes`
- 独立 `MCC_MOVIEPILOT_BACKUP_ENABLED=false` 闸门。
- 只提供人工查重预览和明确确认。
- Torra/qB 活动时拒绝。
- Torra 不可达不能自动切换 MoviePilot。
- 不实现 MoviePilot 调度器。

测试：

- 复用原函数，不新写客户端。
- 闸门关闭零写调用。
- 已有订阅重搜和新订阅创建两条路径。
- 外部错误、Token 和数据库异常脱敏。

## 11. 阶段 8：React 页面

### 任务 12：增加前端类型和动作轮询

新增：

- `src/types/subscriptionAutomation.ts`

修改：

- `src/services/api.ts`
- `src/types/rssSeedLibrary.ts`
- `src/types/subscriptions.ts`

实现：

- 全局设置、单条状态、观察单元、动作和错误类型。
- 动作 202 后轮询 `Location`，页面区分已提交、执行中和终态。
- 不在浏览器保存 Torra/MoviePilot 凭据或 RSS 下载 URL。

### 任务 13：更新订阅设置和我的订阅

修改：

- `src/components/pages/SubscriptionSettingsPage.tsx`
- `src/components/pages/DiscoverPage.tsx` 或拆出的订阅子组件
- `src/styles/discover.css`
- `src/styles/settings.css`

实现：

1. 订阅设置增加：
   - 追更洗版总开关。
   - 默认 24/48 小时窗口。
   - 可自定义的兜底点；默认 12/24 或 12/24/48 小时，也允许设置 30 分钟、2 小时等更早时间。
   - 批量、小时、每日上限。
   - MoviePilot 独立备用开关。
2. 单条订阅增加：
   - 覆盖 24/48 小时。
   - 当前集窗口结束时间。
   - RSS 最近命中。
   - 下一兜底时间。
   - 暂停、恢复和立即检查。
3. 到期只显示“本集观察已结束”，不显示整条订阅关闭。
4. MoviePilot 只在开关开启且满足边界时显示人工入口。

### 任务 14：更新任务中心、控制室和种子库

修改：

- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/ControlRoom.tsx`
- `src/components/pages/RssSeedLibraryPage.tsx`
- `src/styles/tasks.css`
- `src/styles/control-room.css`
- `src/styles/rss-seed-library.css`

实现：

- 任务中心显示“已有可看版本，继续观察”“RSS 发现新版本”“正在让 Torra 检查”“本集窗口已结束”。
- 技术详情折叠显示匹配依据、Torra job 和跳过原因。
- 控制室显示 RSS 收集、追更洗版队列和 MoviePilot 备用状态，不增加 Symedia 推送。
- 种子库匹配记录可进入订阅详情；人工 Torra 分析必须二次确认。
- 不修改影院大厅。

前端验收：

- `npm run typecheck`
- `npm run build`
- 390、1024、1440 宽度。
- 键盘、焦点和确认框。
- 标题不追加实时数字。
- 表层中文业务文案，内部状态放次级信息。

## 12. 阶段 9：全量验收与候选镜像

### 任务 15：自动化回归

执行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
npm audit --audit-level=high
git diff --check
```

必须覆盖：

- SQLite 多线程、租约和重启。
- RSS 即时唤醒和有限兜底。
- Torra job 全状态。
- qB/Torra 活动阻止。
- 24/48 小时窗口不延长。
- MoviePilot 默认关闭。
- 所有测试零真实外部写调用。

### 任务 16：Docker 候选镜像

修改：

- `docker-compose.yml`
- `.env.example`
- `docs/DEPLOYMENT.md`
- `docs/API_CONTRACT.md`
- `docs/PLAN.md`
- `docs/ROADMAP.md`

新增环境闸门示例：

- `MCC_PRIVATE_RSS_ENABLED=false`
- `MCC_TORRA_QUALITY_WATCH_ENABLED=false`
- `MCC_MOVIEPILOT_BACKUP_ENABLED=false`

验收：

1. 只读候选镜像启动。
2. 登录、静态资源和现有只读 API。
3. RSS 收集、Torra 追更洗版和 MoviePilot 外部动作闸门关闭时对应网络动作返回 503；本地只读状态仍可读取。
4. SQLite 和动作状态重启恢复。
5. 运行层无 Node/npm。
6. 不配置真实私人 RSS。
7. 不调用真实 Torra、qB、MoviePilot 或 Emby 写接口。

建议提交：

```text
feat: complete rss-driven torra follow-up rewash
```

## 13. 实机前停点

完成代码和候选镜像后必须停止，不自动进入实机：

1. 用户提供 1 个脱敏或专门测试的私人 RSS。
2. fnOS 只读部署，三项新闸门关闭。
3. 验证 RSS 抓取和种子入库，不触发 Torra。
4. 选择单条测试订阅，人工执行一次 Torra 追更洗版分析。
5. 验证正分差候选和 job 终态。
6. 再单独批准一次候选下载。
7. 观察稳定后才开启该订阅 RSS 自动唤醒。
8. 最后才考虑全局调度。

任何步骤失败都回到只读状态，不自动切换 MoviePilot。
