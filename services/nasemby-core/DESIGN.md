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
      ├─ 认证、Origin、请求 ID、错误脱敏
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
5. v2 Torra 推送、追更洗版设置/动作、系统指标、集成与延期保留的网盘运行时。
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

未知字段、原始上游包络、Token、Cookie 和异常正文不会透传浏览器。

### 内部诊断响应

`/api/internal/nasemby-core/*` 保留原 NasEmby 数据形状，便于核对源码行为。它们仍受会话认证保护，不是外部服务间的第二个网络层。

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

- 生产必须配置至少 16 字符的 `MCC_ACCESS_KEY`。
- 使用固定七天、HttpOnly、SameSite=Strict 的 HMAC 会话 Cookie。
- 危险方法只接受同源或 `MCC_ALLOWED_ORIGINS` 中的精确 Origin。
- 登录五次失败锁定 15 分钟；登录页使用严格 CSP。
- `/healthz` 是唯一无认证业务健康探针，只返回 `status=ok`。
- 图片代理拒绝私网 IP、私网 DNS、凭据 URL、重定向和无效图片魔数。
- 静态资源使用安全路径拼接；`api/auth/mineradio` 前缀不进入 SPA 回退。
- API 异常返回固定错误与请求 ID，不返回堆栈、内部 URL 或异常正文。
- 统一动作 API 不返回请求 payload、Token、候选下载信息或原始 external job ID；job ID 只提供稳定 SHA-256 摘要用于审计关联。
- 阶段 6 动作请求不接受浏览器提供的 Torra subscription ID、analysis ID 或候选映射；跨 RSS 匹配复用幂等键返回 409，不回放其他匹配的动作。
- 保留核心接口在统一入口默认返回 503；Flask 原 `/static/*` 注册关闭，原 NasEmby 静态页面脚本不对外提供，迁移期静态快照不再保存在公开仓库。

### 信任边界

- 浏览器不接收服务端已保存的 Emby、qB、Torra、Symedia、TMDB、115、Telegram 或 HDHive 凭据；登录表单中的临时输入不写入浏览器存储。
- 环境变量和 `data/user.env` 属于服务端受保护配置。
- 外部服务全部是不可信上游，响应先解析、校验和映射。
- fnOS 反向代理只信任一层；公网必须使用 HTTPS 和防火墙限制源站端口。

### 已知限制

- SQLite 使用 WAL 和短事务支持当前单 worker 多线程；多 worker 或多副本仍需要调度选主和跨实例动作租约。
- 原 NasEmby legacy 源码和核心路由仍在仓库中作为业务来源与契约基线；没有等价替代和回归测试前不得删除。
- 部分受保护 HDHive 资产只能在匹配的 Python 3.13 / Linux 环境运行。
- 当前阶段没有完成真实订阅到入库闭环，外部路径和下载器 ID仍需 fnOS 实机核对。
- 自动云盘兜底当前只计算和展示状态，没有后台自动执行器；人工搜索/转存同样默认由环境闸门关闭。
- 私人 RSS 地址和下载地址按用户选择明文写入 SQLite；数据库和备份被复制时 Passkey 会泄露，这是明确接受的剩余风险。
- 私人 RSS 已使用 4 个真实结构脱敏夹具（M-Team、HDHome、织梦、青蛙）复核 RSS 2.0 字段、电影/单集/整季包混合 Feed、`720p/1080i/1080p/2160p` 版本摘要和 enclosure；解析器不假设响应条目数严格等于请求条数。四个来源已满足当前版本，更多站点只作为后续兼容扩展；真实收集继续默认关闭。`429/Retry-After`、指数退避、双并发、同来源互斥和抓取记录上限已经用模拟响应覆盖。

## 10. 持久化

- `data/`：配置、活动日志、刷新冷却状态。
- `db/`：SQLite 订阅/RSS 台账、迁移报告、详情/发现缓存。
- `upload/`：运行文件和会话资产。

Compose 通过 `MCC_DATA_ROOT` 把三个目录映射到同一个 fnOS 根目录。升级和回滚整体备份，不能手工拼接台账。

## 11. 测试与完成标准

自动验证包含：

- 47 条冻结 v1 路由和 35 条 v2 路由均在 Python 中存在。
- 42 条受保护路由逐条返回 401。
- 所有受保护写接口逐条拒绝错误 Origin。
- React API 引用全部属于 client 契约。
- 临时台账验证保存、列表、分类和改季，不连接真实 provider。
- Torra 推送、追更洗版分析/下载和 job 查询只使用模拟客户端。
- RSS 解析器和收集器使用完全脱敏的 M-Team、HDHome、织梦、青蛙真实结构夹具覆盖电影/剧集、单集/整季包、多版本、大小、enclosure、`720p/1080i/1080p/2160p`、Blu-ray/Remux、WEB-DL、H.264/H.265、HDR、Atmos 和 TrueHD；四个夹具经假 HTTP 响应写入临时 SQLite 后，公共查询仍不返回 URL 或测试 Passkey。
- 保留的高风险入口默认返回 503，模拟测试可显式开启。
- Docker 最终镜像不含 Node，重启后持久目录保留。
- Mineradio 片段使用冻结 SHA-256，桥接消息和原资源继续回归。
- v2 写接口逐条验证认证与 Origin；网盘候选脱敏、默认关闭、重复阻止和幂等回放使用模拟测试覆盖。

## 12. 回滚

代码优先回滚到上一个已验证镜像或归档标签；订阅数据不随代码回滚。恢复旧双服务归档时必须确保新容器已停止，不能同时启动两套后端或调度器。

## 13. 变更历史

### 2026-07-20 — 容器运行根路径兼容

**变更内容**：工作区 `.env` 路径改为向上查找 `package.json`；容器镜像找不到工作区标记时回退到运行根目录 `.env`，并增加本地深目录与容器浅目录回归测试。

**变更理由**：容器内应用位于 `/app/app`，固定访问 `ROOT_DIR.parents[1]` 会越过可用父目录并导致 Gunicorn Worker 启动失败。

**影响范围**：Python 配置初始化、容器启动与源码契约测试；Compose 仍通过 `env_file` 注入环境变量，不改变凭据优先级和写入闸门。

### 2026-07-19 — 本地服务环境加载与 TMDB Bearer 凭据

**变更内容**：本地 `python -m app.main` 启动时加载工作区根 `.env`；新增 `TMDB_API_TOKEN` v4 Bearer 支持，并限制 Authorization 头只发送到配置的 TMDB API 基址。写入、推送、自动调度和追更下载闸门继续默认关闭。

**变更理由**：开发环境原先只在请求配置时读取服务目录环境，导致启动时的 Emby、qB、Torra、Symedia 客户端拿到空配置；TMDB v4 Token 不能作为旧版 query `api_key` 使用。

**影响范围**：`app/config.py`、`app/main.py`、`discover_runtime.py`、环境样例、Compose 变量和 TMDB Bearer 回归测试。凭据只保存在被 Git 忽略的本地 `.env`，不透传浏览器。

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

**影响范围**：Flask API 装配、MoviePilot 门面、SQLite provider_actions、35 条 v2 机器契约、环境样例、Compose、模拟测试和维护文档。代码阶段没有连接真实 MoviePilot、Torra、qB、RSS、Emby、115 或 Symedia。

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
