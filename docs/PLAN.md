# 媒体控制中心 v2 当前计划

状态：本地代码、React 人工入口、API 契约和四个真实结构脱敏 RSS 夹具已收口；fnOS 实机链路等待受控窗口
更新时间：2026-07-20

## 1. 最终目标

媒体控制中心在 fnOS 上以一个 Docker 容器运行：React 提供统一页面，Python 提供全部 API、认证、订阅业务和外部服务聚合。用户只从媒体控制中心创建和管理订阅。

固定原则：

- Python 是唯一后端。
- NasEmby 源码是订阅、发现、日历、资源规则和调度依据。
- 当前只运行 PT 主线：Torra → qB → Torra 秒传 115 → Symedia → Emby。
- 只有一份订阅台账和一套调度器；SQLite 已接管运行时读写，旧 JSON 只允许首次校验、备份和一次性导入，不双写。
- Telegram 频道网盘订阅、HDHive / pansou、影巢和分享转存延期，源码保留。
- Mineradio 主视觉不重做；导航按钮和媒体抽屉允许优化。

## 2. 已完成代码状态

- React 工作页、影院大厅和 Mineradio 桥接已经纳入同一项目。
- Python 已提供整站认证、React 静态托管和统一错误处理。
- Emby、qBittorrent、Torra、Symedia 摘要与任务链已经接入。
- 发现页包含 TMDB、JustWatch 海外流媒体、豆瓣、国内平台和全球日播。
- 订阅、详情、日历、分类、改季和资源搜索直接使用 NasEmby 源码与唯一台账。
- HTTP v1 冻结契约共 47 条；42 条业务路由受会话保护。
- 单容器 Docker 使用 Python 3.13 / Gunicorn，Node.js 只构建前端。
- 默认关闭订阅写入、订阅调度和 Torra 推送。
- 订阅详情已接入 Torra v2 预览和人工推送，包含服务端复查、确认、幂等、60 秒冷却和脱敏审计。
- 系统指标复用 NasEmby 原采样函数并增加 30 秒缓存；任务中心读取最近活动日志。
- Telegram、HDHive / pansou、影巢和 115 分享转存的底层模块与 v2 接口保留，但当前 React 不提供操作入口。
- 原 NasEmby 核心路由与调用关系已经恢复，默认返回 503 保留状态，不连接真实服务。

## 3. v2 当前代码工作

- [x] 建立全新 Git，不复制旧仓库历史。
- [x] 迁入最新源码、测试、Docker、Mineradio 资源和维护资料。
- [x] 排除真实配置、缓存、运行数据、构建产物和重复 ZIP。
- [x] 停止删除业务源码，恢复 `app/main.py` 中原 NasEmby 核心路由和调用关系。
- [x] 保留网盘订阅仍会使用的 115、Telegram、HDHive / pansou、Symedia 和 provider 业务模块与接口。
- [x] 建立 `CORE_API_CAPABILITY_MATRIX.md`，逐条记录用途、副作用、开关和接入状态。
- [x] 将 NasEmby 原静态页面源码作为迁移参考放入 v2，但不注册为第二套生产页面。
- [x] 把未完成能力整理为 `ROADMAP.md` 和 `CLOUD_ACQUISITION_PLAN.md`，不随迁移流水账删除。
- [x] 完成 165 项 Python 回归测试，包括 SQLite、原子 JSON 迁移、私人 RSS、Torra 质量 job、MoviePilot 人工备用、统一动作查询、按集 Emby 基准、RSS 活动窗口匹配、有限主动兜底、阶段 6 HTTP API、保留核心接口和现有 PT 主链契约。
- [x] 隔离自动测试活动日志；完整回归前后真实 `data/activity_log.jsonl` 不再被模拟动作追加。
- [x] 完成前端类型检查和 Vite 生产构建，npm 审计为 0 个漏洞。
- [x] v2 基线镜像已完成单容器构建、登录、只读、写闸门、保留接口 503 和重启验收。
- [x] 完成质量、安全和凭据扫描：质量 0 错误，生产 Python 安全扫描 0 Critical / 0 High。
- [x] 从 v2 最终镜像启动独立预览：`http://127.0.0.1:18789/`。
- [x] 提交 v2 完整代码基线：`4112c22`。
- [x] 修正订阅默认值为 PT / Torra，并迁移旧资源优先默认配置。
- [x] 修复顶部搜索/服务状态按钮和控制室 0/4 在线语义。
- [x] HTTP v2 契约扩展至 35 条，新增私人 RSS 来源、种子、活动窗口匹配读取、统一动作读取、阶段 6 追更洗版设置/动作接口和阶段 7 MoviePilot 人工备用接口。
- [x] 保留全局/订阅级网盘策略、脱敏候选、单条转存、幂等和模拟测试；当前已移出 React 页面并延期启用。
- [x] 完成 1440×900、1024×768 和 390×844 页面验收；修复移动端媒体抽屉关闭后仍被焦点保持展开的问题。
- [x] 重建 `media-control-center:v2-pt-final`，完成独立只读容器冒烟并确认运行层无 Node/npm。
- [x] 建立“中文业务表层 + 次级技术信息”文案规则，统一工作页标题、任务状态、大厅状态和卡片小标。
- [x] 默认开放正文、任务名、错误和路径复制，只在导航与按钮上禁用文字选择。
- [x] 将 5431 行单一 CSS 拆分为基础、外壳、工作台和页面模块；影院大厅 43 条冻结规则保持原样。
- [x] 工作页统一 UI / 等宽混排字体和 WCAG AA 文字令牌；最低文字对比度 6.83:1。
- [x] 保留原工作页 Hero 布局，不把“0 条订阅 · 0 条未完成”等实时数字放在页面标题旁。
- [x] 工作页标签支持左右键、Home、End 和单一 Tab 停靠点；qB 与 Emby 确认框共用焦点约束、Escape 和焦点返回。
- [x] 基于提交 `bde3eba` 构建只读候选镜像 `media-control-center:v2-pt-rc-bde3eba`，完成登录、静态资源、写闸门、保留接口、无 Node 运行层和重启验收。
- [x] 完成 SQLite 单台账、私人 PT RSS 种子库、Torra 追更洗版和 MoviePilot 备用边界设计，并写出两份分阶段实施计划。
- [x] SQLite 已接管订阅配置和条目读写；旧 JSON 支持备份、校验、报告和一次性导入，不再双写。
- [x] 私人 RSS 第一版已完成来源 CRUD、RSS/Atom 解析、FTS5 搜索、保留期清理、脱敏 API 和“种子库”页面。
- [x] 构建 SQLite/RSS 候选镜像 `media-control-center:sqlite-rss-preview`，完成登录、WAL/FTS5、来源配置写入、无 Node 运行层和容器重启持久化验收。
- [x] 补齐 RSS `429/Retry-After`、指数退避、全局并发 2、同来源互斥和每站 1000 条抓取记录上限。
- [x] 使用用户明确授权的 M-Team、HDHome、织梦、青蛙 RSS 完成只读结构探测和四个完全脱敏夹具；补齐 RSS 2.0 字段、电影/单集/整季包、多版本、大小、enclosure 与 `720p/1080i/1080p/2160p` 版本摘要，并完成假 HTTP → 收集器 → 临时 SQLite → 公共脱敏查询回归，不保存真实响应或访问下载地址。
- [x] 完成临时 SQLite 逐字段复核与原子替换演练；成功路径保留共享 RSS 表，失败路径不发布半成品。
- [x] 构建 `media-control-center:sqlite-rss-hardened`，完成登录、RSS 503 闸门、WAL/schema v2/FTS5、无 Node、脱敏和容器重建持久化冒烟。
- [x] schema version 3 已增加质量观察、provider 动作和调度状态；Torra 推送幂等、冷却与重启回放已迁入 SQLite。
- [x] 完成 Torra 洗版分析、候选下载和单 job 查询适配器；严格解析五种状态，并按每行最高正分差选择候选。
- [x] `/api/v2/automation-actions/:id` 已改为读取 SQLite，RSS 测试动作不再使用进程内字典，外部 job ID 和结果摘要经过脱敏。
- [x] 完成 Emby 基准和观察单元运行时：任务链下载终态建单元，Torra 可见库文件启动窗口；qB 先建后关联、多集隔离、历史不补扫、季集冲突阻断和目标已达均已覆盖。
- [x] 完成 RSS 即时唤醒：可靠 `candidate` 经过双闸门、Torra/qB 状态、冷却和限额复查后只提交一次分析，重启续查原 job，分析结果不自动下载。
- [x] 完成默认关闭的有限主动兜底：SQLite 时间表、0–15 分钟错峰、批量 2、全局并发 1、公平游标、RSS 时间段跳过、小时/每日限额、截止点和租约恢复均已覆盖。
- [x] 完成阶段 6 HTTP API：设置与单条观察 PATCH、人工分析、独立下载闸门、RSS 人工分析、统一错误包络、202 + Location、幂等冲突和脱敏动作查询。
- [x] 完成阶段 7 MoviePilot 人工备用：安全预览、已有订阅重搜、新订阅创建、Torra/qB 资格闸门、SQLite 幂等/冷却、脱敏同步结果和会话/Origin 测试；默认关闭，React 只提供人工入口，不接入自动调度。

详细执行清单见 `docs/V2_IMPLEMENTATION_PLAN.md`。

## 4. 未完成主线

### 当前下一步：候选镜像与实机阶段

执行顺序固定为：

1. 阶段 6 API 契约、模拟测试和 React 人工追更洗版入口已经完成。
2. M-Team、HDHome、织梦、青蛙四个真实结构脱敏夹具已满足当前版本；新增站点夹具作为后续兼容扩展，不阻塞部署。
3. fnOS 没有旧生产台账，首次部署直接初始化空 SQLite；构建新候选镜像后进入只读实机窗口，保持 Torra 分析与下载闸门关闭。

- SQLite 和种子库第一版已经落地；当前不再把它们列为待编码事项。
- M-Team、HDHome、织梦、青蛙四个真实结构脱敏夹具和 `720p/1080i/1080p/2160p` 解析回归已经完成；429、退避、并发和抓取记录上限已经完成，更多站点样本不阻塞当前版本。
- 临时 SQLite 原子替换演练已经完成；本次 fnOS 首次部署没有旧数据，直接创建空 schema version 3，Docker 重启持久化已经通过。
- Torra 推送幂等、冷却、追更洗版观察状态和外部 job ID 已持久化到 SQLite；RSS 即时分析动作已接入并保持默认关闭。
- Torra 洗版分析、候选下载、job 状态适配器、Emby 基准观察运行时、RSS 唤醒、有限主动调度和阶段 6 人工 API 已完成，继续与普通订阅重搜、完结洗版分离。
- 首次下载成功后按集等待当前版本进入 Emby，再开始追更洗版。每条订阅选择 24 或 48 小时窗口，默认 48 小时；RSS 新条目即时唤醒，主动搜索只在 `12 / 24` 或 `12 / 24 / 48` 小时兜底。
- 当前集窗口固定从 `baseline_ready_at` 计算，下载到更好版本不延长；到期停止当前集自动搜索，下一集入库后建立新窗口，已有历史订阅不补扫。
- 版本高低沿用 Torra 已有版本控制和元数据权重；中控只选择 Torra 返回的正分差最高候选，不重复实现资源评分。
- MoviePilot 使用 NasEmby 原源码，保留独立开关和人工备用入口，默认关闭。
- 本地模拟测试、接口契约评审和 React 页面已完成；构建新候选镜像后才进入 fnOS 实机窗口。

正式设计：

- `docs/superpowers/specs/2026-07-18-sqlite-torra-quality-upgrade-design.md`
- `docs/superpowers/specs/2026-07-18-private-pt-rss-seed-library-design.md`

当前代码阶段执行依据：

- `docs/superpowers/plans/2026-07-18-sqlite-private-rss-seed-library-implementation-plan.md`
- `docs/superpowers/plans/2026-07-18-torra-follow-up-rewash-implementation-plan.md`

### 后续：PT 实机证据

- 将已通过本地验收的候选镜像部署到 fnOS，首次启动仍保持全部写闸门关闭。
- fnOS 正式部署和 HTTPS 源站限制。
- 单条真实订阅与 Torra 预览。
- Torra → qB → 115 秒传 → Symedia → Emby 完整闭环。
- qB 暂停/恢复真实任务验证。
- Emby 证据刷新真实验证。
- 主链稳定后的自动调度。

### 以后版本

- MoviePilot 在用户明确开启后只作为其他 PT 站点人工备用；不得与 Torra 并行下载，本轮不实现自动调度。
- Telegram 频道网盘订阅、HDHive / pansou、影巢、115 分享转存和自动兜底。
- 115 清理和助力。

完整矩阵见 `docs/ROADMAP.md`。

## 5. 当前页面范围

- 总览：媒体与服务状态摘要。
- 影院大厅：沿用 Mineradio 主视觉；媒体抽屉展示媒体库与本库内容。
- 控制室：Emby、qB、Torra、Symedia 核心状态；MoviePilot 显示默认关闭的备用状态。
- 任务中心：订阅 → Torra/qB → 进入 115 → Symedia/Emby 四步证据链，以及最近活动日志。
- 日历：订阅播出与入库进度。
- 内容发现：TMDB、JustWatch、豆瓣、腾讯、优酷、爱奇艺、芒果和全球日播。
- 种子库：私人 PT RSS 最近更新、本地种子搜索和来源管理。
- 我的订阅：顶部独立入口，管理唯一台账并提供 Torra 推送、每条订阅 24/48 小时追更洗版设置和当前窗口状态。
- 订阅设置：NasEmby 自动订阅来源、时间、Torra 追更洗版时间表和 MoviePilot 独立开关。
- 系统设置：PT 主线服务连接、MoviePilot 后续配置和系统级安全状态。

页面文案以家人或偶尔使用者能直接理解为第一层；Torra、qB、Symedia、Emby、TMDB 和关联依据继续保留在服务卡、详情与检查信息中。完整规则见 `docs/UI_STANDARD.md`，本轮设计与实施记录见 `docs/superpowers/specs/2026-07-17-friendly-interface-copy-design.md` 和 `docs/superpowers/plans/2026-07-17-friendly-interface-copy-implementation-plan.md`。

工作页继续使用改造前的 Hero 标题结构。实时数量留在摘要卡、状态卡或列表工具栏中，不再作为标题旁的大号首屏状态。2026-07-17 的状态首屏试验已于 2026-07-18 撤回，其余 CSS 模块化、字体、对比度和键盘改造继续保留。

## 6. 当前安全边界

- 不把服务端已保存的外部凭据回填浏览器；临时输入不写入浏览器存储。
- 危险方法校验 Origin。
- qB 暂停/恢复需要确认、目标复查和结果复查。
- Emby 刷新需要 Symedia 较新证据、执行锁和冷却。
- Torra 推送需要 TMDB 身份、分类、保存路径、下载器 ID、在线查重和独立开关。
- Torra v2 动作还要求明确确认、幂等键、单订阅冷却和服务端重新读取台账。
- Torra 追更洗版还要求 SQLite 领取动作、qB/Torra 活动复查、正分差候选、最小间隔、每日上限和可暂停状态。
- 原 115、Telegram、HDHive 和 provider 核心接口源码保留，生产默认由兼容开关禁用。
- 不开放旧管理页面不等于删除其源码或网盘能力；后续使用新的受认证、受闸门保护 API 合并进当前页面。

## 7. 以后实机阶段

当前不执行。用户明确进入实机窗口后再按顺序进行：

1. 构建包含当前 schema version 3 的候选镜像。
2. 在 fnOS 部署单容器，以空 SQLite 初始化并只读运行。
3. 配置持久目录和 Torra、qB、Symedia、Emby 连接，保持全部写入闸门关闭。
4. 备份持久目录并核对空库初始化结果，只开启订阅写入创建一条测试订阅。
5. 核对分类、保存路径、Torra 查重和下载器 ID。
6. 开启单条 Torra 推送，验证 PT / Torra → qB → 115 → Symedia → Emby。
7. 人工验证一次 Torra 追更洗版分析、候选下载与版本控制后，才开启单条自动观察。
8. 主链和追更洗版均稳定后最后开启订阅调度。
9. MoviePilot 与 Telegram 网盘能力继续默认关闭。

## 8. 不做事项

- 不导入或合并外部 NasEmby 台账。
- 不建立 Node 后端或第二套订阅规则。
- 不在当前代码收口阶段调用真实外部写接口。
- 不删除原 `D:\Projects\媒体控制中心` 归档目录。
- 不把一次性本机运维动作写成长期产品能力；关机只按用户当次明确指令执行。
