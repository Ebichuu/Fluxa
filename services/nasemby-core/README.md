# Python 统一后端

媒体控制中心唯一生产后端。目录名称沿用迁移期的 `nasemby-core`，避免为重命名制造大范围导入改动；运行时已经同时承载整站认证、React 静态托管、Mineradio、订阅/发现、外部适配器和任务链，不再作为独立侧车服务。

## 职责

- Flask 应用工厂、统一请求 ID、JSON 错误和整站访问保护。
- React `dist`、SPA 回退、Mineradio 原始资源和桥接页。
- NasEmby 原发现、JustWatch 海外流媒体、订阅、日历、资源规则和调度源码。
- SQLite 唯一订阅台账、独立发现候选池、Torra 已有订阅单向镜像、旧 JSON 一次性迁移、私人 PT RSS 本地种子索引、活动观察窗口匹配和订阅绑定规则影子评分。
- 115、Telegram、HDHive / pansou、provider 等原核心能力与接口调用关系。
- Torra 固定目标推送，以及只读权重规则、追更洗版人工分析、候选下载、job 状态解析、按集 Emby 基准、SQLite 幂等/租约和脱敏审计；自动 RSS 匹配只在 Fluxa 本地评分，不触发 Torra 整订阅搜索或下载。人工精准下载按唯一 RSS 产物预检并直接写入已确认映射的 qB，目录与下载器必须由实时 Torra 订阅和服务端配置派生；qB 分类只在 Torra 明确提供时使用，自动执行仍未开放。
- 30 秒缓存的 NAS 系统指标，以及统一脱敏、可筛选的 v2 活动日志。
- 115、Telegram、HDHive / pansou 和 MoviePilot 的 v2 细分接口继续保留；MoviePilot 阶段 7 已增加默认关闭的人工备用预览/推送，其他能力延期。
- Emby、qBittorrent、Torra、Symedia 的服务端适配和凭据隔离；Symedia 摘要把 transfer history 与归档监控、云盘监听、Webhook、STRM、归档调度和文件观察分别建模。实机 `/api/v1/system/sync_stats` 只提供按日 STRM 数量，不能绑定媒体目标，其独立能力继续保持 `unknown + NOT_INTEGRATED`；只有 Emby 返回的精确电影或季集目标 `Path` 明确以 `.strm` 结尾时，任务事实才确认该目标的 STRM 播放入口。
- 可选的 Torra 秒传 → Symedia 单文件交接：首次启用只建立当前水位，之后每 30 秒消费新成功秒传 job；按 Torra 配置源/目标与唯一 Symedia 归档任务严格映射，只提交一个精确媒体文件，不扫描 115 历史待整理目录。提交、重试和 Symedia 历史确认均写入 SQLite，旧同名历史不能确认新任务。
- 统一任务链、qB 暂停/恢复和证据驱动的 Emby 刷新。
- 六阶段独立事实契约与统一结果派生：`torra/qb/cloud115/symedia/strm/emby` 分别保存；115 优先使用 Torra 逐文件结果，没有该结果时只允许从当前目标已匹配且成功的 Symedia `115` 源路径确认“文件已到达 115”，不能确认秒传或原始上传方式；STRM 只接受 Symedia 明确 `.strm` 结果或 Emby 精确目标的 `.strm` 路径。任务、首页、作品、追更和日历已消费新结果，旧状态只由六阶段事实作兼容投影。
- 全局作品搜索与单作品生命周期聚合：合并本地追更、已识别 RSS、任务、日历和 Emby，并在本地无结果时使用 TMDB 只读补充。
- 按明确 TMDB 身份补充本地追更海报，并保持仅 Torra 条目只读。
- 单一 `data/`、`db/`、`upload/` 持久边界。

React、影院大厅、顶部导航和媒体队列不属于本模块的视觉实现范围。

## 运行时

- Python 3.13。
- Flask 3。
- Gunicorn：一个 `gthread` worker、四个请求线程。
- 生产端口：`8987`。
- 本地 `python -m app.main` 默认端口：`12388`。

生产不允许增加 Gunicorn worker 或横向副本。当前订阅台账和调度器没有多进程选主与并发写协调。

## 本地运行

```powershell
python -m pip install -r requirements.txt
python -m app.main
```

项目根目录的 `npm run dev` 会同时启动该 Python 进程和 Vite。

## Docker

正式部署使用项目根 `Dockerfile` 与 `docker-compose.yml`。根镜像通过 Node 构建阶段生成 React，再复制到 Python 3.13 运行阶段；最终镜像没有 Node 可执行文件。

正式部署只使用项目根 Dockerfile 和 Compose，不启动第二个 Core 容器；本目录不提供独立的 Docker 入口。

## 安全开关

部署只读验收固定：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED=false
NASEMBY_CORE_WRITE_ENABLED=false
MCC_PRIVATE_RSS_ENABLED=false
MCC_TORRA_QUALITY_WATCH_ENABLED=false
MCC_TORRA_REWASH_DOWNLOAD_ENABLED=false
MCC_SYMEDIA_SECUPLOAD_HANDOFF_ENABLED=false
MCC_MOVIEPILOT_BACKUP_ENABLED=false
MCC_PRESERVED_CORE_API_ENABLED=false
TORRA_PUSH_ENABLED=false
MCC_INTEGRATION_PROBE_ENABLED=false
MCC_INTEGRATION_MANAGEMENT_ENABLED=false
MCC_TELEGRAM_MANAGEMENT_ENABLED=false
MCC_HDHIVE_MANAGEMENT_ENABLED=false
MCC_CLOUD_SEARCH_ENABLED=false
MCC_CLOUD_TRANSFER_ENABLED=false
```

- 写闸门关闭时，订阅保存、分类、改季、配置、执行、删除和推送均被服务端拒绝。
- Torra 单向镜像线程随生产后台运行时启动，但 `MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED=false` 时不会访问 Torra；开启后每 10 分钟只同步已关联条目的状态。
- 第一阶段导入项标记为只读，服务端拒绝改季、屏蔽、清空和删除；删除 Torra 订阅留到第二阶段单独设计。
- 旧订阅总调度器只在显式开启环境闸门时启动；独立候选来源调度器只在“每日候选更新”开启时更新本地候选池，不访问 Torra 搜索、推送或下载入口。
- 追更洗版协调器只在 `MCC_TORRA_QUALITY_WATCH_ENABLED=true` 时启动，并继续要求 SQLite 中的追更设置开启；自动 RSS 入口只读 Torra 订阅和权重规则。可选缺集 PT 搜索兜底还要求 `torra_quality_missing_fallback_enabled=true`，只使用已关联日历中的明确已播缺集，默认关闭。
- RSS 精准下载还要求 `executionMode=manual`、`MCC_TORRA_REWASH_DOWNLOAD_ENABLED=true`、`TORRA_DOWNLOADER_ID` 与订阅下载器一致、10 分钟内有效的产物预览和人工确认。Torra 明确提供 `qb_category` 或 `download_category` 时原样提交；缺失时省略 qB 分类，普通媒体 `category`、保存目录、标题和历史任务均不能用于推断。打开评分闸门不会自动下载，设置接口在实机验收前拒绝 `automatic`。
- 秒传单文件交接同时要求总写闸门和 `MCC_SYMEDIA_SECUPLOAD_HANDOFF_ENABLED=true`，修改后重启生效。首次启用不会补扫历史文件；目标配置、目录边界或唯一归档任务任一无法确认时拒绝写入。回滚只需关闭该开关并重启，既有 115 文件和 Symedia 历史不受影响。
- MoviePilot 人工备用还要求 `MCC_MOVIEPILOT_BACKUP_ENABLED=true`、观察单元全部 `observation_expired`、Torra/qB 预检通过和明确确认；已有订阅只重搜，没有订阅才复用创建逻辑，默认不接入自动调度。
- NasEmby 的 115、Telegram、HDHive、缓存预热和 provider 核心 API 保留在统一端口的 URL map 中，但默认返回 `503 PRESERVED_CORE_API_DISABLED`。
- qB 与 Emby 手动动作仍由各自的确认、目标复查和冷却保护；只读验收阶段不得调用。

## 公开 API

公开兼容层以 `app/discover_compat_runtime.py`、`app/subscription_compat_runtime.py` 和 `app/contract_mapping.py` 为边界：

- `/api/discover/*`：发现、趋势、搜索和资源搜索。
- `/api/subscriptions/*`：唯一台账、配置、详情、日历和受保护动作。
- `/api/media/*`：影院大厅与 Emby。
- `/api/qbittorrent/*`、`/api/torra/summary`、`/api/symedia/summary`；qB 摘要兼容保留原始计数，并新增可选共享 `assessment`，应用内同一客户端通过 5 秒线程安全单飞快照让首页、任务链和控制室复用相同 `lastCheckedAt/counts.active`，任务链和控制室按同一观察时间、900 秒窗口与优先级判断；Torra 摘要可选增加 `searchAutomation`，只读取正式 jobs、job detail 与 schedules 端点，单次批次只接受明确 `auto/rss` 模式，订阅级模式缺失时保持 `unsupported` 并阻断 RSS 优先调整预览；Symedia 摘要兼容保留原统计并新增七项能力证据和脱敏洗版摘要，只有可证明的成功评分替换进入替换计数；缺失状态仍返回 `evidence_insufficient`，界面显示“暂未确认”。`/api/symedia/secupload-handoff` 只返回交接水位、脱敏计数与最近状态，不公开路径、Torra 配置或凭据。
- `/api/tasks/chain`：订阅到入库的统一证据链。
- `/api/v2/tasks/summary`：返回唯一任务链、健康/身份/执行三维状态、兼容 `userCounts`、新 `outcomeCounts`、阶段和服务轻量摘要，支持 ETag 条件读取。
- `/api/v2/tasks/chains`：按 `chainId/targetKey` 合并重复来源，默认分页返回 20 条摘要；支持可重复 `outcomeState`、兼容 `userState`、可播放日期、健康状态、身份、增量时间及独立 `qbActive=1` 筛选，后者按 `qbControl.active` 在分页前保留所有当前下载器活跃链和 orphan qB 任务，不受媒体结果影响；顶层返回 `outcomeState/playableAt`，任务中心按“需要处理 / 处理中 / 已可播放 / 无需处理”消费新结果。
- `/api/v2/tasks/chains/:chainId`：按需返回单链阶段证据、artifact、原因、动作资格及可选 `pipelineFacts/pipelineOutcome`；完整聚合快照幂等写入本地资源事件账本，但不执行外部动作。
- `/api/v2/system-issues/secupload-failures`：从 Torra `recent_runs.result` 读取结构化成功/失败计数和可选 `failure_details`；公开响应只增加脱敏文件显示名、错误分类、可空重试次数、批次引用和 file-scope 115 事实。没有详情时只说明“本次运行没有文件级详情”，路径、错误原文和内部 ID 不返回。
- `/api/v2/calendar`：只读聚合追更播出日期与精确目标的六阶段事实，使用 `Asia/Shanghai` 并支持 ETag；默认排除未关联、自动来源和范围不明记录，显式 `includeUnlinked=1` 才读取高级项；规范范围 owner 可投影集级 qB、Symedia、STRM 和 Emby 历史时间，成功历史不因当前新鲜度过期而消失，只有当前 Emby 集级证据生成 `playable`；月摘要与完整轻量搜索索引共用 300 秒完整快照。
- `/api/v2/subscriptions/capabilities`：分别返回候选规则、服务端调度、真实候选运行/成功/错误、Asia/Shanghai 计划与 2 小时宽限，以及本地写入和 Torra 推送能力；线程心跳不代替候选运行，发现页据此显示追更确认文案。
- `/api/v2/discover/candidates`：分页读取未过期的独立发现候选，只返回海报、标题、TMDB 身份、季号和来源标签等白名单字段。
- `/api/v2/discover/candidates/:candidateId/follow-previews`、`/follows`：先只读复核候选、重复追更和写入能力，再以明确确认和幂等键创建人工追更；只有确认动作会进入现有 provider 链路。
- `/api/v2/subscriptions/candidate-migrations/*`：把历史追更按人工、下游归属、可迁候选和待复核四类只读预览；确认执行要求最新指纹、幂等键和 SQLite 备份，只迁移可安全识别的自动来源记录，不调用外部服务。
- `/api/qbittorrent/actions/:action/preview`：只读返回暂停/恢复动作资格、实际影响对象、跳过数量、禁止原因、确认要求、幂等键和冷却时间；浏览器提交任务 DTO 中的 40 位不透明引用，服务端从当前 qB 快照解析真实 hash，不调用 qB 写接口。
- `/api/qbittorrent/actions/:action`：执行前复查任务状态并校验可选预览幂等键，状态变化时拒绝旧确认；旧客户端真实 hash 输入继续兼容，但执行结果、错误和活动记录只返回脱敏公开引用。
- `/api/v2/home/summary`：纯读 SQLite 中七个独立首页模块缓存，不在 GET 内刷新或访问外部服务；空缓存也立即返回结构完整的 200 部分响应。后台单飞刷新每轮只读一次共享任务快照，并分别保存各模块的可靠值或失败状态；日期模块按 `Asia/Shanghai` 隔离。确认状态按 `unknown > partial > confirmed` 合并；下载完成未入库和追更缺集同时返回已确认数量与不重叠的暂未确认对象，追更条目读取失败计入未确认数量。
- `/api/v2/subscriptions/reconciliation`：只读对比 Fluxa 与 Torra，返回对账、兼容履约、健康、`torraFact` 与 `pipelineOutcome`；Torra completed 只表示获取目标满足，不写入或删除任一台账。
- `/api/v2/subscriptions/workbench`：分页返回追更工作台、五项能力状态、结构化确认进度、统一派生结果、对账摘要和可选海报补齐目标；Torra 入队、已提交待确认和只读对账 linked 分开表达，没有集级证据时返回“集数进度未确认”。
- `/api/v2/subscriptions/workbench` 生产读取只投影 SQLite 中最后可靠的完整工作台快照；后台每 60 秒单飞刷新，容器重启后仍可直接返回旧快照。响应可选增加 `generatedAt/confirmation/stale/refreshState/lastError`；失败保留旧值并只返回脱敏错误。`POST /api/v2/subscriptions/workbench/refresh-requests` 只提交后台刷新并返回 202，不等待 Torra 对账。
- `/api/v2/subscriptions/visual-backfills`：最多处理 100 个订阅 ID，只按明确 TMDB 身份补充空缺海报/背景；本地写入关闭时只返回视觉结果，开启时才补充已有本地记录；不创建仅 Torra 镜像。
- `/api/internal/nasemby-core/*`：已认证的只读诊断兼容路由。
- `/api/v2/subscriptions/:id/torra-push-*`：固定目标 Torra 的预览和受保护推送；提交响应不公开上游订阅 ID，兼容 `subscriptionId` 固定为空，linked 只由后续只读对账投影。
- `/api/v2/torra/subscription-sync/*`：Torra 已有订阅状态、只读预览、幂等确认导入和手动状态同步。
- `/api/v2/activity/logs`：读取或经确认清空统一脱敏活动日志；React 任务中心使用读取接口。
- `/api/v2/system/metrics`：缓存、白名单映射的系统指标。
- `/api/v2/rss-sources`、`/api/v2/rss-items`：私人 RSS 来源和本地种子库；支持订阅身份/类型/季号/年份精确筛选，以及兼容全库待复核、已关联追更待识别和未关联追更三种只读范围；已识别资源可从匹配订阅、精确订阅、发现候选和现有 TMDB 缓存补充可选媒体标题、年份与海报，并支持媒体标题搜索。电视剧标题候选不强制年份，未知季号只作为人工候选；卡片查询不访问外部服务，读取响应不含完整 RSS/下载地址或订阅原始 payload。
- `/api/v2/rss-items/identity-backfills`：管理员显式触发的本地有界身份回填，每批最多 200 条，不访问 PT 详情页或执行下载；摘要保留最近扫描、识别、冲突、未变化和剩余数量。
- 历史 RSS 范围修复仅允许管理员显式运行 `python -m app.admin repair-rss-scope --preview`，确认后使用 `--apply <preview-fingerprint>`；预览指纹漂移、备份/事务/审计失败会拒绝或整批回滚。该命令只重解析本地 RSS、归档安全候选匹配并保留原始条目，不调用 Torra、qB、115、Symedia 或其他外部写接口，也不会在启动时自动执行。
- `/api/v2/rss-matches`：读取本地候选、规范订阅/季集绑定和 Torra 规则影子评分；分组读取可把整组均因 `subscription_missing` 阻断的孤立候选归入 `needs_cleanup`，主评分范围排除该组但旧全量 `total` 保持兼容。POST 可为一个 RSS 搜索结果和明确观察单元建立幂等人工匹配，服务端复核身份、季集、首次下载时间及 Torra 归属，并只读当前规则评分。规则或证据不完整时返回“暂未确认”，不按零分处理；后续人工 Torra 分析和下载仍分别受独立闸门保护。
- `/api/v2/rss-matches/:id/exact-download-previews`：保留的单匹配兼容预检；普通页面使用产物级接口，避免多集种子重复操作。
- `/api/v2/rss-artifact-groups/:id/exact-download-previews`、`/exact-downloads`：先只读复核全覆盖冠军、严格高分、订阅身份、实时规则与基线、qB 状态、保存目录和下载器映射，并明确返回 qB 分类是否由 Torra 提供，再以 `previewToken + idempotencyKey` 确认一次 qB 添加。动作使用稳定事实收据、全局单飞、冷却与小时/每日限额；qB 暂未显现时保持 `submitted`，重启后只按审计标签补确认，不盲目新建任务。公开响应不含 RSS 下载地址、Passkey、内部路径或 Torra 原始 ID。
- `/api/v2/subscription-automation/settings`、`/api/v2/subscription-automation/bridge-summary`、`/api/v2/subscriptions/:id/quality-watch`：追更洗版全局与单条观察设置、生产桥接水位/收据摘要、暂停和恢复；默认 `follow_rss` 只监听并本地评分 RSS 候选，不按旧检查点触发 Torra 整订阅搜索。生产桥接 v4 支持 `off/shadow/apply`，首次影子水位永久保留，摘要只统计 v4 收据并继续保留 v3 及更早审计；可选 `missingFallbackEnabled` 开启可靠缺集的单订阅搜索兜底，显式 `fixed_window` 才保留高级兼容调度。
- `/api/v2/subscription-automation/baseline-initialization-previews`、`/api/v2/subscription-automation/baseline-initializations`：只从缓存任务快照和稳定 `resource_events` 生成持久预览，最多确认 200 个可靠历史目标；同批漂移全部回滚，真实历史时间决定进入观察或直接过期，不触发任何外部搜索或下载动作。
- `/api/v2/subscriptions/:id/torra-rewash-analyses`、`/api/v2/subscriptions/:id/torra-rewashes`、`/api/v2/rss-matches/:id/torra-rewash-analyses`、`/api/v2/rss-matches/:id/torra-rewashes`：人工异步分析与候选下载；服务端从观察单元和已完成分析动作读取 Torra ID/候选，不接受浏览器映射。
- `/api/v2/search`、`/api/v2/media/:mediaKey`：外部只读聚合本地追更、已识别 RSS、任务、当月日历和 Emby TMDB 索引；本地无结果时才使用 TMDB 只读补充。无 TMDB 的本地任务保留空 `tmdbId`、公开 `chainId` 和任务深链，不伪造作品详情地址；以媒体键或 TMDB 身份也可定位仅 Emby 候选。响应不返回路径、Hash、外部原始 ID 或不安全播放直链。
- `/api/v2/subscriptions/:id/moviepilot-previews`、`/api/v2/subscriptions/:id/moviepilot-pushes`：阶段 7 人工备用预览与同步推送；只复用 NasEmby MoviePilot 门面，不返回外部订阅 ID、URL、Token 或原始响应。
- `/api/v2/automation-actions/:id`：从 SQLite 读取统一外部动作状态，只返回哈希化 job 引用和安全结果摘要。
- `/api/v2/integrations/*`、`/api/v2/acquisition/cloud/*` 和云盘策略路由继续保留，当前 React 不调用延期动作。
- `/mineradio/embed`、`/mineradio/*`。

仅 Torra 条目对浏览器使用 `torra:<10 位 SHA-256 摘要>` 形式的不透明公开 ID；质量观察、RSS 匹配、调度游标和操作台账内部统一使用 `torra:<远端 ID>` 规范键。质量观察、人工分析和 RSS 单条匹配在服务端依据当前 Torra 只读快照反解到唯一远端条目；未命中或摘要冲突会明确失败，公开响应和活动记录不回传原始 Torra ID。

质量观察生产桥接 v4 同时解析 Fluxa 本地追更和任务链本轮已经读取的 Torra 只读追更。绑定只接受唯一一致的 Torra ID、TMDB、媒体类型和季号，本地订阅优先，Torra-only 使用规范内部键但不写入本地订阅表；旧只读镜像即使仍以公开短键保存在追更台账，也只能作为兼容展示载体。完成的剧集季包只有在唯一所有权、标题无集号且订阅身份已确认时，才按 Hash 读取并缓存 qB 文件清单，仅从已选中且完成的文件投影明确集号；文件名不参与作品身份判断。激活水位后的精确 Symedia 集级成功可以直接建立入库基线，历史事实仍只进入初始化流程。

应用启动会在 Private RSS 与质量观察 schema 初始化后、桥接器和调度器注册前运行一次规范键迁移。迁移先只读规划；没有旧短键时返回 0 且不备份，有待迁移时使用 SQLite backup API 备份并校验，再在单一 `BEGIN IMMEDIATE` 中重新核验计划并原子迁移观察单元、RSS 匹配、操作记录、已登记 JSON 路径和调度游标。摘要碰撞、双键单元、未知 JSON 引用、唯一键冲突、备份或审计失败都会整批回滚并阻止服务启动；冲突报告只包含脱敏引用。

任务链公开 DTO 同样不会返回 qB hash、Symedia 内部 ID、artifact 原键、外部 job、路径或 URL。qB 引用使用确定性的 40 位 SHA-256 摘要，以保持现有前端字段形状；暂停/恢复动作必须从本次实时 qB 快照唯一反解，引用不存在或冲突时拒绝执行。`chainId`、`mediaKey` 与 `targetKey` 仍是 Fluxa 本地聚合和深链定位键。

47 条冻结 v1 契约见项目根 `docs/contracts/http-api-contract-v1.json`；75 条新增能力见 `http-api-contract-v2.json`。浏览器公开响应经过白名单映射；内部诊断路由保留 NasEmby 原始字段，仍受整站认证保护。

## 唯一订阅台账

订阅写入只使用 `db/media_control_center.sqlite3`。首次发现旧 JSON 时先备份，在同目录临时 SQLite 中导入并逐字段复核，再原子替换正式库；运行时不再写回 JSON。失败迁移不会发布半成品数据库。

分类与改季直接更新同一条订阅，不创建 Node 副本，也不会因为字段修改排队外部 provider。保存订阅继续调用 NasEmby 原保存函数；外部后处理仍受配置和总开关约束。

## 测试

```powershell
python -m unittest discover -s tests -t . -v
```

测试使用临时台账、隔离的临时活动日志和模拟客户端，不连接真实服务执行写操作。保留接口只在模拟测试中显式开启；Mineradio 注入片段继续使用冻结的 SHA-256 快照保护视觉桥接基线。

回归测试全部使用临时台账、临时活动日志和模拟客户端，不连接真实外部服务。RSS 覆盖订阅绑定、范围包单一所有权、Torra 规则评分、可靠基线、跨批次冠军、产物级只读预检、人工精准 qB 提交、稳定事实收据、全局单飞、冷却和小时/每日限额、qB 标签重启恢复及公开脱敏；完整数量以实际测试发现结果为准，不在文档中维护易过期的固定数字。

RSS 身份端到端验收使用临时 SQLite 覆盖结构化 TMDB、简介 IMDb 链接、唯一追更匹配和多候选冲突四类固定样本，不写入正式 RSS 台账。

RSS 解析回归已加入四个真实结构的完全脱敏夹具：M-Team 的 `tests/fixtures/mteam_rss_sanitized.xml`、HDHome 的 `tests/fixtures/hdhome_rss_sanitized.xml`、织梦的 `tests/fixtures/zmpt_rss_sanitized.xml` 和青蛙的 `tests/fixtures/qingwa_rss_sanitized.xml`，覆盖 RSS 2.0、电影/剧集、多版本、单集/整季包、文件大小、`enclosure`、`720p/1080i/1080p/2160p`、Blu-ray/Remux、WEB-DL、H.264/H.265、HDR、Atmos 和 TrueHD 版本摘要。四个夹具还会经过假 HTTP 响应、收集器、临时 SQLite 和公共脱敏查询的完整回归，已满足当前版本；夹具只使用 `tracker.example` 地址，不保存真实签名、UID、详情或下载 URL，也不访问 enclosure。

当前源码为 schema version 5，新增独立 `discover_candidates` 与 `candidate_migration_runs`，榜单和全球日播刷新只更新候选池；`resource_chains`、`resource_artifacts` 和 `resource_events` 继续保留。候选转追更和历史污染迁移由 P0.5 后续小阶段接入，当前不会自动执行外部动作。

## 持久目录

- `data/`：配置、活动日志和运行状态。
- `db/`：SQLite 订阅/RSS 台账、迁移报告和缓存。
- `upload/`：上传、会话或临时文件。

这些目录不能提交真实数据。升级和回滚必须整体备份，不能手工合并订阅文件。
