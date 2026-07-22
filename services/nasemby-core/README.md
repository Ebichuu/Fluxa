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
- `/api/v2/tasks/summary`：返回唯一任务链、健康/身份/执行三维状态、阶段和服务轻量摘要，支持 ETag 条件读取。
- `/api/v2/tasks/chains`：按 `chainId/targetKey` 合并重复来源，默认分页返回 20 条摘要；支持健康状态、身份和增量时间筛选。
- `/api/v2/tasks/chains/:chainId`：按需返回单链阶段证据、artifact、原因和动作资格；完整聚合快照幂等写入本地资源事件账本，但不执行外部动作。
- `/api/v2/calendar`：只读聚合追更播出日期与任务链的获取、入库证据，使用 `Asia/Shanghai` 并支持 ETag。
- `/api/v2/subscriptions/capabilities`：返回本地写入、Torra 推送和调度器真实运行状态，发现页据此显示追更确认文案。
- `/api/qbittorrent/actions/:action/preview`：只读返回暂停/恢复动作资格、实际影响对象、跳过数量、禁止原因、确认要求、幂等键和冷却时间；不会调用 qB 写接口。
- `/api/qbittorrent/actions/:action`：执行前复查任务状态并校验可选预览幂等键，状态变化时拒绝旧确认；执行结果再次读取 qB，并写入脱敏活动记录。
- `/api/v2/home/summary`：基于任务链和调度器心跳生成首页今日结论；证据缺失、调度未启动或服务不可验证时不返回绿色正常。
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
- `/api/v2/rss-matches`：只读本地 `candidate` 与后续状态；双闸门开启时后台可创建一次性 Torra 分析动作，但不把标题匹配当作版本质量结论，也不自动下载候选。
- `/api/v2/subscription-automation/settings`、`/api/v2/subscriptions/:id/quality-watch`：追更洗版全局与单条观察设置、暂停和恢复。
- `/api/v2/subscriptions/:id/torra-rewash-analyses`、`/api/v2/subscriptions/:id/torra-rewashes`、`/api/v2/rss-matches/:id/torra-rewash-analyses`：人工异步分析与候选下载；服务端从观察单元和已完成分析动作读取 Torra ID/候选，不接受浏览器映射。
- `/api/v2/subscriptions/:id/moviepilot-previews`、`/api/v2/subscriptions/:id/moviepilot-pushes`：阶段 7 人工备用预览与同步推送；只复用 NasEmby MoviePilot 门面，不返回外部订阅 ID、URL、Token 或原始响应。
- `/api/v2/automation-actions/:id`：从 SQLite 读取统一外部动作状态，只返回哈希化 job 引用和安全结果摘要。
- `/api/v2/integrations/*`、`/api/v2/acquisition/cloud/*` 和云盘策略路由继续保留，当前 React 不调用延期动作。
- `/mineradio/embed`、`/mineradio/*`。

47 条冻结 v1 契约见项目根 `docs/contracts/http-api-contract-v1.json`；53 条新增能力见 `http-api-contract-v2.json`。浏览器公开响应经过白名单映射；内部诊断路由保留 NasEmby 原始字段，仍受整站认证保护。

## 唯一订阅台账

订阅写入只使用 `db/media_control_center.sqlite3`。首次发现旧 JSON 时先备份，在同目录临时 SQLite 中导入并逐字段复核，再原子替换正式库；运行时不再写回 JSON。失败迁移不会发布半成品数据库。

分类与改季直接更新同一条订阅，不创建 Node 副本，也不会因为字段修改排队外部 provider。保存订阅继续调用 NasEmby 原保存函数；外部后处理仍受配置和总开关约束。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试使用临时台账、隔离的临时活动日志和模拟客户端，不连接真实服务执行写操作。保留接口只在模拟测试中显式开启；Mineradio 注入片段继续使用冻结的 SHA-256 快照保护视觉桥接基线。

当前共 285 项回归测试。SQLite、RSS、Torra、MoviePilot 备用、网盘、日历时间线和系统指标测试全部使用临时台账与模拟函数，不连接真实外部服务；覆盖默认闸门、脱敏、原子迁移、Torra 镜像幂等、任务身份/执行状态拆分、正常保护一致性、用户/技术原因分层、首页季集定位、日历未知/逾期判定、RSS 精确搜索与身份回填、追更海报补齐、qB 安全动作和自动化窗口。

RSS 身份端到端验收使用临时 SQLite 覆盖结构化 TMDB、简介 IMDb 链接、唯一追更匹配和多候选冲突四类固定样本，不写入正式 RSS 台账。

RSS 解析回归已加入四个真实结构的完全脱敏夹具：M-Team 的 `tests/fixtures/mteam_rss_sanitized.xml`、HDHome 的 `tests/fixtures/hdhome_rss_sanitized.xml`、织梦的 `tests/fixtures/zmpt_rss_sanitized.xml` 和青蛙的 `tests/fixtures/qingwa_rss_sanitized.xml`，覆盖 RSS 2.0、电影/剧集、多版本、单集/整季包、文件大小、`enclosure`、`720p/1080i/1080p/2160p`、Blu-ray/Remux、WEB-DL、H.264/H.265、HDR、Atmos 和 TrueHD 版本摘要。四个夹具还会经过假 HTTP 响应、收集器、临时 SQLite 和公共脱敏查询的完整回归，已满足当前版本；夹具只使用 `tracker.example` 地址，不保存真实签名、UID、详情或下载 URL，也不访问 enclosure。

当前源码为 schema version 4，新增 `resource_chains`、`resource_artifacts` 和 `resource_events`；本地硬化候选镜像 `media-control-center:sqlite-rss-hardened` 仍是上一阶段的 schema v2。该镜像的隔离冒烟已确认 WAL、FTS5、RSS 外部访问闸门、无 Node 运行层和容器重建持久化，本轮代码尚未重建候选镜像。

## 持久目录

- `data/`：配置、活动日志和运行状态。
- `db/`：SQLite 订阅/RSS 台账、迁移报告和缓存。
- `upload/`：上传、会话或临时文件。

这些目录不能提交真实数据。升级和回滚必须整体备份，不能手工合并订阅文件。
