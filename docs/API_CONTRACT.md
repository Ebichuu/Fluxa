# 媒体控制中心 HTTP v1 契约

机器清单：`docs/contracts/http-api-contract-v1.json`  
路由数量：47  
运行实现：Python / Flask

新增能力使用真正的 URL 版本契约：`docs/contracts/http-api-contract-v2.json`，当前共 64 条。v1 的 47 条冻结路径和历史状态码不变。

## 1. 版本规则

当前浏览器路径保持 `/api/*`，机器清单将其定义为 v1 兼容契约。破坏性改动必须新增 `/api/v2/*` 或提供兼容期，不能直接改变现有字段、类型、状态码或认证边界。

v1 保留少量历史 HTTP 语义：部分删除和动作使用 POST、创建订阅返回 200、错误包络存在少量差异。当前不为追求形式统一而破坏 React 调用。

新增加的 v2 接口统一使用 `{ "code", "error", "request_id" }` 错误包络，并通过 `X-Request-ID` 响应头返回同一请求 ID；不得复制 v1 的历史错误格式。

## 2. 认证边界

公开启动路由只有：

- `GET /healthz`
- `GET /auth/login`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /api/auth/session`

其余 42 条路由必须通过管理员会话认证。未登录 API 返回：

```json
{
  "error": "需要登录",
  "code": "AUTH_REQUIRED"
}
```

管理员认证不依赖 Origin 白名单；受保护接口必须持有有效管理员会话，浏览器会话 Cookie 使用 `HttpOnly` 与 `SameSite=Strict`，服务端不向任意来源开放带凭据 CORS。

## 3. 路由分组

| 分组 | 能力 |
| --- | --- |
| 启动与认证 | 健康、登录、退出、会话状态 |
| 管理员设置 | 全部应用配置的脱敏目录、分组保存和显式清除敏感值 |
| 媒体 | Emby 首页、概览、图片和刷新证据 |
| qBittorrent | 摘要、暂停、恢复 |
| Torra / Symedia | 服务摘要与任务证据 |
| 任务链 | 四步聚合状态 |
| 订阅 | 列表、详情、保存、分类、改季、配置、日历和安全推送 |
| 发现 | 浏览、趋势、搜索和资源搜索 |
| 活动 | 脱敏活动日志 |
| 内部诊断 | 同进程 NasEmby 只读诊断 |
| 影院大厅 | Mineradio 嵌入页 |

完整方法、路径、认证、读写属性、成功状态和响应类型以机器清单为准。

## 4. 请求约束

| 路由 | 主要请求字段 |
| --- | --- |
| `POST /auth/login` | 表单 `access_key`、可选 `next`，正文不超过 2 KiB |
| `GET /api/media/home` | 可选 `libraryId` |
| `POST /api/media/emby/refresh` | 无正文，必须有较新 Symedia 证据 |
| `POST /api/qbittorrent/actions/:action/preview` | 只读检查最多 20 个 40 位公开任务引用；服务端从实时 qB 快照反解，返回是否允许、影响数量、禁止原因、确认要求、幂等键和冷却时间 |
| `POST /api/qbittorrent/actions/:action` | `hashes` 字段承载公开任务引用，另含 `taskId`、`title`、可选 `idempotencyKey`；旧客户端真实 hash 输入兼容但响应不回显，执行前重新读取 qB 状态，旧预览键返回 `409 QB_PREVIEW_STALE` |
| `GET /api/subscriptions/items` | 可选 `include_progress=1` |
| `GET /api/v2/home/summary` | 无参数；按任务链、调度器心跳和服务证据返回今日结论；身份、RSS 和调度维护信息进入中性诊断，qB、Torra、Symedia 或 Emby 等关键服务不可验证时不得报告绿色正常；问题项可选返回 `displayTitle`、`seasonNumber`、`episodeNumber` 和 `secondaryReasonText`；无法验证时 `archivedToday`、`activeDownloadTasks` 返回 `null` 而不是伪造 `0`，旧 `ingestedToday` 保留兼容 |
| `GET /api/v2/subscriptions/workbench` | 可选 `limit`（1–100，默认 24）、`offset`（默认 0）、`mediaType`（`movie`/`tv`）和 `query`；返回五项能力状态、全量 `following/completed/actionRequired/inLibrary` 生命周期统计、当前页订阅、`page.nextOffset` 和可选 `posterBackfillIds`，只读访问外部服务；映射完整且读取成功的 Torra-only 条目是正常只读追更，不因缺少本地镜像进入待处理 |
| `POST /api/v2/subscriptions/visual-backfills` | `ids` 为最多 100 个订阅 ID；只按明确 TMDB 身份补充空缺海报/背景，不按标题猜图；本地写入开启时可补充已有本地记录，关闭时只返回视觉结果；仅 Torra 条目始终不创建本地镜像 |
| `GET /api/v2/subscriptions/reconciliation` | 无参数；只读对比 Fluxa 与 Torra，独立返回对账、履约、健康状态，不修改或删除任一台账 |
| `GET /api/v2/tasks/summary` | 返回唯一任务链数量、健康/身份/执行三维状态数量、日常四态 `userCounts`、阶段数量、服务状态和稳定 `version`；支持 ETag 条件读取；可选 `systemIssues` 返回系统级问题（如 Torra 秒传）的分类级安全摘要 |
| `GET /api/v2/tasks/chains` | 支持 `healthState`、可重复 `identityState`、`executionState`、`userState`、`completedDate`、`chainId`、`targetKey`、`subscriptionId`、`tmdbId`、`title`、`seasonNumber`、`updatedAfter`、`offset`、`limit` 和 `refresh=1`；默认返回 20 条唯一链路摘要和稳定分页字段；摘要可选返回 `userState/resultText/completedAt/primaryAction/embyEvidenceScope`；可选 `systemIssues` 同任务 summary |
| `GET /api/v2/tasks/chains/:chainId` | 可选 `refresh=1`；返回单条任务链的阶段证据、artifact、原因、`userState/resultText/completedAt/primaryAction` 和动作资格；不存在返回 `404 TASK_CHAIN_NOT_FOUND`；Emby 证据范围可选为 `none`、`title` 或 `episode` |
| `GET /api/v2/tasks/ledger/migrations/preview` | 只读计算旧标题链到标准 TMDB 链的安全迁移计划、拒绝原因和预计别名数量；不写台账，不触发外部服务写操作 |
| `GET /api/v2/calendar` | 支持 `year/month/type/view`、单日 `date` 和范围 `from/to`；聚合播出日期与任务链获取/入库证据，返回标准 `chainId/targetKey`、播出/获取/入库/正常保护/逾期/未知状态和 `Asia/Shanghai` 时区；月摘要每天保留前三条 `preview`，同时返回覆盖全部条目的轻量 `searchIndex`，搜索不得只查预览；支持 ETag |
| `GET /api/v2/search` | `q` 最长 200 字符，`limit` 为 1–20；聚合本地追更、已识别 RSS、任务、当月日历和 Emby TMDB 索引，按 `mediaKey` 去重并保留来源深链；本地没有匹配结果时使用现有 TMDB 只读客户端补充，未配置或读取失败降级为空且不写缓存；无 TMDB 本地任务可返回空 `tmdbId`、公开 `chainId` 和任务深链 |
| `GET /api/v2/media/:mediaKey` | 外部只读返回单作品追更、Torra、下载、115、入库、Emby、日历、唯一主操作和深链；`mediaKey` 为 `movie:tmdbId` 或 `tv:tmdbId`；过期、未验证或 `healthState=protected` 的 blocked 阶段不形成真实异常 |
| `GET /api/v2/subscriptions/capabilities` | 只读返回本地写入、Torra 推送和调度器真实运行状态，供发现页生成不夸大的追更文案；新增可选 `manualFollow`（`state`=`write_disabled`/`saved_only`/`queued_ready`、`provider`、`blockers`）与 `sourceScan`（`configured`/`enabled`/`running`）；`sourceScan` 只描述后台来源扫描，不参与手动加入结果判定 |
| `POST /api/subscriptions/save` | 标题、TMDB ID、媒体类型和可选元数据；响应可选返回 `activation`（`state`=`saved_and_torra_pushed`/`saved_and_queued`/`saved_only`/`already_exists`/`saved_push_failed`，含用户文案 `message`、`provider`、`queued` 与脱敏 `reason`）；异步入队只返回 `saved_and_queued`，不提前声称已推送 Torra |
| `PATCH /api/subscriptions/:id/category` | 八分类 key 或 `null` |
| `GET /api/subscriptions/detail` | 必填 `id`，可选 `season` |
| `GET /api/subscriptions/calendar` | `year`、`month`、`type` |
| `GET /api/v2/subscriptions/:id/torra-push-preview` | 路径中的订阅 ID，只读预检 |
| `POST /api/v2/subscriptions/:id/torra-pushes` | `confirm=true`、12–128 字符幂等键 |
| `PATCH /api/v2/subscription-automation/settings` | camelCase 设置字段；窗口只允许 24/48 小时，时间点严格递增且最早 30 分钟 |
| `PATCH /api/v2/subscriptions/:id/quality-watch` | 可选 `paused`、`windowHours`、`scheduleMinutes` |
| `POST /api/v2/subscriptions/:id/torra-rewash-analyses` | `idempotencyKey`、可选 `unitId` |
| `POST /api/v2/subscriptions/:id/torra-rewashes` | `confirm=true`、`idempotencyKey`、`analysisActionId`、可选 `unitId` |
| `POST /api/v2/rss-matches/:id/torra-rewash-analyses` | `idempotencyKey`；不接受 Torra ID 或候选映射 |
| `POST /api/v2/rss-matches/:id/torra-rewashes` | `confirm=true`、`idempotencyKey`、`analysisActionId`；严格绑定同一 RSS 匹配、追更和观察单元 |
| `GET /api/v2/rss-matches/:id` | 读取单条本地 RSS 匹配；不存在返回 `404 RSS_MATCH_NOT_FOUND`；响应不包含下载地址、详情地址或 Passkey |
| `POST /api/v2/rss-matches` | `rssItemId`、`subscriptionId`、`unitId`；服务端重新验证种子身份、媒体类型、季集、观察窗口与 Torra 归属；新建返回 `201 + Location`，已存在返回 `200`；只写本地运行证据，不依赖订阅配置写闸门，也不触发 Torra |
| `POST /api/v2/subscriptions/:id/moviepilot-previews` | 空对象；服务端复核观察单元、Torra、qB 和 MoviePilot 查重 |
| `POST /api/v2/subscriptions/:id/moviepilot-pushes` | `confirm=true`、12–128 字符幂等键；不接受外部订阅 ID、Token 或 URL |
| `GET /api/v2/torra/subscription-sync/preview` | 无参数；只读取 Torra 与本地台账，不调用 Torra 写接口 |
| `POST /api/v2/torra/subscription-sync/imports` | `confirm=true`、12–128 字符幂等键；导入和幂等结果在同一 SQLite 事务提交 |
| `POST /api/v2/torra/subscription-sync/runs` | 空对象；只读取 Torra，并更新本地已关联镜像的状态 |
| `GET /api/v2/activity/logs` | 可选 `category` 和 `limit`，最多返回 1000 条脱敏记录；可选 `view=important` 在应用 `limit` 前折叠 `request_id=background` 且状态为 success/info/skip 的相同 category/action/status 后台活动，折叠项返回 `repeatCount`、`firstTime`、`lastTime`；error 与人工请求永不折叠，默认 raw 行为不变 |
| `GET /api/v2/system-issues/secupload-failures` | 只读返回 Torra 秒传系统问题：状态机 `normal/recovering/action_required/unknown`（600 秒宽限、86400 秒计划上限）、分类级批次摘要、近三批失败数、重试策略与下次计划；分类使用稳定摘要公开 ID，不泄露 Torra 原始分类 ID、插件 key、目录或文件路径 |
| `POST /api/v2/system-issues/secupload-failures/retry-previews` | 手动重试预检；重新读取插件状态、分类映射、活动运行与自动计划，自动计划有效或已有活动运行时拒绝 |
| `POST /api/v2/system-issues/secupload-failures/retries` | `confirm=true`、12–128 字符幂等键；复用 provider_actions 持久化（目标键 `system:torra:secupload`），全局与分类锁竞争返回 `409`，成功调用 Torra 正式任务接口后保存 run ID 返回 `202` |
| `GET /api/v2/system-issues/secupload-failures/retries/:actionId` | 通过插件 recent_runs 按 run ID 轮询动作状态；终态写回后刷新秒传摘要 |
| `DELETE /api/v2/activity/logs` | `confirm=true`；清空后写入一条新的清空审计记录 |
| `GET /api/v2/system/metrics` | 无参数，30 秒服务端缓存 |
| `GET /api/discover/browse` | 来源、类型、排序、语言、年份、风格、provider 和分页 |
| `GET /api/discover/search` | `query`、可选 `page` |
| `GET /api/discover/resources/search` | 标题，可选类型、年份、TMDB ID 和来源 |

## 5. 响应与字段边界

公开订阅、详情、日历、发现和资源响应通过 `contract_mapping.py` 白名单映射。浏览器不会收到原始上游包络、未知内部字段、Cookie、Token 或异常正文。

任务链健康状态固定为 `action_required`、`evidence_insufficient`、`waiting`、`protected`、`normal`，优先级依次降低。缺失或过期证据不得返回 `normal`；已有计划重试返回 `waiting`；低分、重复或已有更高版本返回 `protected`，并且不会通过该读取接口开放重试动作。

任务的普通用户状态固定为 `action_required`、`in_progress`、`completed`、`no_action`，分别对应“需要处理、处理中、已完成、无需处理”。明确且仍有效的执行故障进入 `action_required`，有活动下载或活动阶段进入 `in_progress`，已有可信入库完成证据进入 `completed`；身份未关联、身份冲突、普通等待和正常保护进入 `no_action`，其原始 `identityState/executionState/healthState` 只用于高级诊断。每条任务同时返回一句话 `resultText` 和最多一个 `primaryAction`，正常保护不得抢占异常主操作。

全局搜索结果的 `sources` 只使用 `subscription / rss / task / calendar / emby / tmdb` 白名单；RSS 仅聚合 `identityStatus=identified` 的条目。标准结果使用 `movie:tmdbId` 或 `tv:tmdbId`，可以进入 `/api/v2/media/:mediaKey`；无 TMDB 的本地任务返回 `tmdbId=""`、公开 `chainId`，`links.overview` 与 `links.tasks` 都指向携带该 `chainId` 的任务中心，`links.api` 为空。此类结果不伪造 TMDB 身份，也不能调用作品详情接口。Emby 只有 TMDB 索引而没有安全标题时，只有按媒体键或 TMDB 身份定位才可形成候选；标题补充继续来自只读 TMDB 查询。

仅 Torra 条目的公开订阅 ID 固定为 `torra:<10 位 SHA-256 摘要>`，并视为不透明字符串。服务端在质量观察、人工分析、RSS 匹配等路径中重新读取当前 Torra 列表并解析该公开 ID；唯一命中后才使用内部远端 ID，未命中返回 `404`，摘要与另一真实 ID 发生冲突时返回 `409 TORRA_SUBSCRIPTION_KEY_CONFLICT`。公开订阅、观察单元、匹配、动作和错误响应不得包含原始 Torra ID；同步预览的 `conflictItems.subscriptionKey` 也必须投影为公开键，即使本地 SQLite 仍保存旧格式 `torra:<原始远端 ID>`。

没有 Fluxa/Torra 订阅时，Symedia 记录只有在电视剧类型、有效 TMDB、明确季号和非空规范标题同时成立时，才可以建立只存在于任务快照中的媒体目标。qB 必须以保守中文标题、相同季号和唯一候选归属；匹配方法分别记录为 `symedia_tmdb_anchor` 与 `symedia_title_season_unique`。多 TMDB 候选保持冲突，电影缺年份不使用该回退。统一链可以返回 Emby 作品级 `embyEvidenceScope=title`，但 Symedia 下游记录不能反推逐文件秒传方式或 Emby 单集索引。

内部诊断路由仍受会话保护，只用于核对同一 Python 进程中的 NasEmby 数据，不表示存在第二个服务。

集合边界：

- 发现和资源搜索使用分页或固定上限。
- 活动日志最多返回 1000 条。
- v1 订阅列表继续保持全量兼容；v2 追更工作台按 `limit + offset + nextOffset` 分页，媒体类型和关键词过滤在分页前执行。

## 6. 状态码

- `200`：普通读取或已完成动作。
- `202`：Emby 已接受刷新，或 qB 动作已接受但尚未完全确认。
- `303`：登录和退出跳转。
- `400`：输入格式错误。
- `401`：未登录。
- `403`：写闸门拒绝。
- `404`：资源不存在，或未注册的旧静态页面路径。
- `409`：状态冲突、并发锁或冷却。
- `429`：登录限流。
- `502`：上游失败。
- `503`：依赖未配置、离线，或已保留的核心兼容接口尚未安全接入。

未捕获异常返回脱敏的 `500 / INTERNAL_ERROR` 与请求 ID。

## 7. 已保留但默认关闭的核心入口

原 115、Telegram、HDHive、provider、缓存预热和 NasEmby 配置接口仍保留在源码与 Flask URL map 中，但不属于当前 47 条 React v1 契约。默认调用返回 `503 PRESERVED_CORE_API_DISABLED`；只允许在模拟测试中通过 `MCC_PRESERVED_CORE_API_ENABLED=true` 开启。

NasEmby 原静态管理页不注册为第二套生产页面，迁移期静态快照不再保存在公开仓库，因此 `/static/app.js` 仍返回 404。逐接口用途和副作用见 `docs/CORE_API_CAPABILITY_MATRIX.md`。

保留接口的守卫顺序固定为：未登录先返回 401；通过认证后，在总开关关闭时返回 503。总开关只用于模拟兼容测试，不能代替后续每组动作的细分写闸门。

`POST /api/subscriptions/import-nasemby` 仅为冻结路径兼容，生产不导入外部台账，调用返回明确的 404 禁用结果。

## 8. 自动验证

- 47 条方法与路径逐条存在。
- 42 条受保护路由未登录时逐条返回 401。
- 所有受保护写路由要求有效管理员会话并遵守对应写闸门。
- React API 引用必须命中 `client=true` 契约。
- 所有 GET 不能改变订阅、下载器或外部服务状态；允许生成脱敏访问审计和只读缓存。
- 保留核心接口默认返回 503，旧静态页仍保持 404。

## 9. HTTP v2 契约

当前 47 条 v1 契约不承担新增语义。64 条 `/api/v2` 接口包括：

- 当前 React 使用：集成脱敏摘要、Torra 单条预览/推送、缓存系统指标、私人 RSS 来源管理、本地种子库和管理员运行时配置。
- 阶段 6 人工追更洗版：全局设置、单条观察设置、人工 Torra 分析、人工候选下载和 RSS 匹配级分析/确认下载，已接入 React 订阅详情与 RSS 种子库。
- 作品统一视图：全局本地搜索和单作品生命周期总览，已接入顶部搜索与可分享作品页。
- 系统问题闭环：Torra 秒传状态机只读摘要、手动重试预检、确认执行与动作轮询；摘要经 `systemIssues` 附加到任务 summary/chains 与首页。
- 阶段 7 MoviePilot 人工备用：受独立闸门保护的预览和同步确认动作，已接入 React 订阅详情。
- 第一阶段 Torra 单向镜像与活动闭环：已有订阅预览、幂等导入、状态同步和统一脱敏活动日志，已接入 React 订阅页与任务中心。
- 延期保留：115 检查、Telegram 登录/频道、HDHive 授权/配置/签到、订阅级网盘开关、候选预览和单条转存。

Torra 推送目标固定，浏览器不能把普通订阅推送改投 Symedia 或 MoviePilot。独立 MoviePilot 备用接口只在观察窗口全部结束、Torra/qB 可核对且空闲时可用，服务端从唯一台账重新读取订阅并执行查重、确认、幂等和 60 秒冷却。

系统指标调用原 NasEmby 采样函数，响应只保留 CPU、内存、磁盘和网络白名单字段，不返回内部路径或 Emby 库列表。

所有 v2 接口继续使用整站会话认证；管理员运行时配置使用字段白名单、脱敏和 `SameSite=Strict` 会话，不要求填写 Origin 地址。其他外部动作仍遵守各自的会话、确认和功能闸门。延期的网盘路由继续存在但当前 `client=false` 且环境闸门关闭，等待以后版本，不能据此整体开启原核心 API。

## 10. v2 状态码和兼容性

- `200`：状态、候选或已确认同步动作完成。
- `201`：本地资源创建成功，并返回 `Location`。
- `202`：上游结果仍需后续证据确认。
- `204`：删除成功且没有响应正文。
- `400`：字段、确认或幂等键无效。
- `403`：细分写闸门关闭。
- `404`：订阅不存在。
- `409`：候选过期、冷却、重复任务或状态冲突。
- `422`：语法有效但周期、保留期或业务参数不符合规则。
- `429`：请求超过动作或上游限频。
- `502`：脱敏后的上游失败。
- `503`：对应的外部访问或动作闸门关闭。

v2 新增响应字段允许向后兼容扩展；删除字段、改变类型或放宽安全边界必须新增下一版本。

## 11. 已实现的管理员运行时配置接口

- `GET /api/v2/settings/runtime`：返回全部应用级配置目录、中文名称、用途说明、控件类型和重启提示；旧 NasEmby 字段统一归入最后的高级兼容分组。密码、Token、Cookie、API Key 只返回 `hasValue`，不返回明文。
- `PUT /api/v2/settings/runtime`：按字段白名单保存地址、账号、开关、路径和敏感值；敏感值留空保持原值，`clearSecrets` 用于明确清除；连接客户端会立即重配置，调度线程类字段返回重启提示。

接口只写入持久化的 `data/user.env`，不会修改宿主机端口、Docker 卷挂载或镜像标签。

## 12. 已实现的 Torra 追更洗版接口

以下接口已在 2026-07-18 阶段 6 注册，并计入当前 v2 机器契约：

- `GET /api/v2/subscription-automation/settings`
- `PATCH /api/v2/subscription-automation/settings`
- `GET /api/v2/subscriptions/:id/quality-watch`
- `POST /api/v2/subscriptions/:id/torra-rewash-analyses`
- `POST /api/v2/subscriptions/:id/torra-rewashes`
- `PATCH /api/v2/subscriptions/:id/quality-watch`
- `POST /api/v2/rss-matches/:id/torra-rewash-analyses`
- `POST /api/v2/rss-matches/:id/torra-rewashes`

后台 RSS 即时分析与有限主动兜底已经实现，但它们不是 HTTP 接口：可靠 `candidate` 只有在 RSS 与追更洗版双闸门、SQLite 设置、观察窗口、Torra/qB 空闲、冷却和小时/每日限额全部通过时，才创建固定幂等的一次性分析动作；RSS 无命中时，协调器按 SQLite 时间表、批量 2、公平游标和全局并发 1 做有限检查。动作保存外部 job 后仅续查原任务，分析结果不会自动下载。人工接口只允许从服务端已完成分析动作读取分析 ID 与候选映射，下载还必须通过独立下载闸门。

追更洗版分析会触发 PT 站点搜索，因此不是无副作用 GET，必须使用独立分析闸门、冷却和幂等。分析和候选下载都创建异步动作，返回 `202 Accepted`、动作 ID 和 `Location` 轮询地址；不能用 200 表示 Torra 已经完成。候选下载还必须满足管理员会话、下载闸门、确认和服务端复查。这里的“追更洗版”只指更新期间的高质量版本追踪，不包含 Torra 自身的完结洗版。

动作查询需要同时表达媒体控制中心本地状态和 Torra 外部 job 状态。服务重启后如果动作已经保存 Torra job ID，只能继续轮询原 job，不能重复提交。全局和单条设置中的 `window_hours` 只接受 `24` 或 `48`；时间点不得超过窗口，否则返回 `422`。当前集窗口到期不再自动搜索，下一集建立新窗口。计划状态码为：读取和 PATCH 成功 `200`、异步动作已创建 `202`、并发或幂等冲突 `409`、语义不合法 `422`、限频 `429`、上游失败 `502`、功能闸门关闭 `503`；错误不能包装在 `200` 中。以上约束已固化在 `docs/contracts/http-api-contract-v2.json`，并由契约测试逐条校验。

## 13. 已实现的私人 PT RSS 种子库接口

以下接口已经进入当前 64 条 v2 机器契约和 React：

- `GET /api/v2/rss-sources`
- `POST /api/v2/rss-sources`
- `GET /api/v2/rss-sources/:id`
- `PATCH /api/v2/rss-sources/:id`
- `DELETE /api/v2/rss-sources/:id`
- `POST /api/v2/rss-sources/:id/tests`
- `GET /api/v2/rss-items`
- `GET /api/v2/rss-items/:id`
- `POST /api/v2/rss-items/identity-backfills`
- `POST /api/v2/rss-items/match-runs`
- `GET /api/v2/rss-matches`
- `GET /api/v2/rss-matches/:id`
- `POST /api/v2/rss-matches`
- `GET /api/v2/automation-actions/:id`

私人 RSS 和下载地址按用户选择在 SQLite 中明文保存，但所有读取响应、错误和日志都不得返回完整地址或 Passkey。来源创建返回 `201 + Location`，删除成功返回 `204`；测试返回 `202`、动作 ID 和统一动作轮询 `Location`。来源和种子列表分页，重复来源返回 `409`，非法 RSS/周期/保留期或 `identityStatus` 返回 `422`，收集闸门关闭返回 `503`。RSS 收集闸门关闭只阻止测试和后台外部访问，本地来源配置 CRUD 不产生网络请求。

`GET /api/v2/rss-items` 保留原有查询行为，并接受可选 `identityStatus=identified|conflict|unidentified`、`tmdbId`、`mediaType`、`seasonNumber` 和 `year` 筛选。订阅目标搜索优先返回同一 TMDB 身份；电视剧标题回退不强制年份，明确不同季号仍会排除，季号缺失但没有冲突时只作为人工候选返回，并附带 `matchMethod=title_media_scope`、`matchConfidence=fallback` 和 `seasonScopeState=unknown`；电影标题回退仍必须匹配年份。此类回退候选不能触发自动下载或反向认领身份。

列表与详情响应新增可选身份字段：`tmdbId`、`imdbId`、`identityStatus`、`identitySource`、`identityConfidence` 和 `identityUpdatedAt`。身份只来自 RSS/Atom 结构化字段、简介/公开链接中的明确 ID，或唯一可靠的标准标题追更匹配；多 ID 冲突不会绑定，模糊标题不会反向认领。`GET /api/v2/rss-items/:id` 供中文详情抽屉按需读取，仍不返回 `download_url`、`detail_url` 或包含 Passkey 的原始地址。`POST /api/v2/rss-items/identity-backfills` 是管理员显式触发的本地有界回填，每批 1-200 条，只处理未识别记录；电视剧回填要求类型、标准标题和季号唯一一致，电影回填要求类型、标准标题和年份唯一一致。唯一匹配才写入身份，多候选记录为 `conflict`，不会访问外部详情页或执行下载。`POST /api/v2/rss-items/match-runs` 是管理员显式触发的历史匹配批处理，每批最多 200 条，只处理尚未产生匹配记录且没有身份冲突的本地种子，并返回扫描、候选和剩余数量。

`GET /api/v2/rss-sources` 的摘要可选返回最近一次身份回填的运行时间、扫描、识别、冲突、未变化和剩余数量。未运行与“已运行但识别为 0”必须明确区分，不能仅凭当前未识别数量推断回填器是否工作。

`GET /api/v2/rss-matches` 读取 SQLite 中的本地匹配记录，可按 `candidate / ignored / triggered / confirmed / expired` 状态筛选；`GET /api/v2/rss-matches/:id` 返回与创建接口相同的公开 DTO，供 `POST 201` 的 `Location` 解引用，不存在时返回统一 `404 RSS_MATCH_NOT_FOUND`，且不读取或返回 RSS 条目的下载地址、详情地址或凭据。公开 DTO 的 `reason` 只允许 `identity`、`mediaType`、`year`、`season`、`episode` 和 `matchSource`；旧 Torra subscription/unit ID、路径、URL、外部 job 字段及所有未知键在投影时剔除。新插入条目仍可由自动匹配器生成 `candidate`；普通追更页也可调用 `POST /api/v2/rss-matches` 为一个搜索结果建立单条人工匹配。人工创建不会调用全局 matcher，服务端重新读取 RSS 条目、本地或 Torra 只读追更、观察单元与 Torra 当前订阅，校验身份、媒体类型、年份、季集、基线时间和归属；同一条目与观察单元已存在时幂等返回原匹配。该记录不代表本地已经判断版本更好。随后 `POST /api/v2/rss-matches/:id/torra-rewash-analyses` 执行人工分析；成功响应只公开评分变化、质量和大小等脱敏摘要，不返回候选 ID。`POST /api/v2/rss-matches/:id/torra-rewashes` 要求明确确认，并重新校验分析动作确实属于同一匹配、追更和观察单元。匹配状态和下载动作 ID 在同一 SQLite 事务中更新，幂等重放不会重复提交。RSS 收集闸门关闭不影响人工分析，但下载仍要求独立下载闸门、Torra/qB 前检、限流和冷却。

## 14. 已实现的 MoviePilot 人工备用接口

- `POST /api/v2/subscriptions/:id/moviepilot-previews`
- `POST /api/v2/subscriptions/:id/moviepilot-pushes`

两条接口都先检查独立 `MCC_MOVIEPILOT_BACKUP_ENABLED` 闸门；关闭时立即返回 `503`，不读取 MoviePilot、Torra 或 qB。预览只接受空对象，返回标题、媒体类型、TMDB ID、季、模式和安全阻塞摘要，不返回 MoviePilot URL、Token、原始响应或外部订阅 ID。推送要求 `confirm=true` 和 12–128 字符幂等键，执行前重新读取唯一订阅台账、全部质量观察单元和 Torra/qB 状态。

已有 MoviePilot 订阅只触发重搜；没有时复用原 NasEmby 创建逻辑并触发搜索。同步动作返回 `200`，使用 `provider=moviepilot / action_type=backup-push` 写入 SQLite；幂等冲突、执行中和 60 秒冷却返回 `409`，上游失败写入终态并返回脱敏 `502`。本阶段没有自动调度器，React 仅提供人工预览和确认推送入口。

## 15. 已实现的 Torra 单向镜像与活动日志

- `GET /api/v2/torra/subscription-sync/status`
- `GET /api/v2/torra/subscription-sync/preview`
- `POST /api/v2/torra/subscription-sync/imports`
- `POST /api/v2/torra/subscription-sync/runs`
- `GET /api/v2/activity/logs`
- `DELETE /api/v2/activity/logs`

预览只读取 Torra 和本地 SQLite；确认导入要求 `MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED=true`、`confirm=true` 和 12–128 字符幂等键。服务端会重新读取 Torra，按远端 ID、TMDB 身份和保守标题规则匹配；身份冲突返回 `409`，不产生部分写入。镜像导入与幂等响应在同一 `BEGIN IMMEDIATE` 事务内提交，重复请求返回首次结果，不重复创建条目。

导入的新条目标记为 `origin=torra / readOnly=true`，第一阶段不会修改或删除 Torra。服务端同时拒绝这些条目的改季、屏蔽、清空和删除；手动同步与每 10 分钟后台同步只更新本地远端状态，远端消失时保留本地记录并标记 `remote_missing`。第二阶段才会单独设计“移除本地记录”和“删除 Torra 订阅”，两者不会合并为默认动作。

没有本地镜像的 Torra-only 条目同样只返回公开哈希 ID。浏览器提交该 ID 后，服务端必须从实时只读快照解析唯一远端行；质量观察中的公开 `subscriptionId` 和 `unitId` 沿用同一公开前缀，响应不能泄露原始远端 ID。该解析只建立请求上下文，不会因此创建 Fluxa 本地订阅或修改 Torra。

活动接口使用统一 `{ code, error, request_id }` 错误包络。日志写入前会递归脱敏密码、Token、Cookie、Authorization、Passkey、Bearer 和全部 URL 查询值；任务中心普通视图只显示中文动作和结果，稳定错误码与请求 ID 仅在高级诊断中展示，不展示上游异常正文或完整 RSS 地址。
