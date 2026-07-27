# Python 统一后端设计

## 1. 定位

本模块是媒体控制中心唯一生产后端。它保留 NasEmby 可运行源码作为订阅与发现业务基础，同时吸收原中控边界能力：认证、React 静态托管、Mineradio 桥接、外部服务适配和统一任务链。

目录名称继续使用 `services/nasemby-core`，只是为了保持 Python 导入和补丁来源稳定；部署中不存在第二个 Core 服务。

## 2. 架构

```text
浏览器
  → 单端口 8987
  → Gunicorn（1 worker / gthread / 4 threads）
  → Flask create_app()
      ├─ 管理员认证、会话、请求 ID、错误脱敏
      ├─ React dist 与 SPA 回退
      ├─ Mineradio 原始资源与 postMessage 桥接
      ├─ 公开 API 兼容层
      │   ├─ 发现与资源搜索
      │   └─ 订阅、详情、日历与配置
      ├─ NasEmby 原业务函数与唯一台账
      ├─ Emby / qB / Torra / Symedia / 任务链
      └─ Torra 固定推送 / 系统指标 / 延期保留的网盘模块
```

Node.js 只存在于 Docker `web-build` 阶段。生产镜像不复制 Node、npm、服务端 JavaScript 或 `dist-server`。

## 3. 应用组装

`app/main.py:create_app()` 按以下顺序注册：

1. HTTP 请求 ID和统一错误处理。
2. 整站访问保护。
3. Mineradio、Emby、qB、Torra、Symedia、刷新与任务链运行时。
4. 发现和订阅公开兼容层。
5. v2 Torra 推送、追更洗版设置/动作、全局作品搜索/生命周期、系统指标、集成与延期保留的网盘运行时。
6. React 静态目录与 SPA 回退。
7. NasEmby 原 Blueprint，供 `/api/status`、`/api/health` 和源码兼容保留。
8. 保留核心接口隔离守卫。

公开兼容路由先注册，保证 React 使用的路径命中白名单映射。原 Blueprint 中未列入公开契约的 115、Telegram、HDHive、缓存预热和 provider 核心入口继续保留，默认由守卫返回 `503 PRESERVED_CORE_API_DISABLED`。

## 4. API 兼容层

冻结契约共 47 条，机器清单位于 `docs/contracts/http-api-contract-v1.json`。

### 公开浏览器响应

`app/contract_mapping.py` 只映射 React 明确消费的字段：

- 订阅：camelCase 身份、季号、进度、分类和来源。
- 详情：TMDB 元数据、演员、季集和明确要求展示的入库路径。
- 日历：日期、季集、进度与入库证据。
- 发现：来源、TMDB 身份、海报、评分和分页。
- 资源搜索：候选、来源、季集覆盖和脱敏错误。
- 全局作品搜索：追更、已识别 RSS、任务、完整月历索引、Emby 身份和 TMDB 只读补充，以及可分享的站内深链。

未知字段、原始上游包络、Token、Cookie 和异常正文不会透传浏览器。

TMDB 发现、全球日播和海外流媒体同时支持 v3 API Key 与 v4 Bearer Token。未配置凭据时，公开浏览接口返回 `200 + configured:false`；上游拒绝凭据时返回稳定的 `TMDB_AUTH_FAILED`，限流返回 `TMDB_RATE_LIMITED`，浏览器只显示可操作的中文提示，不暴露凭据或上游响应正文。

`GET /api/image` 是旧 Core 守卫中唯一公开的只读图片例外。它仍受固定外部域名白名单、8 MiB 响应上限和图片魔数校验约束；HTML 反爬页及其他非图片内容不会透传，其他旧 Core 接口继续由总守卫关闭。

### 内部诊断响应

`/api/internal/nasemby-core/*` 保留原 NasEmby 数据形状，便于核对源码行为。它们仍受会话认证保护，不是外部服务间的第二个网络层。

### 作品聚合与 Torra 公开标识

`media_search_runtime.py` 只读合并本地追更、已识别 RSS、任务、当前完整月历缓存和 Emby TMDB 索引，并按标准 `mediaKey` 去重。本地没有候选时才调用既有 TMDB 只读客户端，失败降级为空且不写发现缓存。没有 TMDB 的本地任务使用独立目录键参与搜索，对外保留空 `tmdbId`、公开 `chainId` 和任务深链，不能进入只接受 `movie:tmdbId / tv:tmdbId` 的作品详情接口。用户结果只聚合 `pipelineOutcome`，下载、115、整理和 Emby 生命周期只采纳当前 verified 的独立事实；日历兼容字段不反推任务结果，电视剧作品级 Emby 索引不生成可播放。

Torra-only 条目不得把远端主键当作浏览器 ID。`subscription_reconciliation_runtime.py` 使用远端 ID 的 SHA-256 前 10 位生成 `torra:<摘要>`，质量观察、人工动作和 RSS 匹配收到公开订阅/观察单元 ID 后，从当前 Torra 只读列表解析唯一内部条目。无匹配返回不存在，摘要碰撞返回冲突；公开 DTO、动作摘要和日志始终保留公开键，不返回原始远端 ID。该解析不创建本地镜像，也不改变 Torra。

任务链内部快照继续保存 qB hash、Torra/Symedia 原始 ID 与 artifact 键，供归属、事件台账和动作执行使用；HTTP 路由在响应边界经过独立白名单 presenter。qB 对浏览器使用确定性的 40 位 SHA-256 引用，动作服务每次从实时 qB 快照唯一反解并只把真实 hash 交给 qB 客户端；旧 hash 输入仅作兼容，任何预览、执行结果、错误和活动记录都不回显原值。用户文本还会过滤 URL、主机端口、Windows/Unix/UNC 路径、凭据键值和外部 job 标识。

## 5. 订阅所有权

唯一台账：

- `db/media_control_center.sqlite3`

旧 `db/discover_subscription_items.json` 和 `db/discover_subscriptions.json` 只作为首次迁移输入和备份，不再承担运行时写入。迁移先克隆当前共享 SQLite 到同目录临时库，在临时库导入并逐字段复核配置、订阅 payload 和 key；全部一致后才原子替换正式库。失败只记录脱敏报告，不发布半成品。

用户从媒体控制中心保存订阅时，Python 把 React 平铺字段转换为 NasEmby 原 `item`，再调用 `save_subscription_item()`。列表、详情、日历、任务链和调度都读取同一 SQLite 台账。

分类和改季是台账字段更新：

- 分类写 `media_category`。
- 改季同步 `target_season`、`current_season`、`latest_season`、`season_number` 和 `season_name`。

这两类编辑不调用保存订阅后的 provider 队列，避免普通字段修改意外触发外部获取。

不导入外部 NasEmby 台账；`POST /api/subscriptions/import-nasemby` 只保留路径并明确返回禁用。

## 6. 获取策略

- 默认主通道：PT / Torra。
- 自动云盘兜底：关闭。
- `cloud_then_pt`：不支持。
- Torra 推送前必须同时满足：稳定 TMDB 身份、八分类、非空下载器 ID、非空分类保存路径、在线查重完成和推送开关开启。
- 推送载荷中的 `downloader_id` 与 `save_path` 不允许为空。
- 网盘第二通道独立于旧 `resource_then_pt`：全局和订阅级开关默认关闭，PT 已有关联证据时禁止网盘转存。
- 候选预览只返回 15 分钟有效的随机候选 ID 与脱敏摘要；完整链接和密码只留在单 worker 内存。
- 单条转存要求明确确认、12-128 字符幂等键、60 秒订阅冷却，并在执行前重新读取 Torra、qB、Symedia 和 Emby 证据。

上述网盘运行时当前只作为延期源码和契约基线保留，React 不调用。当前普通订阅动作只允许固定推送 Torra；Symedia 只读取 115 后整理与入库证据。MoviePilot 阶段 7 只提供观察到期后的人工备用入口，自动补齐仍不实施。

Torra v2 推送要求确认、12–128 字符幂等键、60 秒订阅冷却，并从唯一台账重建条目后复用现有分类、保存路径、下载器 ID 和在线查重逻辑。

Torra 追更洗版动作使用 schema version 3 的 `quality_watch_units`、`provider_actions` 和 `scheduler_state`。观察窗口按电影或季集隔离；动作领取使用短事务、租约和幂等键，已经持久化 `external_job_id` 的动作在重启后只能继续查询原 job。质量适配器复用 Torra 读取客户端的 Token/账号密码认证和 401/403 单次重登，只接受已核对的 `{success,data}` 包络与五种 job 状态。分析结果仅按 Torra 的 `is_upgrade`、`meta_weight_score` 和 `library_meta_weight_score` 选择每行最高正分差候选，不建立第二套评分器。

质量观察运行时采用双证据：现有任务链的 `download=done + evidence=verified` 只负责证明首个版本已下载；Torra 订阅行的 `library_file_names` 或逐集 `library_episode_files` 才证明 Torra 已能读取 Emby 当前文件并允许写入 `baseline_ready_at`。系列级 `embyIndexed` 汇总不能代替逐集基准。电视剧必须有明确季集，历史扫描默认不创建观察单元；qB 证据可以先建立等待单元，Torra 后续关联时补写 ID。窗口建立后不因重复证据或新版本延长，目标已达也只作用于明确的电影或单集。

RSS 匹配只在新条目写入时运行，并与 `rss_subscription_matches` 的 `candidate` 写入共用同一 SQLite 事务。候选范围只包含仍在截止时间内的 `observing_upgrade / search_due / search_running` 单元；标准媒体身份优先，其次使用订阅标题和别名，再校验媒体类型、年份、季和明确集号。多个不同身份同时命中时全部放弃，连续集可以分别命中多个活动单元。发布时间早于 `baseline_ready_at`、历史导入、过期窗口和不可靠季集都不创建记录；版本摘要不参与质量高低判断。

RSS `candidate` 只有在 `MCC_PRIVATE_RSS_ENABLED` 与 `MCC_TORRA_QUALITY_WATCH_ENABLED` 均开启、SQLite 设置允许、观察窗口有效、Torra 非运行/变更中、qB 无同单元活动任务且冷却与小时/每日限额均通过时，才领取固定幂等动作 `rss-rewash-analysis:{match_id}`。外部调用在 SQLite 事务外执行；保存 `external_job_id` 后匹配进入 `triggered`，重启或租约恢复后只续查原 job。无升级结果进入 `ignored`，有升级结果保持 `triggered` 并只保存脱敏摘要，当前阶段不自动下载；失败或取消可回到 `candidate` 展示，但自动路径不会用同一固定动作无限重提。

有限主动兜底使用独立单线程协调器和同一 `provider_actions` 台账。24 小时窗口默认在 12/24 小时检查，48 小时窗口默认在 12/24/48 小时检查；自定义时间点最早 30 分钟，严格递增，窗口截止点始终保留最后一次检查。实际执行时间增加按单元和时间点计算的 0–15 分钟确定性错峰，每轮默认最多选择两个不同订阅，持久化公平游标，Torra 分析全局并发固定为 1。RSS 在当前时间段已取得 job 时记录跳过；调度动作使用 `scheduled-rewash-analysis:{unit_key}:{offset_index}` 幂等键，崩溃租约恢复和 RSS job 续查均不重复提交。分析终态只推进观察时间点或关闭窗口，不自动下载候选。

阶段 6 的人工接口使用同一观察单元、动作台账、冷却和限额。GET 只读；设置和暂停/恢复使用 PATCH 并返回 200；分析与下载使用 POST，返回 `202 + Location`。浏览器只能提交幂等键、观察单元和已完成分析动作 ID，不能提交 Torra subscription ID、analysis ID 或候选映射。人工 RSS 分析可使用本地已存在匹配而不要求 RSS 收集闸门，但仍要求追更洗版总闸门和 SQLite 设置。候选下载还要求独立 `MCC_TORRA_REWASH_DOWNLOAD_ENABLED=true`、`confirm=true`，且只能读取服务端已完成分析动作；打开分析闸门不会自动下载。

阶段 7 的 MoviePilot 人工备用使用独立 `MCC_MOVIEPILOT_BACKUP_ENABLED` 闸门。服务端只在相关观察单元全部 `observation_expired`、Torra 映射可读且空闲、qB 无相关活动任务时调用 MoviePilot 查重；已有订阅只重搜，没有订阅才复用 NasEmby 创建逻辑。同步动作写入 `provider_actions`，使用幂等、60 秒冷却和白名单结果摘要，不保存或返回外部订阅 ID、URL、Token 或原始响应。

`push-preview` 是只读证据接口；`push` 在服务端重新构建计划和查重，不能复用浏览器提交的旧预览。

## 7. 调度模型

Gunicorn 固定单 worker，避免多进程重复调度。后台组件：

- HDHive 到期检查线程。
- 发现缓存预热线程。
- 订阅调度线程，仅在 `MCC_SUBSCRIPTION_SCHEDULER_ENABLED=true` 时启动。
- 私人 RSS 收集线程，仅在 `MCC_PRIVATE_RSS_ENABLED=true` 时启动；每次最多并发两个来源，同一来源互斥，失败按 `Retry-After` 或指数退避持久化到 SQLite。
- 追更洗版协调线程，仅在 `MCC_TORRA_QUALITY_WATCH_ENABLED=true` 时启动；SQLite 设置仍须单独开启，单 worker 内全局分析并发固定为 1。

当前 Compose 固定关闭订阅调度。未来开启多 worker 或多副本前，必须先引入调度选主和台账并发写方案。

## 8. 写入闸门

当前部署固定：

```env
MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false
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

`NASEMBY_CORE_WRITE_ENABLED` 控制订阅保存、分类、改季、配置、执行、屏蔽、删除和清空。Torra 推送还必须通过 `TORRA_PUSH_ENABLED`。追更洗版分析要求 `MCC_TORRA_QUALITY_WATCH_ENABLED` 与 SQLite 设置同时开启；候选下载在此基础上还要求独立的 `MCC_TORRA_REWASH_DOWNLOAD_ENABLED`。MoviePilot 人工备用只受更小的 `MCC_MOVIEPILOT_BACKUP_ENABLED` 动作闸门保护，不依赖旧核心接口总开关。

`MCC_PRESERVED_CORE_API_ENABLED` 仅用于原核心接口的模拟兼容测试。生产不得整体开启；每组能力接入 React 前必须建立独立的字段白名单、写入开关、幂等和审计。

qB 暂停/恢复与 Emby 刷新是已经存在的人工可回滚动作，分别使用目标复查、执行结果复查、证据锁和冷却保护。部署只读验收阶段不调用这些接口。

## 9. 认证与安全

### 威胁模型

主要防护：

- 未授权用户读取媒体数据、服务状态和管理 API。
- 跨站请求伪造危险动作。
- 上游异常、URL 或凭据泄漏到浏览器和日志。
- 外部图片代理引发 SSRF。
- 路径遍历读取 Mineradio 或 React 目录外文件。
- 旧 NasEmby 高风险管理路由随统一端口意外暴露。
- 重复调度、重复推送和空分类路径造成外部副作用。

### 安全策略

- 生产首次启动必须完成唯一管理员初始化，管理员密码只保存为 `scrypt` 摘要。
- 使用固定七天、HttpOnly、SameSite=Strict 的 HMAC 会话 Cookie。
- 危险方法要求有效管理员会话和具体写入闸门，不依赖手工来源白名单。
- 登录五次失败锁定 15 分钟；登录页使用严格 CSP。
- `/healthz` 是唯一无认证业务健康探针，始终返回 `status=ok`；正式镜像额外返回完整 Git SHA 的 `revision`，本地未注入构建修订时可以省略该字段。
- 图片代理拒绝私网 IP、私网 DNS、凭据 URL、重定向和无效图片魔数。
- 静态资源使用安全路径拼接；`api/auth/mineradio` 前缀不进入 SPA 回退。
- API 异常返回固定错误与请求 ID，不返回堆栈、内部 URL 或异常正文。
- 统一动作 API 不返回请求 payload、Token、候选下载信息或原始 external job ID；job ID 只提供稳定 SHA-256 摘要用于审计关联。
- 六阶段任务事实在写入前再次校验枚举、时间、scope 和证据一致性；浏览器只接收 presenter 白名单字段，`sourceRef/unitKey` 使用稳定 SHA-256 不透明引用，路径、URL、凭据和原始外部 ID 不进入公开响应。
- 阶段 6 动作请求不接受浏览器提供的 Torra subscription ID、analysis ID 或候选映射；跨 RSS 匹配复用幂等键返回 409，不回放其他匹配的动作。
- 保留核心接口在统一入口默认返回 503；Flask 原 `/static/*` 注册关闭，原 NasEmby 静态页面脚本不对外提供，迁移期静态快照不再保存在公开仓库。

### 信任边界

- 浏览器不接收服务端已保存的 Emby、qB、Torra、Symedia、TMDB、115、Telegram 或 HDHive 凭据；登录表单中的临时输入不写入浏览器存储。
- 环境变量和 `data/user.env` 属于服务端受保护配置；管理员可通过脱敏的 `/api/v2/settings/runtime` 目录在网页修改应用级字段，宿主机端口和卷挂载仍由 Compose 管理。
- 外部服务全部是不可信上游，响应先解析、校验和映射。
- fnOS 反向代理只信任一层；公网必须使用 HTTPS 和防火墙限制源站端口。

### 已知限制

- SQLite 使用 WAL 和短事务支持当前单 worker 多线程；多 worker 或多副本仍需要调度选主和跨实例动作租约。
- 原 NasEmby legacy 源码和核心路由仍在仓库中作为业务来源与契约基线；没有等价替代和回归测试前不得删除。
- 部分受保护 HDHive 资产只能在匹配的 Python 3.13 / Linux 环境运行。
- 当前阶段没有完成真实订阅到入库闭环，外部路径和下载器 ID仍需 fnOS 实机核对。
- 自动云盘兜底当前只计算和展示状态，没有后台自动执行器；人工搜索/转存同样默认由环境闸门关闭。
- Torra `secupload_115` 当前只提供分类批次、最近运行和成功/失败汇总，不提供文件 ID、路径或逐文件结果；Fluxa 可以展示最近批次失败数，但不能据此声称某个文件已经秒传或秒传失败。
- 私人 RSS 地址和下载地址按用户选择明文写入 SQLite；数据库和备份被复制时 Passkey 会泄露，这是明确接受的剩余风险。
- 候选确认加入会先复用现有订阅保存/provider 链路，再记录候选幂等响应；SQLite 与外部 provider 无法共享事务。若进程在两步之间退出，重试会因已存在同媒体范围的追更而在再次调用 provider 前被拒绝，但无法回放首次响应，需要用户刷新候选与追更状态。
- 私人 RSS 已使用 4 个真实结构脱敏夹具（M-Team、HDHome、织梦、青蛙）复核 RSS 2.0 字段、电影/单集/整季包混合 Feed、`720p/1080i/1080p/2160p` 版本摘要和 enclosure；解析器不假设响应条目数严格等于请求条数。四个来源已满足当前版本，更多站点只作为后续兼容扩展；真实收集继续默认关闭。`429/Retry-After`、指数退避、双并发、同来源互斥和抓取记录上限已经用模拟响应覆盖。

## 10. 持久化

- `data/`：配置、活动日志、刷新冷却状态。
- `db/`：SQLite 订阅/RSS 台账、迁移报告、详情/发现缓存。
- `upload/`：运行文件和会话资产。

Compose 通过 `MCC_DATA_ROOT` 把三个目录映射到同一个 fnOS 根目录。升级和回滚整体备份，不能手工拼接台账。

## 11. 测试与完成标准

自动验证包含：

- 47 条冻结 v1 路由和 67 条 v2 路由均在 Python 中存在。
- 当前后端回归基线为 493 项。
- 42 条受保护路由逐条返回 401。
- 所有受保护写接口逐条验证管理员会话与具体写入闸门。
- React API 引用全部属于 client 契约。
- 临时台账验证保存、列表、分类和改季，不连接真实 provider。
- Torra 推送、追更洗版分析/下载和 job 查询只使用模拟客户端。
- RSS 解析器和收集器使用完全脱敏的 M-Team、HDHome、织梦、青蛙真实结构夹具覆盖电影/剧集、单集/整季包、多版本、大小、enclosure、`720p/1080i/1080p/2160p`、Blu-ray/Remux、WEB-DL、H.264/H.265、HDR、Atmos 和 TrueHD；四个夹具经假 HTTP 响应写入临时 SQLite 后，公共查询仍不返回 URL 或测试 Passkey。
- 保留的高风险入口默认返回 503，模拟测试可显式开启。
- Docker 最终镜像不含 Node，重启后持久目录保留。
- Mineradio 片段使用冻结 SHA-256，桥接消息和原资源继续回归。
- v2 写接口逐条验证管理员认证与写闸门；网盘候选脱敏、默认关闭、重复阻止和幂等回放使用模拟测试覆盖。

## 12. 回滚

代码优先回滚到上一个已验证镜像或归档标签；订阅数据不随代码回滚。恢复旧双服务归档时必须确保新容器已停止，不能同时启动两套后端或调度器。

## 13. 变更历史

### 2026-07-28 — 候选预览与确认加入追更 P0.5b

**变更内容**：新增候选白名单列表、只读加入预览和幂等确认加入三条 v2 路由。发现页默认读取独立候选池；候选只有在服务端复核 TMDB/季号、重复追更、写入能力并由用户明确确认后，才以 `origin/intent_origin=manual` 写入追更并进入现有 activation/provider 链路。

**变更理由**：把“榜单发现”和“用户追更意图”之间建立明确确认边界，同时保留直接 TMDB 搜索的兼容保存路径。预览不写库或触发外部动作，同一幂等键重放不重复保存。

**影响范围**：候选仓库、Flask 路由、发现页、TypeScript 类型、67 条 v2 契约和隔离测试。历史污染分类与迁移仍留在 P0.5c，不在本阶段自动处理。

### 2026-07-28 — 候选池与来源刷新隔离 P0.5a

**变更内容**：SQLite schema 升级为 v5，增加 `discover_candidates/candidate_migration_runs`。榜单刷新与全球日播同步只按明确 TMDB 身份 upsert 独立候选并更新运行摘要，不再合并、重写订阅台账，也不再排队 Torra、资源规则或其他 provider。

**变更理由**：榜单结果是待选择内容，不是用户追更意图；自动写入追更会污染追更列表、日历和任务统计。

**影响范围**：SQLite 订阅仓库、发现来源刷新、全球日播同步、订阅设置文案、兼容刷新响应和回归测试。候选转追更与历史污染迁移尚未在本小阶段开放。

### 2026-07-28 — 追更、对账与日历可信语义 P0.4

**变更内容**：对账响应增加公开脱敏的 `torraFact/pipelineOutcome`，兼容 `fulfillmentState` 只由 Torra 新事实投影；追更工作台改读任务 v2 的独立事实和统一结果，新增结构化确认进度及 `playable` 统计，兼容 `completed/chainState` 只由新结果投影。Torra completed 只展示“获取目标已满足”，只有 `is_running=true` 显示获取中；没有正数集级媒体库证据时返回 `progress.state=unconfirmed`，不再从 TMDB 总集数生成 `0/N`。

日历只把精确电影或季集目标的当前 `pipelineFacts/pipelineOutcome` 投影到播出条目；Symedia 成功形成整理入库时间，Emby 电影或精确集级成功才形成 `playable`。默认月/周视图只包含人工追更、Torra 已关联记录和明确范围，自动来源、迁移复核及范围不明记录计入 `unlinked`，仅在 `includeUnlinked=1` 时返回。

**变更理由**：Torra 获取目标、Symedia 整理和 Emby 可播放原先被追更卡片与日历共用的 `completed/inLibrary/0/N` 混为最终完成，导致已获取但不可播放、作品级 Emby 命中批量覆盖单集以及大量未关联记录进入默认日历。

**影响范围**：Fluxa/Torra 只读对账、追更工作台、订阅兼容映射、日历时间线与 URL、React 追更/日历、TypeScript 类型、v2 契约和模拟测试。没有新增数据库表、候选池或外部写动作；P0.5 才停止自动来源写追更并提供历史污染迁移。

### 2026-07-28 — 任务中心、首页与统一结果统计 P0.3

**变更内容**：任务列表新增可重复 `outcomeState` 查询和顶层 `outcomeState/playableAt`，兼容 `completed/completedAt` 只由 `playable/playableAt` 单向投影。任务中心、首页、顶部导航、全局搜索和作品总览切换到 `pipelineOutcome` 与六阶段事实；任务中心四组改为“需要处理 / 处理中 / 已可播放 / 无需处理”，无 URL 筛选时先读 summary 决定首次列表查询。首页拆分 `mediaActionRequired/auxiliaryAlerts/inProgress/playableToday`，自动恢复中的秒传按明确失败数量计入处理中，媒体异常和辅助提醒使用各自深链。

**变更理由**：旧页面仍把 Torra/Symedia 完成解释为最终完成，并把 RSS、服务异常和媒体任务共用一个红色数字；任务中心默认进入空的处理中页，计划重试又显示为 0 个处理中。首批核心消费者必须共享同一个派生结果和统计口径，才能让顶部入口、首页数字、列表筛选与作品搜索互相一致。

**影响范围**：任务 v2 只读查询与兼容投影、首页摘要、全局作品搜索、React 任务中心/首页/顶部导航/作品总览、TypeScript 类型、v2 机器契约和回归测试。没有新增数据库结构或外部写动作；旧 `userState` 查询仍可读，新页面只写 `outcomeState`。

### 2026-07-28 — 六来源事实适配与单向兼容投影 P0.2

**变更内容**：新增 `pipeline_source_fact_runtime.py`，把 Torra、qB、115、Symedia、STRM 和 Emby 分别转换为六阶段事实。Torra `completed` 只生成获取目标满足，qB 使用文件 unit 汇总，Symedia 区分成功、正常保护与真实失败；115 分类摘要不能绑定当前媒体时保持 `system-category + unknown`，STRM 没有独立服务结果时保持 `unknown`。Emby 索引扩展为电影、剧集和精确季集，并按 `TotalRecordCount/StartIndex` 完整分页，只有 movie 或精确 episode 命中生成可播放事实。

任务链的兼容 `steps/state/acquisition/embyIndexed` 改为从 `pipelineFacts` 单向投影；原始来源不再绕过事实层直接写旧状态。旧响应字段与页面行为继续保留，P0.3 才切换任务中心、首页和统计消费者。新增红线测试覆盖 Torra 完成不等于可播放、qB 完成不反推 115、Symedia 成功不反推 STRM、剧集作品级命中不替代集级证据、跨页 Emby 集级关联以及旧字段不得绕过事实层。

**变更理由**：P0.1 只有契约和默认未知事实，无法修正真实任务的阶段语义；若来源同时继续直接写旧字段，又会形成两套互相漂移的逻辑。P0.2 先建立唯一来源适配路径，并保留可回滚的兼容输出，为 P0.3 切换核心消费者提供真实数据。

**影响范围**：只读任务聚合、Emby 只读索引、兼容字段投影、维护文档和模拟测试。没有新增路由、数据库表、外部写动作或前端消费者；115 文件级结果和 STRM 服务状态仍待后续明确来源接入。

### 2026-07-27 — 可信任务事实契约 P0.1

**变更内容**：新增 `pipeline_fact_runtime.py` 与 `pipeline_outcome_runtime.py`，固定 `torra/qb/cloud115/symedia/strm/emby` 六类事实、五类 scope、三档证据和六类用户结果。任务详情可选返回六阶段 `pipelineFacts`，列表返回轻量 `pipelineOutcome`，摘要返回 `outcomeCounts`；原 `userState/resultText/completedAt` 继续由唯一 legacy projector 生成，本阶段不切换前端筛选或页面结论。没有显式来源适配器事实时，六阶段统一返回 `unknown + missing` 和 `evidence_insufficient`，不从旧 `download/library` 状态反推新事实。

资源快照改为把当前六阶段事实幂等写入既有 `resource_events`，payload 只保存 scope、重试元数据和不透明 `sourceRef/unitKey`；既有 identity、迁移和历史阶段事件保持不可变。公开 presenter 使用字段白名单并再次隐藏路径、URL、凭据和原始外部引用。事实缺少观测时间/有效期、枚举非法、包含未知字段或同阶段当前证据冲突时明确拒绝或返回 `EVIDENCE_CONFLICT`。

**变更理由**：旧任务链把 Torra 获取、下载、115、Symedia 整理、STRM 和 Emby 收录混入少量综合阶段，导致下游记录可以提前生成“完成”。先增加独立事实与纯函数派生层，可以在不改变旧页面的前提下为 P0.2 来源接入和 P0.3 消费者切换建立可验证契约。

**影响范围**：任务聚合、任务公开 presenter、资源事件台账、HTTP v2 可选响应字段、TypeScript 可选类型、API 文档和定向测试。没有新增路由、必填请求字段、外部写动作或数据库表；`contractVersion=2`、旧查询和旧字段类型保持兼容。

### 2026-07-26 — 系统异常闭环与追更生效契约

**变更内容**：新增 `secupload_issue_runtime.py` 纯函数秒传状态机：状态固定 `normal/recovering/action_required/unknown`，600 秒宽限、86400 秒计划上限，时间统一按带时区绝对时间比较；不再只凭失败数或 `nextRunAt` 非空判定。新增 `/api/v2/system-issues/secupload-failures` 只读摘要及重试预检/确认执行/动作轮询三个接口；手动重试复用 `provider_actions`（目标键 `system:torra:secupload`），幂等重放返回原动作、竞争返回 409、run ID 保存为 `external_job_id` 并经插件 `recent_runs` 复查。分类摘要使用稳定公开 ID，不泄露 Torra 原始分类 ID、插件 key、目录或路径；摘要以可选 `systemIssues` 附加到任务 summary/chains 与首页，recovering 用处理中语义、不进入红色真实异常计数，首页深链改为 `/tasks?systemIssue=secupload_failures`。

追更能力接口拆分 `manualFollow` 与 `sourceScan`，后台来源扫描不再参与手动加入结果判定；保存接口按 replaced、队列、推送与错误生成五类 `activation`（已推送/已入队/仅保存/已存在/推送失败），异步入队只返回 `saved_and_queued`。活动 API 新增 `view=important`：在 limit 前折叠 `request_id=background` 且 success/info/skip 的相同 category/action/status 后台活动，折叠项返回 `repeatCount/firstTime/lastTime`，error 与人工请求永不折叠，raw 默认行为不变。

**变更理由**：实机秒传失败此前只能显示红色计数，无法区分"等待 18:00 自动重试"与"需要人工介入"；手动加入追更的结果由前端用调度器运行状态猜测，出现"仅保存"与"自动获取"互相矛盾的文案；重复后台同步会把更早的人工失败挤出活动窗口。

**影响范围**：Torra 读取层、任务链 v2、首页摘要、订阅工作台/兼容保存、发现后处理活动标记、活动 API、64 条 v2 契约与 453 项回归。全部新字段可选，旧客户端不受影响；自动化测试全部使用假客户端，未触发真实外部写操作。

### 2026-07-26 — 发布前搜索、状态和公开标识收口

**变更内容**：全局搜索补齐已识别 RSS、按明确身份定位的 Emby-only、TMDB 只读补充和无 TMDB 本地任务；后者返回空 `tmdbId`、公开 `chainId` 与任务深链。任务普通状态固定为四类 `userState`，首页只把仍有效的真实故障列为主问题，历史下载完成记录不再误算为“下载完成未入库”。Torra-only 订阅、观察单元与 RSS 匹配改用可反解的公开哈希 ID；任务 DTO 的 qB/Torra/Symedia/artifact 标识在 HTTP 边界改为不透明引用，qB 动作从实时快照反解；同步冲突预览同时把旧 SQLite 中 `torra:<原始远端 ID>` 投影为公开键。日历搜索使用完整轻量索引，RSS 深链统一身份筛选参数。

**变更理由**：用户需要从任意作品或首页问题直接进入准确任务，同时不能因历史身份维护、过期证据、正常保护或外部原始 ID 造成假异常、404 和信息泄露。

**影响范围**：媒体搜索、任务链 v2、首页摘要、Torra 对账/质量观察、RSS 匹配、日历缓存、公开响应映射、React 深链与回归测试。测试包启动时把默认配置、认证库、业务 SQLite、发现缓存、TMDB 缓存和活动日志统一重定向到临时目录，导入 `app.main` 与默认 `create_app()` 不再触碰工作区真实数据。所有新增搜索和解析路径均为只读；Torra 秒传仍只有批次级证据，不能绑定到单文件。

### 2026-07-25 — 用户决策字段、完整日历索引与 RSS 单条闭环

**变更内容**：任务接口增加四类日常 `userState`、一句话 `resultText`、完成时间和唯一主操作；日历月摘要附带完整轻量搜索索引并使用 300 秒完整快照；新增 `POST /api/v2/rss-matches`，为明确 RSS 条目、追更和观察单元建立幂等人工匹配。Torra-only 追更可只读获取质量观察，人工匹配会重新验证媒体身份、季集、基线时间、观察窗口和 Torra 当前归属；分析结果只公开评分变化等脱敏摘要。

**变更理由**：普通用户需要从首页或追更搜索直接定位问题、看懂当前结果并完成一次受保护动作，不能再由浏览器拼接内部状态，也不能让 RSS 搜索停在只有“预览”的只读结果。

**影响范围**：任务链 v2、首页摘要、日历聚合、媒体搜索、RSS 仓储/匹配、Torra 质量动作、60 条 v2 契约、React 工作台和模拟测试。RSS 下载仍要求独立闸门、明确确认、完整请求幂等、限流、冷却及 Torra/qB 复查；回收租约遇永久上下文错误必须转为终态并释放全局槽位，临时上游故障继续保留重试；不暴露候选 ID、下载地址或凭据。

发布前复核进一步明确：实时读取成功且媒体身份已映射的 Torra-only 订阅是正常只读追更证据，不因缺少 Fluxa 本地镜像进入待处理；首页的“今日完成”统一按 `Asia/Shanghai` 自然日计算，避免北京时间 00:00–08:00 落入前一天。

### 2026-07-22 — 资源任务事件账本

**变更内容**：SQLite 升级到 schema version 4，新增 `resource_chains`、`resource_artifacts` 和 `resource_events`。任务链 v2 在响应健康筛选前先幂等保存完整快照；相同状态不重复追加事件，状态或原因变化才形成新证据。产物身份升级保留同一 `chain_id` 下的别名事件，已归属其他链的产物不会静默改绑。

**变更理由**：订阅 ID 无法覆盖手工下载、补档和洗版，临时内存聚合也不能支持重启后的异常追查、证据过期和动作审计。持久化事件账本为后续异常分类与安全重试提供唯一证据来源。

**影响范围**：SQLite schema、任务链 v2 读取、Flask 生产装配、脱敏和模拟测试。只产生本地证据写入，不触发 Torra、qB、115、Symedia 或 Emby 外部动作。

### 2026-07-22 — 首页真实结论与 Fluxa/Torra 只读对账

**变更内容**：新增 `/api/v2/home/summary` 与 `/api/v2/subscriptions/reconciliation`。首页摘要按任务链、服务读取和调度器运行心跳聚合健康状态；追更对账使用远端 ID、TMDB 身份和季号确定关联，标题相同只生成冲突候选。对账、履约、健康三维状态独立返回，并包含观察时间、有效期、来源和机器/用户原因。

**变更理由**：修复“设置关闭但页面显示调度正常”和“Fluxa 158 条、Torra 112 条被当成一套台账”的误报，让首页结论和追更差异均可由真实证据解释。

**影响范围**：Flask 应用装配、任务链首页聚合、Torra 只读读取、订阅工作台响应、v2 机器契约和模拟测试。第一阶段不删除、切季或反向编辑 Torra，`TORRA_PUSH_ENABLED` 保持关闭，影院大厅未修改。

### 2026-07-22 — 任务链身份 v2 适配层

**变更内容**：新增任务链 v2 只读适配接口，为每条资源生成稳定的 `chainId`、`mediaKey`、`targetKey` 和 `artifactKeys`，同时返回每阶段证据和健康状态筛选；任务中心改用该接口，旧 `/api/tasks/chain` 保留兼容。

**变更理由**：订阅 ID 不能覆盖手工下载、补档和洗版任务。先统一身份和证据合同，再逐步接入受预览、确认、幂等和冷却保护的动作。

**影响范围**：Flask 任务链装配、React 任务中心读取、v2 机器契约和模拟测试；不触发 Torra、qB、115、Symedia 或 Emby 写操作。

### 2026-07-22 — 任务异常分类与解释

**变更内容**：新增独立任务异常分类器，逐阶段识别真实阻塞、过期或缺失证据、处理中和计划重试、低分或重复保护、明确完成，并按“需要处理 → 证据不足 → 等待 → 正常保护 → 正常”汇总任务。任务与阶段新增 `recommendedAction`、`retryEligible` 和 `plannedRetryAt`，React 任务中心以“为什么 / 下一步”显示，不新增主导航。

**变更理由**：避免把 Symedia 低分拒绝、已有计划重试和真实失败都显示成红色异常，也避免证据缺失时误报绿色正常。动作资格与动作执行保持分离，C.4 只提供可解释状态。

**影响范围**：任务链 v2 只读响应、任务中心深浅主题、异常分类测试和维护文档；不创建重试 API，不执行 Torra、qB、115、Symedia 或 Emby 外部写操作。

### 2026-07-20 — 运行时配置中文目录与兼容分层

**变更内容**：运行时配置目录为全部字段补齐中文名称和用途说明；当前主链配置继续按 Emby、qBittorrent、Torra、Symedia、TMDB、MoviePilot、云盘、Telegram、HDHive 和自动化分组，旧 `ENV_TORRA_*`、`ENV_SYMEDIA_*`、旧 115/PT 转存及媒体库别名统一进入最后的“高级兼容设置”。React 默认隐藏技术变量名，可按需显示，搜索仍匹配中文说明和真实键名。

**变更理由**：自动 Title Case 回退会把旧环境变量显示成难以理解的英文标题，且兼容字段与当前主链字段混排，容易让用户误以为全部都必须配置。

**影响范围**：`/api/v2/settings/runtime` 的字段元数据与分组顺序、React 设置页展示和运行时配置测试；配置键、保存格式、敏感值不回显和客户端热刷新行为不变。

### 2026-07-20 — 管理员网页配置全部应用字段

**变更内容**：新增 37 条 v2 契约中的运行时配置读写接口、统一字段目录、敏感值脱敏和显式清除、`data/user.env` 原子持久化、核心客户端热重配置，以及按软件分组的 React 设置表单。

**变更理由**：服务连接、凭据和功能开关需要由管理员在网页统一维护，不能要求每次修改都编辑 Compose `.env`；同时必须避免密码、Token、Cookie 和 API Key 回显。

**影响范围**：配置加载优先级、Emby/qB/Torra/Symedia 客户端、设置页、v2 契约、部署文档和安全测试。宿主机端口、卷挂载和镜像标签仍由 Compose 管理。

### 2026-07-20 — 容器运行根路径兼容

**变更内容**：工作区 `.env` 路径改为向上查找 `package.json`；容器镜像找不到工作区标记时回退到运行根目录 `.env`，并增加本地深目录与容器浅目录回归测试。

**变更理由**：容器内应用位于 `/app/app`，固定访问 `ROOT_DIR.parents[1]` 会越过可用父目录并导致 Gunicorn Worker 启动失败。

**影响范围**：Python 配置初始化、容器启动与源码契约测试；Compose 仍通过 `env_file` 注入环境变量，不改变凭据优先级和写入闸门。

### 2026-07-19 — 本地服务环境加载与 TMDB Bearer 凭据

**变更内容**：本地 `python -m app.main` 启动时加载工作区根 `.env`；新增 `TMDB_API_TOKEN` v4 Bearer 支持，并限制 Authorization 头只发送到配置的 TMDB API 基址。写入、推送、自动调度和追更下载闸门继续默认关闭。

**变更理由**：开发环境原先只在请求配置时读取服务目录环境，导致启动时的 Emby、qB、Torra、Symedia 客户端拿到空配置；TMDB v4 Token 不能作为旧版 query `api_key` 使用。

**影响范围**：`app/config.py`、`app/main.py`、`discover_runtime.py`、环境样例、Compose 变量和 TMDB Bearer 回归测试。该阶段凭据只保存在被 Git 忽略的本地 `.env`；后续网页配置通过脱敏接口保存到 `data/user.env`。

### 2026-07-19 — 首个真实结构 RSS 脱敏夹具

**变更内容**：在用户明确授权的只读探测后，新增完全脱敏的 M-Team RSS 2.0 夹具和解析回归，并将 `1080i` 纳入版本摘要；夹具地址全部替换为测试域名，不保存真实签名、UID、详情或下载 URL。

**变更理由**：用实际响应结构验证标准字段与标题解析，同时让后续回归无需再次连接私人 RSS，也不把个人凭据写入仓库。

**影响范围**：私人 RSS 解析器、单元测试、测试夹具和维护文档。未写入 SQLite，未访问 enclosure，未连接 Torra、qB、MoviePilot、Emby、115 或 Symedia。

### 2026-07-19 — 第二个真实结构 RSS 脱敏夹具

**变更内容**：在第二次用户明确授权的只读探测后，新增 HDHome RSS 代表性脱敏夹具，覆盖电影与整季包、完整 `enclosure`、大小以及 Blu-ray/Remux、WEB-DL、`1080i`、`2160p`、Atmos 和 TrueHD 标题字段；独立 `Sxx` 标记会识别为电视剧季号，集号保持为空。

**变更理由**：继续用不同站点的真实字段组合验证解析兼容性，并修正整季包只有 `Sxx`、没有 `Exx` 时的媒体类型判断；只保留测试域名、测试 GUID 和有限样本，不保存原始 RSS 或个人参数。

**影响范围**：私人 RSS 单元测试、测试夹具和维护文档。未写入 SQLite，未访问 enclosure，未连接任何 Torra、qB、MoviePilot、Emby、115 或 Symedia 写接口。

### 2026-07-19 — 第三个真实结构 RSS 脱敏夹具

**变更内容**：在用户明确授权的只读探测后，新增织梦 RSS 代表性脱敏夹具，覆盖 `S01/S02` 整季包、`720p/1080p/2160p`、WEB-DL、H.264/H.265/HEVC、AAC 和大体积 enclosure；站点请求 10 条时实际返回 11 条，解析器按响应内容处理而不依赖请求数量。

**变更理由**：验证独立季号规则在另一个真实站点上的兼容性，并确认 UTF-8 Feed 标题、整季包和非严格条数响应不会破坏解析。

**影响范围**：私人 RSS 解析器/收集器单元测试、测试夹具和维护文档。只写入测试临时 SQLite，未访问 enclosure，未保存原始响应或个人参数，也未连接任何外部写接口。

### 2026-07-19 — 第四个真实结构 RSS 脱敏夹具

**变更内容**：在用户明确授权的只读探测后，新增青蛙 RSS 代表性脱敏夹具，覆盖同一 Feed 中的电影、`S01` 整季包、`S01E03/S02E03` 单集以及 `1080p/2160p`、WEB-DL、H.264/H.265、HDR、AAC 和 Atmos。

**变更理由**：验证混合媒体 Feed、整季包与明确单集在同一站点中的分类和季集提取，并继续通过收集器到临时 SQLite 的公共脱敏查询回归。

**影响范围**：私人 RSS 解析器/收集器单元测试、测试夹具和维护文档。只写入测试临时 SQLite，未访问 enclosure，未保存原始响应或个人参数，也未连接任何外部写接口。

### 2026-07-18 — MoviePilot 阶段 7 人工备用入口

**变更内容**：新增 `moviepilot_backup_runtime.py`、两条 v2 预览/推送路由和 7 项隔离测试；复用 NasEmby MoviePilot 查重、创建和重搜门面，加入独立默认关闭闸门、观察单元/Torra/qB 预检、SQLite 幂等冷却、同步终态和脱敏摘要。

**变更理由**：为 Torra 观察窗口结束后的人工 PT 站点备用提供可核对的单条入口，同时不引入 MoviePilot 自动调度、不把外部订阅 ID 或凭据交给浏览器，也不让 Torra 不可达时自动切换。

**影响范围**：Flask API 装配、MoviePilot 门面、SQLite provider_actions、37 条 v2 机器契约、环境样例、Compose、模拟测试和维护文档。代码阶段没有连接真实 MoviePilot、Torra、qB、RSS、Emby、115 或 Symedia。

### 2026-07-18 — Torra 追更洗版阶段 6 人工 API

**变更内容**：新增全局设置、单条观察设置、人工订阅分析、人工候选下载和 RSS 匹配人工分析共 7 条 v2 路由；异步动作返回 `202 + Location`，统一 409/422/429/502/503 错误映射，并由共享调度器续查人工分析、下载和 RSS job。下载使用独立环境闸门，只从服务端已完成分析动作读取候选。

**变更理由**：在不开放浏览器上游映射、不自动下载候选的前提下，为受控人工验证提供稳定 HTTP 契约，并确保幂等重放、进程重启和跨 RSS 匹配冲突不会制造重复外部动作。

**影响范围**：Flask API 适配、订阅自动化服务、Torra/qB 预检、RSS 幂等身份、质量协调器、33 条 v2 机器契约、独立下载闸门、模拟测试和维护文档。真实 RSS、Torra、qB、Emby、115、Symedia 与 MoviePilot 均未连接。

### 2026-07-18 — Torra 追更洗版阶段 5 有限主动兜底

**变更内容**：新增默认关闭的质量观察协调器、SQLite 调度查询、公平游标、确定性错峰、截止点检查和 RSS job 共享续查；默认时间表、每轮批量、冷却及小时/每日限额进入订阅配置默认值，并补充线程闸门、限额、RSS 跳过、崩溃恢复和多订阅公平测试。

**变更理由**：RSS 无可靠命中时仍需少量主动检查，但必须避免固定高频搜索、同订阅占满队列、重启重复提交和 RSS/兜底并行分析。

**影响范围**：质量观察仓储/运行时/协调器、RSS 分析续查、订阅配置默认值、Flask/Gunicorn 后台装配、Compose 闸门、测试与维护文档。协调器与 SQLite 设置均默认关闭，分析结果不自动下载。

### 2026-07-18 — Torra 追更洗版阶段 4 RSS 匹配与即时分析

**变更内容**：升级 RSS 匹配表为 `item_id + unit_key` 唯一，新增活动观察窗口匹配器、原子写入回调和匹配列表读取；可靠 `candidate` 在双闸门、上游空闲、qB 空闲、冷却与限额复查通过后领取一次性 Torra 分析动作，持久化 job 并支持重启续查；补充匹配、阻塞、终态回放和失败恢复测试。

**变更理由**：只用可靠本地证据唤醒 Torra 分析，同时避免历史种子、模糊标题、跨集匹配、迟到 job 和进程重启制造重复外部动作。

**影响范围**：私人 RSS 仓储/收集器、质量观察与动作仓储、Torra/qB 只读复查、Flask 装配、v2 匹配读取、测试与计划文档。真实 RSS 与追更洗版闸门默认关闭，分析结果不自动下载。

### 2026-07-18 — Torra 追更洗版阶段 1–3

**变更内容**：新增质量观察、provider 动作和调度状态仓储；Torra 推送幂等与冷却迁入 SQLite；增加严格的洗版分析、下载和 job 适配器；统一动作查询改为读取持久化动作并脱敏公开字段；新增按电影/季集协调任务链下载证据和 Torra 可见 Emby 基准的观察运行时。

**变更理由**：保证服务重启后不重复提交外部任务，并让媒体控制中心只编排 Torra 已有评分结果，不猜测未知响应或复制质量规则。

**影响范围**：SQLite schema version 3、Torra 认证与 job 契约、任务链/Emby 基准协调、RSS 测试动作、v2 动作查询、Flask 装配、测试和维护文档。真实 Torra/RSS 写动作与自动调度仍保持关闭。

### 2026-07-18 — RSS 收集与原子迁移硬化

**变更内容**：增加 RSS 失败次数、退避截止时间和 HTTP 状态持久化，限制全局双并发、同来源互斥及每站 1000 条抓取记录；旧 JSON 改为临时 SQLite 导入、逐字段复核后原子替换。

**变更理由**：避免 PT 站点限流时持续重试，并确保迁移中断或差异检查失败时不会发布半成品台账。

**影响范围**：SQLite schema version、RSS 来源状态与收集调度、迁移报告、前端来源类型、回归测试和维护文档。真实 RSS 与所有 Torra/qB 写动作仍保持关闭。

### 2026-07-18 — SQLite 单台账与私人 RSS 种子库第一版

**变更内容**：订阅配置和条目切换到 `media_control_center.sqlite3`，增加旧 JSON 备份/校验/迁移报告；新增私人 RSS 来源、条目、FTS5、解析器、默认关闭的收集器、10 条 v2 契约和 React 种子库页面。

**变更理由**：消除 JSON 多线程读改写风险，并用一次站点 RSS 收集替代按订阅反复搜索 PT 站，为后续 Torra 追更洗版提供本地候选索引。

**影响范围**：订阅持久化、Flask 装配、后台调度、HTTP v2 契约、React 导航/种子库、Docker 环境、测试和文档。影院大厅未修改，真实 RSS 与 Torra 写动作继续关闭。

### 2026-07-17 — PT 单一主线与当前页面收口

**变更内容**：当前订阅只提供 Torra 预览和推送；Symedia 不接收订阅推送。Telegram、HDHive / pansou、影巢和 115 分享转存从 React 隐藏但源码、路由与测试保留。新增缓存系统指标、活动日志页面、订阅导航和媒体抽屉优化；测试活动日志改用临时路径，移动端抽屉关闭时主动释放焦点。

**变更理由**：Torra 已负责 PT、qB 编排和 115 秒传，Symedia 已负责后续整理入库；中控不重复实现现有线路。先验证单条 PT 主链，再考虑 MoviePilot 或 Telegram 网盘扩展。

**影响范围**：订阅兼容层、系统指标运行时、React 导航/订阅/任务/设置/影院抽屉、HTTP v2 契约、测试和文档。NasEmby 网盘底层源码没有删除，Mineradio 核心视觉与桥接协议未修改。

### 2026-07-17 — PT 默认策略与 NasEmby 网盘安全接入

**变更内容**：默认订阅模式改为 Torra；旧资源优先默认配置迁移到 PT 主通道。新增 13 条 v2 接口、集成设置、网盘策略、候选预览、单条转存保护和任务中心支线状态。

**变更理由**：落实“PT 优先、网盘由开关控制”的产品决策，同时继续复用 NasEmby 115、Telegram、HDHive / pansou 业务源码，不整体开放旧高风险管理接口。

**影响范围**：订阅配置、任务链、系统设置、发现订阅卡、Python 应用装配、Docker 闸门、契约、测试和文档。影院大厅视觉、顶部导航外观和媒体队列未修改。

### 2026-07-17 — 恢复原核心接口并改为保留优先

**变更内容**：恢复 `main.py` 中 115、Telegram、HDHive、provider、配置和活动日志路由；增加默认 503 隔离开关、模拟测试、逐接口能力矩阵和原静态页面参考快照。

**变更理由**：底层模块仍在并不等于接口链路完整。保留准确路由、参数处理和调用关系，避免以后重新猜测业务语义或误删网盘能力。

**影响范围**：Python 应用入口、接口安全边界、测试、环境样例和 v2 文档。React 当前 47 条契约、页面结构、影院大厅、顶部导航和媒体队列未修改。

### 2026-07-17 — Python 后端统一完成

**变更内容**：补齐发现、订阅和内部诊断兼容层，增加写闸门与 legacy 管理入口守卫；切换单容器 Python 运行时；删除 Express 源码、运行依赖和旧 Node 后端测试。

**变更理由**：保持 NasEmby 原业务的同时消除双后端、双台账和双调度风险，并让 fnOS 部署只维护一个容器。

**影响范围**：后端运行时、Docker、API 契约测试、部署与回滚文档。React 页面结构、影院大厅、顶部导航、媒体队列和 Mineradio 原视觉未修改。
