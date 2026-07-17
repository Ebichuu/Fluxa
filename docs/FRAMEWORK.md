# 媒体控制中心 v2 框架

状态：Python 单后端和 PT 主线已落地；SQLite 单台账与 Torra 自动版本升级待实施

日期：2026-07-18

## 产品目标

媒体控制中心不是多个工具链接的集合，而是用户唯一进入的控制面：在一个 React 应用中完成内容发现、订阅、日历、任务链观察、服务状态和受保护操作，并把 Torra、qBittorrent、115、Symedia、Emby 串成一条可解释、可检查的 PT 媒体链路。

用户只在媒体控制中心创建和管理订阅。NasEmby 的 Python 源码作为订阅、发现、日历、资源规则和调度的业务基础合并到统一后端，不再要求用户进入第二套页面，也不建立第二套订阅台账。

## 最终技术框架

```text
前端
  React + TypeScript + Vite
  ├─ 总览
  ├─ 影院大厅（Mineradio 原视觉）
  ├─ 控制室
  ├─ 任务中心
  ├─ 日历
  ├─ 发现 / 独立订阅入口 / 订阅设置
  └─ 系统设置

后端
  Python 3.13 + Flask + Gunicorn
  ├─ 整站认证与同源保护
  ├─ React / Mineradio 静态托管
  ├─ NasEmby 发现、订阅、日历、资源规则和调度源码
  ├─ 115 / Telegram / HDHive / pansou 网盘能力
  ├─ Emby / qB / Torra / Symedia 适配器
  ├─ Torra 单一 PT 主通道与安全推送
  ├─ SQLite 订阅仓储与 Torra 版本观察协调器（待实施）
  ├─ 四步任务链聚合
  └─ 活动日志、错误脱敏和安全动作

部署
  一个 Docker 容器
  一个 8787 端口
  一个 Gunicorn worker
  data / db / upload 一个持久根目录下的三个子目录
```

Node.js 只在开发与镜像构建阶段编译 React，不是后端，不进入最终运行镜像。生产容器只有 Python / Gunicorn 常驻进程。

## 业务所有权

| 能力 | 唯一负责人 | 当前说明 |
| --- | --- | --- |
| 内容发现 | NasEmby Python | TMDB、JustWatch 海外流媒体、豆瓣、国内平台、全球日播 |
| 订阅业务 | NasEmby Python | 用户只从媒体控制中心创建和管理，不重写规则 |
| 订阅持久化 | 媒体控制中心 SQLite | 实机前替换 JSON，单一台账、事务和可恢复调度 |
| 日历与资源规则 | NasEmby Python | 直接使用现有可运行源码，不重新猜测业务逻辑 |
| 获取主通道 | Torra + qB | PT 优先 |
| Telegram 网盘通道 | 115 + Telegram + HDHive / pansou | 延期；底层源码和安全 API 保留，当前 React 不展示 |
| 整理入库 | Symedia | 识别、归档、STRM 和入库记录 |
| 媒体库 | Emby | 库内证据、影院大厅和索引状态 |
| 统一观察 | 媒体控制中心 Python | 把外部证据关联成四步任务链 |

## 订阅和获取原则

用户从媒体控制中心创建订阅后，数据直接写入同进程 NasEmby 台账。不存在外部 NasEmby 实例导入、Node 台账或双写。

当前链路：

```text
订阅 → Torra → qB → Torra 秒传 115 → Symedia → Emby
```

- PT / Torra 始终是默认主通道。
- Telegram 网盘能力源码必须保留，但当前不提供页面入口或后台执行器。
- Torra 独占 PT 搜索、qB 编排和 115 秒传；NasEmby `ptto115.py` 不启动。
- Symedia 只负责 115 后整理入库，不接收重复订阅推送。
- Torra 首次下载成功只表示已有可看版本；当前版本进入 Emby 成为洗版基准后才开始质量观察，达到版本控制目标或观察结束后才结束。
- 媒体控制中心自动触发 Torra 原本需要人工执行的洗版分析，只选择 Torra 返回的正分差最高候选；版本优先级仍由 Torra 判断。
- MoviePilot 使用 NasEmby 原源码作为可关闭备用，默认关闭，不承担周播剧版本升级主线。
- 任务中心只展示有证据的状态；无法可靠关联的外部记录标记为“未关联”。
- 普通分类调整或改季只更新订阅台账，不触发 provider 队列。

延期网盘能力的保留代码和以后工作见 `docs/CLOUD_ACQUISITION_PLAN.md`。

## 页面边界

### 视觉与结构边界

- 影院大厅的 Mineradio 视觉、Three.js / GSAP、封面粒子和 shelf 交互。
- 顶部导航允许增减和重排按钮，当前增加“订阅”并由健康按钮进入控制室。
- 影院大厅媒体抽屉允许优化，当前准确区分“媒体库”和“本库内容”并支持移动端关闭。

NasEmby 的旧管理页面不作为第二套用户界面重新公开，但其源码和接口调用关系作为迁移依据保留，不能因为页面不启用就删除对应核心业务能力。

### 工作页

- 总览：PT 主链、异常和最近入库。
- 控制室：Torra、qB、Symedia、Emby 核心状态与安全动作。
- 任务中心：订阅、Torra / qB、进入 115、Symedia / Emby 四步证据和最近活动日志。
- 日历：播出、订阅进度和入库状态。
- 发现：TMDB、JustWatch 海外流媒体、豆瓣、国内平台和全球日播。
- 我的订阅：唯一台账、单条管理、版本观察和 Torra 洗版检查状态。
- 订阅设置：来源、定时、Torra 自动洗版检查时间表和 MoviePilot 独立开关；不混入系统连接设置。
- 系统设置：服务连接、认证方式、全局安全开关和退出。

“订阅”使用独立顶部入口，订阅设置从“我的订阅”进入。海外流媒体属于内容发现，不建立独立页面。

## API 框架

### 当前统一接口

- `/api/discover/*`：发现、搜索和资源搜索。
- `/api/subscriptions/*`：台账、配置、详情、日历和受保护动作。
- `/api/tasks/chain`：统一证据链。
- `/api/media/*`：影院大厅和 Emby。
- `/api/qbittorrent/*`：摘要和可回滚动作。
- `/api/torra/summary`、`/api/symedia/summary`：只读服务证据。
- `/api/internal/nasemby-core/*`：已认证只读诊断，不是第二个服务。

### 新增 v2 安全接口

- `/api/v2/subscriptions/:id/torra-push-preview`：Torra 分类、路径、下载器和在线查重预览。
- `/api/v2/subscriptions/:id/torra-pushes`：固定目标 Torra，要求确认、幂等、冷却和服务端复查。
- `/api/v2/system/metrics`：缓存并脱敏的 CPU、内存、磁盘和网络指标。
- `/api/v2/integrations`：当前只用于 MoviePilot 后续 PT 补齐状态。
- SQLite 与 Torra 自动洗版检查的新 v2 接口仍处于设计状态，见 `docs/API_CONTRACT.md` 的计划章节；未实施前不属于当前契约。
- 115、Telegram、HDHive 和网盘候选/转存 v2 路由继续保留，但当前 `client=false` 且环境闸门关闭。

### 已通过 v2 接入、原实现继续保留的核心能力

- 115 / Telegram / HDHive / pansou：安全适配层和底层源码保留，当前页面延期。
- Torra：连接检查、订阅预览、固定目标推送和搜索触发。
- Symedia：连接检查和整理入库证据，不接收当前订阅推送。
- MoviePilot：连接检查、查重、创建订阅和重搜源码保留，独立开关默认关闭。
- NasEmby 配置与活动日志：保留实际业务需要的配置读写和审计语义。

浏览器不能直接访问外部工具凭据或 API。当前 React v1 契约见 `docs/API_CONTRACT.md`；上述历史核心接口必须建立逐条能力矩阵、模拟契约测试和新接口映射后再接入统一页面。

## 核心源码保留规则

v2 从现在起执行“保留优先”，不再按文件名、旧页面或路由年代直接删除源码。

每个候选删除项必须同时满足：

1. 已确认它不是订阅、发现、日历、规则、调度、PT、网盘、入库或媒体库链路的一部分。
2. 已记录原调用方、输入、输出、副作用、凭据和外部依赖。
3. 已存在功能等价的新入口，或者明确证明该能力不再需要。
4. 新入口已有自动化测试，能够覆盖原接口的关键行为。
5. 文档和接口能力矩阵已经标记替代关系。

在五项证据齐全前，只允许隔离、停止注册或标记待迁移，不允许删除源码。原 NasEmby 管理页面可以不在生产端口注册，但页面源码应作为迁移和接口语义参考保留。

## 数据与调度

- 当前代码仍以 `db/discover_subscription_items.json` 和 `db/discover_subscriptions.json` 为生产订阅文件。
- 进入实机前将一次性迁移为 `db/media_control_center.sqlite3`，迁移成功后 JSON 只保留备份，不双写。
- SQLite 保存订阅、配置、按集质量观察、幂等动作、冷却、调度游标和迁移报告。
- 列表、详情、日历、任务链和调度继续读取同一台账。
- Gunicorn 固定一个 worker、四个线程。
- HDHive 到期检查源码和发现缓存预热线程保留；当前不通过 React 管理 HDHive。
- 订阅调度器只在 `MCC_SUBSCRIPTION_SCHEDULER_ENABLED=true` 时启动。
- Torra 自动洗版检查使用独立开关，默认关闭；默认相对 Emby 基准就绪的计划为 `2 / 6 / 12 / 24 / 48 / 72 小时`，允许自定义。
- 多 worker 或多副本部署前必须增加调度选主和台账并发控制。
- 当前部署的订阅写入、调度、Torra 推送、集成管理、网盘搜索、网盘转存和自动网盘兜底均关闭。

## 安全与部署

- 生产必须配置访问密钥，业务 API、React 和 Mineradio 都受保护。
- 危险方法执行 Origin 校验，并由具体写入开关控制。
- 浏览器不保存外部软件凭据。
- 外部图片代理限制地址、重定向和图片类型。
- 旧页面或旧接口“不默认公开”与“删除源码”是两回事：前者是安全策略，后者必须满足核心源码保留规则。
- 最终镜像只有 Python / Gunicorn 常驻，不含 Node / Express。
- fnOS 通过 `MCC_DATA_ROOT` 持久化 `data`、`db` 和 `upload`。
- 公网访问必须经过 HTTPS 反向代理。

## 当前完成度

### 已完成代码

- Python 统一后端和当前 React v1 兼容契约。
- 单一 NasEmby 订阅台账与调度所有权。
- 内容发现、订阅列表、详情、日历和资源规则的 Python 接入。
- Emby、qBittorrent、Torra、Symedia 的当前只读适配器及受保护动作。
- 四步任务链基础聚合。
- PT/Torra 新默认值和旧资源优先默认配置迁移。
- Torra v2 推送预览、确认、幂等、冷却和脱敏审计。
- 活动日志和 30 秒缓存的 NAS 系统指标。
- 16 条 `/api/v2` 机器契约；网盘相关路由延期保留为 `client=false`。
- 单容器 Docker、Gunicorn 和持久化目录结构。
- Express 运行后端已经退出生产架构。
- 影院大厅主体视觉保护、独立订阅导航和媒体抽屉优化。

### 未完成代码

- SQLite 单台账、一次性 JSON 迁移和崩溃恢复。
- Torra 自动洗版分析、按集版本观察和持久化幂等/冷却。
- MoviePilot 独立开关、安全预览和人工备用入口。
- Telegram 网盘订阅、自动兜底、115 清理和助力；均已延期。
- fnOS 实机结果确认后的真实 115 状态证据接入；当前仍以 Symedia 相邻证据补充。
- 多 worker / 多副本下的调度选主与幂等存储升级。

### 暂缓的实机测试

- fnOS 上创建真实测试订阅。
- Torra 搜索与 qB 下载。
- 115 / Symedia 真实转存。
- Emby 刷新或完整入库闭环。
- MoviePilot 自动补齐和 Telegram 网盘能力真实运行。

实机动作只能在用户明确进入实机测试窗口后，按照 `docs/DEPLOYMENT.md` 逐项开放。当前阶段只做源码恢复、接口梳理、模拟测试、构建和隔离容器验证。

## 后续文档

- 总计划：`docs/PLAN.md`
- 代码路线图：`docs/ROADMAP.md`
- 网盘通道计划：`docs/CLOUD_ACQUISITION_PLAN.md`
- HTTP 契约：`docs/API_CONTRACT.md`
- 核心接口能力矩阵：`docs/CORE_API_CAPABILITY_MATRIX.md`
- 部署与实机闸门：`docs/DEPLOYMENT.md`
- 原始维护资料：`docs/references/`
- 源码与资料完整性清单：`docs/V2_SOURCE_INVENTORY.md`
