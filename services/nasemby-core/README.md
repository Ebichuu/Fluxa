# Python 统一后端

媒体控制中心唯一生产后端。目录名称沿用迁移期的 `nasemby-core`，避免为重命名制造大范围导入改动；运行时已经同时承载整站认证、React 静态托管、Mineradio、订阅/发现、外部适配器和任务链，不再作为独立侧车服务。

## 职责

- Flask 应用工厂、统一请求 ID、JSON 错误和整站访问保护。
- React `dist`、SPA 回退、Mineradio 原始资源和桥接页。
- NasEmby 原发现、JustWatch 海外流媒体、订阅、日历、资源规则和调度源码。
- SQLite 唯一订阅台账、Torra 已有订阅单向镜像、旧 JSON 一次性迁移、私人 PT RSS 本地种子索引和活动观察窗口匹配。
- 115、Telegram、HDHive / pansou、provider 等原核心能力与接口调用关系。
- Torra 固定目标推送，以及追更洗版分析、候选下载、job 状态解析、按集 Emby 基准、SQLite 幂等/租约和脱敏审计。
- 30 秒缓存的 NAS 系统指标，以及统一脱敏、可筛选的 v2 活动日志。
- 115、Telegram、HDHive / pansou 和 MoviePilot 的 v2 细分接口继续保留；MoviePilot 阶段 7 已增加默认关闭的人工备用预览/推送，其他能力延期。
- Emby、qBittorrent、Torra、Symedia 的服务端适配和凭据隔离。
- 统一任务链、qB 暂停/恢复和证据驱动的 Emby 刷新。
- 六阶段独立事实契约与统一结果派生：`torra/qb/cloud115/symedia/strm/emby` 分别保存；P0.2 已接入 Torra、qB、Symedia 与 Emby 明确证据，115 分类摘要不能绑定媒体时及 STRM 独立来源未接入时保持 `unknown + missing`。旧页面暂不消费新结果，旧状态只由六阶段事实作兼容投影。
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
- 订阅调度器只在显式开启时启动；发现缓存和关闭状态检查不会替代订阅调度。
- 追更洗版协调器只在 `MCC_TORRA_QUALITY_WATCH_ENABLED=true` 时启动，并继续要求 SQLite 中的追更设置开启；默认不创建线程或调用 Torra。
- 追更洗版候选下载还要求独立的 `MCC_TORRA_REWASH_DOWNLOAD_ENABLED=true`、人工确认和服务端已完成分析动作；打开分析闸门不会自动下载。
- MoviePilot 人工备用还要求 `MCC_MOVIEPILOT_BACKUP_ENABLED=true`、观察单元全部 `observation_expired`、Torra/qB 预检通过和明确确认；已有订阅只重搜，没有订阅才复用创建逻辑，默认不接入自动调度。
- NasEmby 的 115、Telegram、HDHive、缓存预热和 provider 核心 API 保留在统一端口的 URL map 中，但默认返回 `503 PRESERVED_CORE_API_DISABLED`。
- qB 与 Emby 手动动作仍由各自的确认、目标复查和冷却保护；只读验收阶段不得调用。

## 公开 API

公开兼容层以 `app/discover_compat_runtime.py`、`app/subscription_compat_runtime.py` 和 `app/contract_mapping.py` 为边界：

- `/api/discover/*`：发现、趋势、搜索和资源搜索。
- `/api/subscriptions/*`：唯一台账、配置、详情、日历和受保护动作。
- `/api/media/*`：影院大厅与 Emby。
- `/api/qbittorrent/*`、`/api/torra/summary`、`/api/symedia/summary`。
- `/api/tasks/chain`：订阅到入库的统一证据链。
- `/api/v2/tasks/summary`：返回唯一任务链、健康/身份/执行三维状态、兼容 `userCounts`、新 `outcomeCounts`、阶段和服务轻量摘要，支持 ETag 条件读取。
- `/api/v2/tasks/chains`：按 `chainId/targetKey` 合并重复来源，默认分页返回 20 条摘要；支持可重复 `outcomeState`、兼容 `userState`、可播放日期、健康状态、身份和增量时间筛选，顶层返回 `outcomeState/playableAt`；任务中心按“需要处理 / 处理中 / 已可播放 / 无需处理”消费新结果。
- `/api/v2/tasks/chains/:chainId`：按需返回单链阶段证据、artifact、原因、动作资格及可选 `pipelineFacts/pipelineOutcome`；完整聚合快照幂等写入本地资源事件账本，但不执行外部动作。
- `/api/v2/calendar`：只读聚合追更播出日期与任务链的获取、入库证据，使用 `Asia/Shanghai` 并支持 ETag；月摘要与完整轻量搜索索引共用 300 秒完整快照，日期详情按需读取。
- `/api/v2/subscriptions/capabilities`：返回本地写入、Torra 推送和调度器真实运行状态，发现页据此显示追更确认文案。
- `/api/qbittorrent/actions/:action/preview`：只读返回暂停/恢复动作资格、实际影响对象、跳过数量、禁止原因、确认要求、幂等键和冷却时间；浏览器提交任务 DTO 中的 40 位不透明引用，服务端从当前 qB 快照解析真实 hash，不调用 qB 写接口。
- `/api/qbittorrent/actions/:action`：执行前复查任务状态并校验可选预览幂等键，状态变化时拒绝旧确认；旧客户端真实 hash 输入继续兼容，但执行结果、错误和活动记录只返回脱敏公开引用。
- `/api/v2/home/summary`：基于 `pipelineOutcome` 和调度器心跳生成首页今日结论；媒体异常、辅助能力提醒、处理中与当日可播放分别统计，自动恢复中的明确秒传失败计入处理中；关键服务不可验证时不返回绿色正常，无法核实的归档与下载统计返回 `null` 而不是伪造 `0`。
- `/api/v2/subscriptions/reconciliation`：只读对比 Fluxa 与 Torra，按对账、履约、健康三个维度返回差异，不写入或删除任一台账。
- `/api/v2/subscriptions/workbench`：分页返回追更工作台、五项能力状态、对账摘要和可选海报补齐目标。
- `/api/v2/subscriptions/visual-backfills`：最多处理 100 个订阅 ID，只按明确 TMDB 身份补充空缺海报/背景；本地写入关闭时只返回视觉结果，开启时才补充已有本地记录；不创建仅 Torra 镜像。
- `/api/internal/nasemby-core/*`：已认证的只读诊断兼容路由。
- `/api/v2/subscriptions/:id/torra-push-*`：固定目标 Torra 的预览和受保护推送。
- `/api/v2/torra/subscription-sync/*`：Torra 已有订阅状态、只读预览、幂等确认导入和手动状态同步。
- `/api/v2/activity/logs`：读取或经确认清空统一脱敏活动日志；React 任务中心使用读取接口。
- `/api/v2/system/metrics`：缓存、白名单映射的系统指标。
- `/api/v2/rss-sources`、`/api/v2/rss-items`：私人 RSS 来源和本地种子库；支持订阅身份/类型/季号/年份精确筛选，电视剧标题候选不强制年份，未知季号只作为人工候选，读取响应不含完整 RSS/下载地址。
- `/api/v2/rss-items/identity-backfills`：管理员显式触发的本地有界身份回填，每批最多 200 条，不访问 PT 详情页或执行下载；摘要保留最近扫描、识别、冲突、未变化和剩余数量。
- `/api/v2/rss-matches`：读取本地 `candidate` 与后续状态；POST 可为一个 RSS 搜索结果和明确观察单元建立幂等人工匹配，服务端复核身份、季集、基线时间及 Torra 归属；该步只写本地运行证据，不依赖订阅配置写闸门，后续 Torra 分析和下载仍分别受独立闸门保护。
- `/api/v2/subscription-automation/settings`、`/api/v2/subscriptions/:id/quality-watch`：追更洗版全局与单条观察设置、暂停和恢复。
- `/api/v2/subscriptions/:id/torra-rewash-analyses`、`/api/v2/subscriptions/:id/torra-rewashes`、`/api/v2/rss-matches/:id/torra-rewash-analyses`、`/api/v2/rss-matches/:id/torra-rewashes`：人工异步分析与候选下载；服务端从观察单元和已完成分析动作读取 Torra ID/候选，不接受浏览器映射。
- `/api/v2/search`、`/api/v2/media/:mediaKey`：外部只读聚合本地追更、已识别 RSS、任务、当月日历和 Emby TMDB 索引；本地无结果时才使用 TMDB 只读补充。无 TMDB 的本地任务保留空 `tmdbId`、公开 `chainId` 和任务深链，不伪造作品详情地址；以媒体键或 TMDB 身份也可定位仅 Emby 候选。响应不返回路径、Hash、外部原始 ID 或不安全播放直链。
- `/api/v2/subscriptions/:id/moviepilot-previews`、`/api/v2/subscriptions/:id/moviepilot-pushes`：阶段 7 人工备用预览与同步推送；只复用 NasEmby MoviePilot 门面，不返回外部订阅 ID、URL、Token 或原始响应。
- `/api/v2/automation-actions/:id`：从 SQLite 读取统一外部动作状态，只返回哈希化 job 引用和安全结果摘要。
- `/api/v2/integrations/*`、`/api/v2/acquisition/cloud/*` 和云盘策略路由继续保留，当前 React 不调用延期动作。
- `/mineradio/embed`、`/mineradio/*`。

仅 Torra 条目对浏览器使用 `torra:<10 位 SHA-256 摘要>` 形式的不透明公开 ID。质量观察、人工分析和 RSS 单条匹配在服务端依据当前 Torra 只读快照反解到唯一远端条目；未命中或摘要冲突会明确失败，公开响应和活动记录不回传原始 Torra ID。

任务链公开 DTO 同样不会返回 qB hash、Symedia 内部 ID、artifact 原键、外部 job、路径或 URL。qB 引用使用确定性的 40 位 SHA-256 摘要，以保持现有前端字段形状；暂停/恢复动作必须从本次实时 qB 快照唯一反解，引用不存在或冲突时拒绝执行。`chainId`、`mediaKey` 与 `targetKey` 仍是 Fluxa 本地聚合和深链定位键。

47 条冻结 v1 契约见项目根 `docs/contracts/http-api-contract-v1.json`；60 条新增能力见 `http-api-contract-v2.json`。浏览器公开响应经过白名单映射；内部诊断路由保留 NasEmby 原始字段，仍受整站认证保护。

## 唯一订阅台账

订阅写入只使用 `db/media_control_center.sqlite3`。首次发现旧 JSON 时先备份，在同目录临时 SQLite 中导入并逐字段复核，再原子替换正式库；运行时不再写回 JSON。失败迁移不会发布半成品数据库。

分类与改季直接更新同一条订阅，不创建 Node 副本，也不会因为字段修改排队外部 provider。保存订阅继续调用 NasEmby 原保存函数；外部后处理仍受配置和总开关约束。

## 测试

```powershell
python -m unittest discover -s tests -t . -v
```

测试使用临时台账、隔离的临时活动日志和模拟客户端，不连接真实服务执行写操作。保留接口只在模拟测试中显式开启；Mineradio 注入片段继续使用冻结的 SHA-256 快照保护视觉桥接基线。

当前共 476 项回归测试。SQLite、RSS、Torra、MoviePilot 备用、网盘、日历时间线、全局作品搜索和系统指标测试全部使用临时台账、临时活动日志与模拟函数，不连接真实外部服务；覆盖默认闸门、脱敏、原子迁移、Torra 镜像幂等与公开哈希 ID、旧 Torra 冲突键公开投影、六阶段任务事实、六来源适配、单向兼容投影、Emby 集级分页索引与结果派生、任务公开引用与 qB 动作反解、任务用户状态、无 TMDB 任务深链、首页关注项、北京时间自然日、日历完整索引、RSS 单条安全匹配与匹配级下载确认、追更海报补齐、qB 安全动作、自动化窗口、租约回收终态和完整幂等请求绑定。

RSS 身份端到端验收使用临时 SQLite 覆盖结构化 TMDB、简介 IMDb 链接、唯一追更匹配和多候选冲突四类固定样本，不写入正式 RSS 台账。

RSS 解析回归已加入四个真实结构的完全脱敏夹具：M-Team 的 `tests/fixtures/mteam_rss_sanitized.xml`、HDHome 的 `tests/fixtures/hdhome_rss_sanitized.xml`、织梦的 `tests/fixtures/zmpt_rss_sanitized.xml` 和青蛙的 `tests/fixtures/qingwa_rss_sanitized.xml`，覆盖 RSS 2.0、电影/剧集、多版本、单集/整季包、文件大小、`enclosure`、`720p/1080i/1080p/2160p`、Blu-ray/Remux、WEB-DL、H.264/H.265、HDR、Atmos 和 TrueHD 版本摘要。四个夹具还会经过假 HTTP 响应、收集器、临时 SQLite 和公共脱敏查询的完整回归，已满足当前版本；夹具只使用 `tracker.example` 地址，不保存真实签名、UID、详情或下载 URL，也不访问 enclosure。

当前源码为 schema version 4，新增 `resource_chains`、`resource_artifacts` 和 `resource_events`；本地硬化候选镜像 `media-control-center:sqlite-rss-hardened` 仍是上一阶段的 schema v2。该镜像的隔离冒烟已确认 WAL、FTS5、RSS 外部访问闸门、无 Node 运行层和容器重建持久化，本轮代码尚未重建候选镜像。

## 持久目录

- `data/`：配置、活动日志和运行状态。
- `db/`：SQLite 订阅/RSS 台账、迁移报告和缓存。
- `upload/`：上传、会话或临时文件。

这些目录不能提交真实数据。升级和回滚必须整体备份，不能手工合并订阅文件。
