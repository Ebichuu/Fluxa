# 媒体控制中心 HTTP v1 契约

机器清单：`docs/contracts/http-api-contract-v1.json`  
路由数量：47  
运行实现：Python / Flask

新增能力使用真正的 URL 版本契约：`docs/contracts/http-api-contract-v2.json`，当前共 53 条。v1 的 47 条冻结路径和历史状态码不变。

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
| `POST /api/qbittorrent/actions/:action/preview` | 只读检查最多 20 个 hash，返回是否允许、影响数量、禁止原因、确认要求、幂等键和冷却时间 |
| `POST /api/qbittorrent/actions/:action` | `hashes`、`taskId`、`title`、可选 `idempotencyKey`；执行前重新读取 qB 状态，旧预览键返回 `409 QB_PREVIEW_STALE` |
| `GET /api/subscriptions/items` | 可选 `include_progress=1` |
| `GET /api/v2/home/summary` | 无参数；按任务链、调度器心跳和服务证据返回今日结论，证据不足不得报告绿色正常；问题项可选返回 `displayTitle`、`seasonNumber`、`episodeNumber` 和 `secondaryReasonText`；统计可选返回 `archivedToday` 与 `completedTargetsToday`，旧 `ingestedToday` 保留兼容 |
| `GET /api/v2/subscriptions/workbench` | 可选 `limit`（1–100，默认 24）、`offset`（默认 0）、`mediaType`（`movie`/`tv`）和 `query`；返回五项能力状态、全量统计、当前页订阅、`page.nextOffset` 和可选 `posterBackfillIds`，只读访问外部服务 |
| `POST /api/v2/subscriptions/visual-backfills` | `ids` 为最多 100 个订阅 ID；只按明确 TMDB 身份补充空缺海报/背景，不按标题猜图；本地写入开启时可补充已有本地记录，关闭时只返回视觉结果；仅 Torra 条目始终不创建本地镜像 |
| `GET /api/v2/subscriptions/reconciliation` | 无参数；只读对比 Fluxa 与 Torra，独立返回对账、履约、健康状态，不修改或删除任一台账 |
| `GET /api/v2/tasks/summary` | 返回唯一任务链数量、健康/身份/执行三维状态数量、阶段数量、服务状态和稳定 `version`；支持 ETag 条件读取 |
| `GET /api/v2/tasks/chains` | 支持 `healthState`、`identityState`、`executionState`、`chainId`、`targetKey`、`updatedAfter`、`offset`、`limit`；默认返回 20 条唯一链路摘要和稳定分页字段，不返回完整阶段证据；摘要可选返回 `embyEvidenceScope` |
| `GET /api/v2/tasks/chains/:chainId` | 返回单条任务链的阶段证据、artifact、原因和动作资格；不存在返回 `404 TASK_CHAIN_NOT_FOUND`；Emby 证据范围可选为 `none`、`title` 或 `episode` |
| `GET /api/v2/calendar` | 按月聚合播出日期与任务链获取/入库证据，返回标准 `chainId/targetKey`、播出/获取/入库/正常保护/逾期/未知状态和 `Asia/Shanghai` 时区；支持 ETag |
| `GET /api/v2/subscriptions/capabilities` | 只读返回本地写入、Torra 推送和调度器真实运行状态，供发现页生成不夸大的追更文案 |
| `POST /api/subscriptions/save` | 标题、TMDB ID、媒体类型和可选元数据 |
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
| `POST /api/v2/subscriptions/:id/moviepilot-previews` | 空对象；服务端复核观察单元、Torra、qB 和 MoviePilot 查重 |
| `POST /api/v2/subscriptions/:id/moviepilot-pushes` | `confirm=true`、12–128 字符幂等键；不接受外部订阅 ID、Token 或 URL |
| `GET /api/v2/torra/subscription-sync/preview` | 无参数；只读取 Torra 与本地台账，不调用 Torra 写接口 |
| `POST /api/v2/torra/subscription-sync/imports` | `confirm=true`、12–128 字符幂等键；导入和幂等结果在同一 SQLite 事务提交 |
| `POST /api/v2/torra/subscription-sync/runs` | 空对象；只读取 Torra，并更新本地已关联镜像的状态 |
| `GET /api/v2/activity/logs` | 可选 `category` 和 `limit`，最多返回 1000 条脱敏记录 |
| `DELETE /api/v2/activity/logs` | `confirm=true`；清空后写入一条新的清空审计记录 |
| `GET /api/v2/system/metrics` | 无参数，30 秒服务端缓存 |
| `GET /api/discover/browse` | 来源、类型、排序、语言、年份、风格、provider 和分页 |
| `GET /api/discover/search` | `query`、可选 `page` |
| `GET /api/discover/resources/search` | 标题，可选类型、年份、TMDB ID 和来源 |

## 5. 响应与字段边界

公开订阅、详情、日历、发现和资源响应通过 `contract_mapping.py` 白名单映射。浏览器不会收到原始上游包络、未知内部字段、Cookie、Token 或异常正文。

任务链健康状态固定为 `action_required`、`evidence_insufficient`、`waiting`、`protected`、`normal`，优先级依次降低。缺失或过期证据不得返回 `normal`；已有计划重试返回 `waiting`；低分、重复或已有更高版本返回 `protected`，并且不会通过该读取接口开放重试动作。

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

当前 47 条 v1 契约不承担新增语义。43 条 `/api/v2` 接口包括：

- 当前 React 使用：集成脱敏摘要、Torra 单条预览/推送、缓存系统指标、私人 RSS 来源管理、本地种子库和管理员运行时配置。
- 阶段 6 人工追更洗版：全局设置、单条观察设置、人工 Torra 分析、人工候选下载和 RSS 匹配人工分析，已接入 React 订阅详情与 RSS 种子库。
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

以下接口已在 2026-07-18 阶段 6 注册，并计入当前 43 条机器契约：

- `GET /api/v2/subscription-automation/settings`
- `PATCH /api/v2/subscription-automation/settings`
- `GET /api/v2/subscriptions/:id/quality-watch`
- `POST /api/v2/subscriptions/:id/torra-rewash-analyses`
- `POST /api/v2/subscriptions/:id/torra-rewashes`
- `PATCH /api/v2/subscriptions/:id/quality-watch`
- `POST /api/v2/rss-matches/:id/torra-rewash-analyses`

后台 RSS 即时分析与有限主动兜底已经实现，但它们不是 HTTP 接口：可靠 `candidate` 只有在 RSS 与追更洗版双闸门、SQLite 设置、观察窗口、Torra/qB 空闲、冷却和小时/每日限额全部通过时，才创建固定幂等的一次性分析动作；RSS 无命中时，协调器按 SQLite 时间表、批量 2、公平游标和全局并发 1 做有限检查。动作保存外部 job 后仅续查原任务，分析结果不会自动下载。人工接口只允许从服务端已完成分析动作读取分析 ID 与候选映射，下载还必须通过独立下载闸门。

追更洗版分析会触发 PT 站点搜索，因此不是无副作用 GET，必须使用独立分析闸门、冷却和幂等。分析和候选下载都创建异步动作，返回 `202 Accepted`、动作 ID 和 `Location` 轮询地址；不能用 200 表示 Torra 已经完成。候选下载还必须满足管理员会话、下载闸门、确认和服务端复查。这里的“追更洗版”只指更新期间的高质量版本追踪，不包含 Torra 自身的完结洗版。

动作查询需要同时表达媒体控制中心本地状态和 Torra 外部 job 状态。服务重启后如果动作已经保存 Torra job ID，只能继续轮询原 job，不能重复提交。全局和单条设置中的 `window_hours` 只接受 `24` 或 `48`；时间点不得超过窗口，否则返回 `422`。当前集窗口到期不再自动搜索，下一集建立新窗口。计划状态码为：读取和 PATCH 成功 `200`、异步动作已创建 `202`、并发或幂等冲突 `409`、语义不合法 `422`、限频 `429`、上游失败 `502`、功能闸门关闭 `503`；错误不能包装在 `200` 中。以上约束已固化在 `docs/contracts/http-api-contract-v2.json`，并由契约测试逐条校验。

## 13. 已实现的私人 PT RSS 种子库接口

以下接口已经进入当前 53 条 v2 机器契约和 React：

- `GET /api/v2/rss-sources`
- `POST /api/v2/rss-sources`
- `GET /api/v2/rss-sources/:id`
- `PATCH /api/v2/rss-sources/:id`
- `DELETE /api/v2/rss-sources/:id`
- `POST /api/v2/rss-sources/:id/tests`
- `GET /api/v2/rss-items`
- `GET /api/v2/rss-items/:id`
- `POST /api/v2/rss-items/identity-backfills`
- `GET /api/v2/rss-matches`
- `GET /api/v2/automation-actions/:id`

私人 RSS 和下载地址按用户选择在 SQLite 中明文保存，但所有读取响应、错误和日志都不得返回完整地址或 Passkey。来源创建返回 `201 + Location`，删除成功返回 `204`；测试返回 `202`、动作 ID 和统一动作轮询 `Location`。来源和种子列表分页，重复来源返回 `409`，非法 RSS/周期/保留期或 `identityStatus` 返回 `422`，收集闸门关闭返回 `503`。RSS 收集闸门关闭只阻止测试和后台外部访问，本地来源配置 CRUD 不产生网络请求。

`GET /api/v2/rss-items` 保留原有查询行为，并接受可选 `identityStatus=identified|conflict|unidentified`、`tmdbId`、`mediaType`、`seasonNumber` 和 `year` 筛选。订阅目标搜索优先返回同一 TMDB 身份；电视剧标题回退不强制年份，明确不同季号仍会排除，季号缺失但没有冲突时只作为人工候选返回，并附带 `matchMethod=title_media_scope`、`matchConfidence=fallback` 和 `seasonScopeState=unknown`；电影标题回退仍必须匹配年份。此类回退候选不能触发自动下载或反向认领身份。

列表与详情响应新增可选身份字段：`tmdbId`、`imdbId`、`identityStatus`、`identitySource`、`identityConfidence` 和 `identityUpdatedAt`。身份只来自 RSS/Atom 结构化字段、简介/公开链接中的明确 ID，或唯一可靠的标准标题追更匹配；多 ID 冲突不会绑定，模糊标题不会反向认领。`GET /api/v2/rss-items/:id` 供中文详情抽屉按需读取，仍不返回 `download_url`、`detail_url` 或包含 Passkey 的原始地址。`POST /api/v2/rss-items/identity-backfills` 是管理员显式触发的本地有界回填，每批 1-200 条，只处理未识别记录；电视剧回填要求类型、标准标题和季号唯一一致，电影回填要求类型、标准标题和年份唯一一致。唯一匹配才写入身份，多候选记录为 `conflict`，不会访问外部详情页或执行下载。

`GET /api/v2/rss-sources` 的摘要可选返回最近一次身份回填的运行时间、扫描、识别、冲突、未变化和剩余数量。未运行与“已运行但识别为 0”必须明确区分，不能仅凭当前未识别数量推断回填器是否工作。

`/api/v2/rss-matches` 读取 SQLite 中的本地匹配记录，可按 `candidate / ignored / triggered / confirmed / expired` 状态筛选。当前只有新插入 RSS 条目在活动观察窗口内通过媒体身份、标题/别名、季和明确集号校验后才会生成 `candidate`；同一条目与观察单元唯一，历史条目和过期窗口不反向匹配。该记录不代表本地已经判断版本更好。`POST /api/v2/rss-matches/:id/torra-rewash-analyses` 允许人工使用本地已有匹配；RSS 收集闸门关闭不影响这条人工分析路径，但仍要求追更洗版总闸门、SQLite 设置和上游复查。

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

活动接口使用统一 `{ code, error, request_id }` 错误包络。日志写入前会递归脱敏密码、Token、Cookie、Authorization、Passkey、Bearer 和全部 URL 查询值；任务中心只显示中文动作、结果、稳定错误码和请求 ID，不展示上游异常正文或完整 RSS 地址。
