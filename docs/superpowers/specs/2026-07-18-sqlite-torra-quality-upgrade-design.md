# SQLite 单台账与 Torra 追更洗版设计

状态：设计已确认，待书面复核后进入实施计划

日期：2026-07-18

## 1. 背景

媒体控制中心当前使用以下两个 JSON 文件保存订阅配置和订阅条目：

- `db/discover_subscriptions.json`
- `db/discover_subscription_items.json`

生产 Gunicorn 固定一个 worker、四个线程。现有 NasEmby 业务函数会直接执行 JSON 的读取、修改和覆盖写入，部分带进度的读取也会回写文件。单 worker 并不能避免线程、后台调度和用户请求之间的读改写竞争，也无法为 Torra 追更洗版分析、候选下载、幂等、冷却和崩溃恢复提供事务边界。

另一个实际问题是 Torra 首次命中即把订阅视为成功。PT 站点可能先发布普通版本，数小时或次日再发布 2160P、高码率、HDR 或其他更优版本。Torra 已经提供可用于追更洗版的手动分析和候选下载，但用户目前必须逐条进入 Torra 操作。

MoviePilot 的 NasEmby 适配源码已经保留，但 MoviePilot 自身数据库在用户环境中不够稳定。MoviePilot 不能成为自动主线，也不能用媒体控制中心的 SQLite 掩盖它自身的数据库问题。

本设计统一使用以下术语：

- **追更洗版**：剧集仍在更新期间，由媒体控制中心按计划调用 Torra 的分析与候选下载，补上后续发布的高码率、2160P、HDR 等更优版本。
- **完结洗版**：订阅完结后由 Torra 自身完结洗版机制处理的能力。
- Torra 真实接口路径和源码字段仍使用 `rewash`、`auto_rewash_*`，文档和页面不修改这些技术名称。

## 2. 目标

1. 在进入 fnOS 实机阶段前，用 SQLite 替换 JSON，成为唯一生产订阅台账。
2. 保留 NasEmby 的发现、订阅、日历、季处理和 MoviePilot 业务语义，不重新猜测或重写规则。
3. 自动调用 Torra 原本需要人工触发的追更洗版分析和候选下载能力；不把它与普通订阅重搜、完结洗版混为一谈。
4. 把“首次成功”和“达到目标版本”拆成不同状态。
5. 按订阅、季和集记录追更洗版观察窗口，避免重复搜索和并行下载。
6. 版本高低继续由 Torra 已有版本控制规则判断；媒体控制中心不建立第二套资源评分器。
7. MoviePilot 保留连接检查、查重、创建订阅和重新搜索源码，使用独立开关，默认关闭。
8. 更新总计划、框架、路线图、部署说明和 API 文本，明确区分已完成与待实施。

## 3. 非目标

- 不自行爬取 PT 站点或实现第二套 RSS 搜索器。
- 不由媒体控制中心硬编码“1080P < 2160P < HDR”等通用质量排序。
- 不让 Torra 与 MoviePilot 对同一媒体单元并行下载。
- 不向 Symedia 推送订阅；Symedia 继续只负责 115 后整理和入库。
- 不把 MoviePilot 自动补齐作为本轮默认运行能力。
- 不因引入 SQLite 就开放多 worker 或多副本。
- 不在代码阶段调用真实 Torra、MoviePilot、qB、115、Symedia 或 Emby 写接口。

## 4. 已核对的源码与外部契约

### 4.1 NasEmby MoviePilot 源码

v2 已保留 NasEmby 原项目中的以下实现，相关函数与原项目对应代码一致：

- `moviepilot_status()`
- `_moviepilot_list_subscribes()`
- `_moviepilot_find_subscribe()`
- `_moviepilot_trigger_subscribe_search()`
- `moviepilot_subscribe()`

现有能力包括连接检查、读取订阅、按 TMDB/类型/季查重、通过 Seerr 通知创建订阅、查找订阅 ID 和触发单条订阅搜索。本轮只在这些源码外增加安全入口、SQLite 状态和调度协调，不另写 MoviePilot 客户端。

MoviePilot 官方 Swagger 仍提供：

- `GET /api/v1/subscribe/list`
- `GET /api/v1/subscribe/search/{subscribe_id}`
- `POST /api/v1/subscribe/seerr`
- `PUT /api/v1/subscribe/`

官方文档说明 MoviePilot 原生完结洗版会在发现更高优先级资源后继续下载，但剧集只有完结状态才支持。因此它不能直接解决周播剧次日出现高码版本的追更洗版痛点。

### 4.2 Torra 源码契约

现有 NasEmby Torra 适配器已经包含：

- 查询订阅与在线查重。
- 保存或更新订阅。
- `POST /api/v1/subscriptions/run/{subscription_id}` 重搜。
- `version_control_enabled`、`version_control_entries`、`version_control_weight_by_category`。
- `auto_rewash_enabled`、`auto_rewash_deadline_days` 和 `auto_rewash_*` 状态字段。
- `is_running`、`is_mutating` 和最近结果字段。

本机 Torra 源码、编译前端契约和用户现有权重配置进一步确认：

- `POST /api/v1/subscriptions/rewash/{subscription_id}`：以 Emby 已入库文件为基准搜索并分析洗版候选。
- `POST /api/v1/subscriptions/rewash/{subscription_id}/download`：提交 `analysis_id`、按行选择的 `selected_candidates` 和 `force_push`。
- Torra 原界面的“选中分数更高”只选择 `meta_weight_score > library_meta_weight_score` 的候选。
- 版本控制条目支持本地规则和共享规则。本地规则由有序的 `include_conditions`、`exclude_conditions`、属性、值和 `any/all` 命中方式组成。
- 用户本机已有按电影、国产剧、港台剧、日韩剧、欧美剧、动漫和综艺划分的 Torra 权重/版本控制配置，实施时直接复用这些真实结构，不新造规则格式。

Torra 自带完结洗版主要在基础订阅完结后运行。媒体控制中心需要补齐的是：对仍在更新的周播剧，定时执行 Torra 已有的手动洗版分析，选择明确高于 Emby 当前基准分的候选，再调用 Torra 原候选下载接口；这一过程统一称为“追更洗版”。

## 5. 总体架构

```text
React 订阅设置 / 我的订阅 / 任务中心
  → Flask 同源 v2 API
  → SubscriptionRepository
      → SQLite 唯一台账
  → TorraQualityCoordinator
      → 读取 Torra / qB / Symedia / Emby 证据
      → 领取到期追更洗版动作
      → 调用 Torra 原分析与候选下载接口
  → NasEmby MoviePilot 适配源码
      → 独立开关
      → 默认仅人工备用
```

业务规则仍在 NasEmby 函数中。仓储层负责持久化和事务，协调器只负责状态推进、限频、幂等和调用顺序。

## 6. SQLite 数据设计

生产数据库固定为：

`db/media_control_center.sqlite3`

### 6.1 `schema_meta`

- `schema_version`
- `created_at`
- `updated_at`

### 6.2 `subscription_config`

单行保存 NasEmby 完整订阅配置 JSON，同时为常用开关建立独立列：

- `payload_json`
- `torra_quality_watch_enabled`
- `torra_quality_schedule_json`
- `torra_quality_min_interval_minutes`
- `torra_quality_daily_limit`
- `moviepilot_enabled`
- `updated_at`
- `version`

### 6.3 `subscriptions`

- `subscription_key` 主键
- `media_type`
- `tmdb_id`
- `season_number`
- `title`
- `payload_json`
- `created_at`
- `updated_at`
- `version`

`payload_json` 保存完整 NasEmby 条目，避免迁移时丢失尚未结构化的业务字段。核心身份字段建立唯一索引和查询索引。

### 6.4 `quality_watch_units`

按媒体单元记录追更洗版观察状态。电影为单一单元，剧集优先按季和集建立单元；无法获得集号时退化为季级观察并明确标记。Torra 追更洗版分析以 Emby 文件作为基准，因此首次下载后还必须等待当前版本进入 Emby。

- `unit_key` 主键
- `subscription_key` 外键
- `season_number`
- `episode_number`
- `torra_subscription_id`
- `state`
- `first_success_at`
- `baseline_ready_at`
- `next_check_at`
- `observation_ends_at`
- `attempt_count`
- `current_offset_index`
- `current_evidence_json`
- `last_result_json`
- `target_reached_at`
- `updated_at`
- `version`

状态值固定为：

- `waiting_first_version`
- `waiting_library_baseline`
- `observing_upgrade`
- `search_due`
- `search_running`
- `target_reached`
- `observation_expired`
- `paused`
- `blocked`

### 6.5 `provider_actions`

记录每一次外部动作，不把网络调用包在长事务中：

- `action_id`
- `idempotency_key` 唯一
- `subscription_key`
- `unit_key`
- `provider`
- `action_type`
- `status`
- `lease_until`
- `external_job_id`
- `request_summary_json`
- `response_summary_json`
- `error_code`
- `error_message`
- `created_at`
- `completed_at`

媒体控制中心本地动作状态固定为：

- `claimed`：已经从调度队列领取，尚未调用外部服务。
- `submitted`：Torra 已返回 job ID，但外部任务尚未进入终态。
- `polling`：正在轮询已有 Torra job。
- `succeeded`、`failed`、`cancelled`：与 Torra job 终态对应。
- `expired`：本地等待超过规定期限；如果已有 `external_job_id`，后续只能续查该 job，不能重新提交相同外部动作。

### 6.6 `scheduler_state` 与 `migration_runs`

分别保存调度游标、最后轮询时间，以及 JSON 迁移批次、备份路径、校验结果和差异报告。

## 7. SQLite 运行规则

- Python 标准库 `sqlite3`，不新增数据库服务。
- 每个请求或后台任务使用独立短连接。
- 启用 WAL、外键、`busy_timeout` 和 `synchronous=NORMAL`。
- 读改写使用显式事务和乐观版本字段。
- 领取调度动作使用短 `BEGIN IMMEDIATE` 事务。
- 外部 HTTP 调用前先提交“执行中”动作，完成后另开事务写结果。
- Torra 追更洗版分析和候选下载返回后台 job；动作保存 job ID，并通过 `GET /api/v1/jobs/{job_id}` 轮询 `pending/running/success/failed/cancelled`。
- 服务重启后，凡是已经保存 `external_job_id` 且尚无终态的动作，只恢复轮询，不再次调用分析或下载提交接口。
- 崩溃遗留的执行租约到期后可恢复，不会永久卡在运行中。
- Gunicorn 继续固定一个 worker、四个线程。

## 8. JSON 到 SQLite 的一次性迁移

迁移顺序固定：

1. 停止订阅调度和全部外部写动作。
2. 复制两个 JSON 文件到带时间戳的备份目录。
3. 校验 JSON 顶层结构、订阅 key、TMDB 身份、季号和配置类型。
4. 在临时 SQLite 文件中创建 schema 并导入。
5. 对比配置、条目数量、每条订阅 key、核心身份字段和完整 JSON 规范化结果。
6. 生成机器可读与中文差异报告。
7. 校验无阻塞差异后设置 `migration_runs.status=completed` 并原子移动为正式数据库。
8. 启动应用后只读 SQLite，不再写 JSON。

迁移失败时不生成空台账、不回退双写，也不静默继续。应用健康状态明确显示迁移失败。回滚方式是停止新镜像、恢复备份 JSON 并运行迁移前镜像。

## 9. Torra 追更洗版流程

### 9.1 首次成功不等于完成

Torra 或 qB 出现首次有效下载证据后，媒体单元先进入 `waiting_library_baseline`。当前版本经 115、Symedia 进入 Emby，并能被 Torra 追更洗版分析读取后，记录 `baseline_ready_at` 并进入 `observing_upgrade`。任务中心显示“已有可看版本，继续观察更好版本”，而不是“已完成”。

### 9.2 默认观察计划

默认相对 Emby 基准版本就绪时间执行：

`2 / 6 / 12 / 24 / 48 / 72 小时`

这组时间不是硬编码常量，必须通过订阅设置修改。全局设置包括：

- 检查时间点列表。
- 最长观察时间。
- 最小搜索间隔，默认 60 分钟。
- 每日最大追更洗版次数。
- 单批最大处理数量。

单条订阅可以覆盖全局计划。

### 9.3 每次检查前的复查

以下任一条件成立时，本次动作跳过并记录原因：

- Torra 追更洗版开关关闭。
- 当前时间未到计划点或观察窗口已结束。
- 相同幂等动作已完成或仍在执行。
- Torra 订阅正在搜索或修改。
- qB 中存在同一媒体单元的活动下载。
- Emby 中尚无可用于 Torra 洗版比对的当前文件。
- Torra 订阅既未启用自身版本控制，也无法按分类命中已保存的元数据权重版本规则。
- Torra 已报告版本控制目标达到。
- 当日次数或最小间隔限制已触发。
- 无法可靠关联订阅、季或集。

### 9.4 调用 Torra

每次到期检查分三个阶段：

1. 调用 Torra `POST /api/v1/subscriptions/rewash/{subscription_id}`，Torra 返回分析 job。媒体控制中心保存 job ID，并轮询 `GET /api/v1/jobs/{job_id}`，直到成功、失败、取消或超时。
2. 分析 job 成功后，从 job result 取得 `analysis_id` 和逐集候选。对每个基准行只选择 Torra 已标记为升级，且 `meta_weight_score > library_meta_weight_score` 的最高分候选。
3. 存在选择时，调用 `POST /api/v1/subscriptions/rewash/{subscription_id}/download`，传入 `analysis_id`、`selected_candidates` 和 `force_push=true`。该接口同样返回后台 job，必须保存并轮询终态。

这与 Torra 原界面的“选中分数更高”语义一致。媒体控制中心不解析 PT 列表、不自行计算质量分，也不修改 Torra 已有版本优先级。没有正分差候选时只记录“暂时没有更好版本”，继续等待下一个自定义时间点。Torra 仅接受任务不等于分析或下载完成，页面和日志必须区分“已提交”“执行中”和终态。

### 9.5 结束条件

- Torra 明确报告本次追更洗版完成，或当前版本已命中版本控制最高目标：`target_reached`。
- 观察时间到期：`observation_expired`，保留人工“再次检查”。
- 用户关闭或暂停：`paused`。
- 身份、版本控制或服务状态无法继续：`blocked`。

每一集单独建立观察窗口。新一集首次下载成功后创建新的观察单元，不重置其他集的状态。

## 10. MoviePilot 边界

MoviePilot 仍使用 NasEmby 已有源码，不重写适配器。

设置提供独立主开关，默认关闭：

- 关闭：只可读取连接配置状态，不创建订阅或触发搜索。
- 开启：只提供人工查重预览和确认按钮。

只有 Torra 不覆盖所需 PT 站点、Torra 观察窗口已结束、没有 Torra/qB 活动任务，并且用户明确开启 MoviePilot 时，才允许进入候选流程。Torra 不可达不会自动切换 MoviePilot。

MoviePilot 自动调度本轮不实施。以后只有在用户确认其数据库稳定并单独批准新设计后，才考虑自动模式。

## 11. 页面设计

### 11.1 订阅设置

增加“Torra 追更洗版”：

- 总开关。
- 可编辑时间点列表，默认 `2, 6, 12, 24, 48, 72`。
- 最小间隔、每日上限、单批上限。
- MoviePilot 独立开关；开启后也只提供人工备用入口。

设置保留在订阅设置页，不混入系统连接设置。

### 11.2 我的订阅

单条订阅显示：

- 当前版本状态。
- 下一次自动检查时间。
- 已执行次数。
- 最近跳过或失败原因。
- 单条暂停、恢复、修改时间表和“立即检查”。

### 11.3 任务中心与控制室

用户文案使用：

- 已有可看版本，继续观察。
- 等待当前版本入库后检查。
- 等待下次检查。
- 正在让 Torra 检查更好版本。
- 已达到目标版本。
- 观察已结束，可手动检查。
- 需要处理。

Torra、qB、幂等键和内部状态作为次级技术信息显示。

影院大厅不因本设计修改。

## 12. 计划 API

以下接口属于待实施 v2 设计，当前不可用：

- `GET /api/v2/subscription-automation/settings`
- `PATCH /api/v2/subscription-automation/settings`
- `GET /api/v2/subscriptions/:id/quality-watch`
- `POST /api/v2/subscriptions/:id/torra-rewash-analyses`
- `POST /api/v2/subscriptions/:id/torra-rewashes`
- `PATCH /api/v2/subscriptions/:id/quality-watch`
- `GET /api/v2/automation-actions/:actionId`
- `POST /api/v2/subscriptions/:id/moviepilot-previews`
- `POST /api/v2/subscriptions/:id/moviepilot-pushes`

追更洗版分析虽然不直接下载，但会让 Torra 搜索 PT 站点，因此使用 POST、独立分析闸门、冷却和幂等，并返回 `202 Accepted`、动作 ID 和 `Location` 轮询地址。候选下载同样返回 `202`，并继续要求会话、Origin、下载闸门、明确确认、幂等键和服务端复查。暂停、恢复和单条时间表覆盖通过同一个 PATCH 完成。

动作查询必须区分本地动作状态与 Torra 外部 job 状态，并返回 `external_job_id` 的脱敏可审计值。读取和成功的 PATCH 返回 `200`；语义不合法返回 `422`；幂等或并发冲突返回 `409`；触发限频返回 `429`；上游不可达返回 `502`；对应功能闸门关闭返回 `503`。错误不能包装在 `200` 中。最终字段在实施时加入机器契约后冻结。

## 13. 安全与错误处理

- 浏览器不读取或保存 Torra、MoviePilot 凭据。
- 追更洗版使用独立环境闸门，默认关闭。
- MoviePilot 使用单独闸门，默认关闭。
- 外部响应进入日志前脱敏并限制长度。
- SQLite 锁冲突返回可重试状态，不伪装成成功。
- Torra 不可达时延后当前动作，不自动切换其他 provider。
- qB 关联不可靠时宁可阻塞，不猜测并行下载。
- 调度批量有上限，单个失败不阻塞其他订阅。
- 所有自动动作都有审计记录和人工暂停入口。

## 14. 测试方案

### 14.1 SQLite

- 空数据初始化。
- 两个 JSON 的正常迁移、非法数据拒绝和差异报告。
- 迁移中断和回滚。
- 多线程保存、删除、改季和进度更新。
- WAL 重启恢复、busy timeout 和 schema version。
- 迁移后确认 JSON 没有运行时写入。

### 14.2 Torra 追更洗版协调器

- 首次成功创建观察单元。
- Emby 基准未就绪时停留在等待状态，就绪后才开始计算检查时间。
- `2/6/12/24/48/72` 到期计算与自定义计划。
- Torra 追更洗版分析结果只选择 `is_upgrade=true` 且分数高于 Emby 基准的每行最高分候选。
- 没有正分差候选时不调用下载接口，并继续后续观察。
- 分析和下载 job 的 pending、running、success、failed、cancelled 与服务重启续查。
- qB 活动、Torra 运行中、目标已达、每日上限和冷却跳过。
- 幂等重放、崩溃租约恢复和失败重试。
- 多集并行状态互不覆盖。
- 不可靠关联进入阻塞状态。

### 14.3 MoviePilot

- 复用 NasEmby 源码的连接、查重、创建和重搜模拟契约。
- 主开关关闭时零写调用。
- Torra 活动或 qB 活动时阻止 MoviePilot。
- 外部错误不泄露 Token 或原始响应。

### 14.4 回归与部署

- 现有 Python 测试、前端类型检查、Vite 构建和 npm audit。
- HTTP v1/v2 契约和认证守卫。
- Docker 持久化、重启和无 Node 运行层。
- 影院大厅冻结规则和现有页面回归。
- 测试环境不连接真实外部写接口。

## 15. 实施顺序

1. 建立 SQLite schema、连接工厂和迁移器。
2. 增加仓储接口，先做 JSON/SQLite 只读差异测试。
3. 一次性切换 NasEmby 订阅读写到 SQLite，删除运行时 JSON 写路径。
4. 将 Torra 推送幂等和冷却从内存迁入 SQLite。
5. 增加追更洗版观察单元和只读状态 API。
6. 增加 Torra 追更洗版分析、下载和 job 状态适配器。
7. 增加人工追更洗版分析预览和按高分候选下载确认。
8. 增加默认关闭的追更洗版调度器。
9. 增加 MoviePilot 独立开关、预览和人工备用入口。
10. 更新页面、API 契约、部署文档和完整测试。
11. 构建新的只读候选镜像，再进入 fnOS 实机窗口。

## 16. 验收标准

- 生产订阅只写 SQLite，不双写 JSON。
- JSON 迁移可验证、可审计、失败不丢数据。
- NasEmby 现有订阅、详情、日历和季处理结果保持兼容。
- Torra 首次成功后先等待 Emby 基准，基准就绪后进入追更洗版观察状态。
- 追更洗版时间可自定义，默认从 2 小时开始。
- qB 活动和 Torra 运行状态能够阻止重复搜索。
- 版本高低由 Torra 规则和其返回的 Emby 基准分/候选分决定，中控不建立第二套评分器。
- 自动动作只选择 Torra 明确标记为升级且分数更高的候选。
- Torra job 被提交、执行和完成有独立状态，服务重启后能够从 SQLite 续查。
- MoviePilot 默认关闭，关闭时没有外部写调用。
- 自动调度和所有外部写闸门默认关闭。
- 代码阶段全部测试不触发真实外部动作。
