# Python 统一后端实施计划

状态：阶段 0 至阶段 8 已完成；阶段 9 等待用户进入真实订阅测试窗口
日期：2026-07-16
设计依据：`docs/superpowers/specs/2026-07-16-python-unified-backend-design.md`

## 目标

在不改现有 React 页面和 Mineradio 视觉的前提下，把当前 Express 门面能力分阶段迁入 NasEmby Python/Flask 运行时，最终形成 fnOS 上一个 Docker 容器、一个端口和一个 Python/Gunicorn 常驻后端。Node.js 只负责镜像构建阶段的 React 编译。

## 固定边界

- 本轮完成统一后端与部署，只做只读验收，不创建真实订阅。
- React 保留；不改影院大厅、顶部导航和媒体队列 UI。
- NasEmby 源码是订阅、发现、日历、资源规则和调度的唯一依据。
- PT/Torra 默认主通道；自动云盘兜底默认关闭。
- 不新建数据库，不顺带引入 Redis、Celery 或消息队列。
- 不执行真实 Torra、qB、115、Symedia、Telegram、HDHive 或 Emby 写动作。
- 任何阶段失败都能回到迁移前归档标签，不回滚或猜测合并订阅数据。

## 阶段 0：冻结与归档（本次完成）

动作：

1. 记录当前 `main` 基线和全部有效工作区改动。
2. 排除 `.env`、日志、缓存、构建产物、真实数据、临时预览和外部参考资料副本。
3. 创建 `codex/pre-python-backend-archive` 分支。
4. 提交当前源码、测试和正式文档。
5. 创建标签 `archive/pre-python-backend-unification-2026-07-16`。
6. 运行 Node、Python、构建、差异和敏感信息检查。

验收：标签能还原 React、Express、NasEmby Core、测试和全部正式设计文档；仓库中不含真实凭据。

## 阶段 1：冻结公开契约（已完成 2026-07-16）

目标：先定义“迁移后必须保持什么”，不先写替代实现。

动作：

- 枚举当前 Express 路由、状态码、超时、认证要求和错误响应。
- 为认证、健康检查、媒体、订阅、发现、任务链、qB、Torra、Symedia 和 Mineradio 建立契约矩阵。
- 把现有 Node 测试拆成与框架无关的 HTTP 契约用例。
- 固化 Mineradio 注入前后 HTML、桥接消息和关键浏览器截图基线。
- 记录 React 实际调用到的字段，删除工作留到路由切换后处理。

验收：当前 Express 全部通过契约；契约覆盖成功、未配置、离线、超时、401/403、上游错误和脱敏。

完成记录：新增 `docs/contracts/http-api-contract-v1.json`，冻结 47 条明确路由的方法、路径、认证、读写属性、主要成功状态、响应类型和迁移分组；`docs/API_CONTRACT.md` 记录兼容性债务与关键请求语义。自动测试会从 Express 路由栈反向提取实际清单，逐条比较契约，并验证 42 条受保护路由均在业务处理前返回统一 `401/AUTH_REQUIRED`；React API 客户端引用也必须命中 `client` 契约。当前未修改任何路由行为。

回滚：本阶段只增加测试和文档，不改变运行路径。

## 阶段 2：建立统一 Flask 应用壳（已完成 2026-07-16）

目标：让现有 NasEmby Core 具备承载整站的应用边界，但暂不移除 Express。

动作：

- 在 `services/nasemby-core/` 内建立统一 Flask 应用工厂和 Blueprint 注册方式。
- 保持 `discover_runtime.py`、`services.py`、`telegram_runtime.py` 和 HDHive 模块原边界。
- 统一配置加载、请求 ID、JSON 错误、超时和日志脱敏。
- 保留 Gunicorn 单 worker、`gthread` 四线程和单实例调度约束。
- 增加统一 `/api/health`，报告应用和依赖状态但不输出凭据。

验收：原 12 项 Python 测试继续通过；新应用壳可在空数据目录启动；后台调度仍只启动一次；全部外部写开关关闭。

完成记录：原 62 条 NasEmby 路由机械迁到 Blueprint，新增可创建独立实例的 `create_app()`，模块级 `app` 和 Gunicorn 入口保持兼容；`app/http_runtime.py` 统一生成和回传 `X-Request-ID`，为 API 404/405/500 提供不含异常文本的 JSON 错误。新增 `/api/health` 只报告六个组件的配置布尔值。归档基线路由逐条比对无缺失，只新增健康路由；Python 测试增至 15 项，创建应用不启动调度器，未触发任何真实外部动作。

回滚：恢复原 Core 入口，不改变订阅文件。

## 阶段 3：迁移整站认证和静态资源（已完成 2026-07-16）

目标：Python 能独立提供受保护的 React 页面，但 Express 仍可作为开发对照。

动作：

- 移植 `MCC_ACCESS_KEY`、HttpOnly 签名 Cookie、固定七天会话、Origin 校验、请求体限制和登录频率限制。
- 用 Python 提供 React `dist/`、SPA 回退和静态资源缓存策略。
- Vite 开发服务器通过代理连接 Python；生产不启动 Vite 或 Node。
- 验证登录保护覆盖页面、静态资源和全部 `/api`。

验收：原认证契约全部通过；未登录不能读取 React、Mineradio 或业务 API；开发环境未配置密钥时仍可按现有规则关闭认证。

完成记录：Python 已实现与 Express 相同的访问密钥校验、v1 HMAC Cookie、七天固定会话、五次失败锁定、Origin/CORS、安全重定向、严格登录页 CSP 和一层生产反向代理信任。显式配置 `MCC_FRONTEND_DIST` 后，Python 提供 React 首页、SPA 回退和 Vite 哈希资产缓存，且不存在的 `/api/*` 保持 JSON 404。固定签名向量完成双向兼容验证，Express 可接受 Python 会话。当前 Compose 未传入前端目录或访问密钥，对外入口尚未切换；影院大厅、顶部导航和媒体队列未修改。

回滚：生产入口仍可切回 Express；不触碰业务写入。

## 阶段 4：迁移 Mineradio 桥接（已完成 2026-07-16）

目标：只更换 `/mineradio/embed` 和资源提供者，不改变大厅体验。

动作：

- 逐段移植当前 Express 的 base 注入、嵌入样式、音乐业务拦截和 `postMessage` 桥接。
- 保持 `vendor/mineradio-public/` 目录、原始 Three.js/GSAP 代码和 React 消息名不变。
- 为 HTML 注入结果增加语法和契约测试。
- 浏览器复验媒体库切换、队列点击、滚轮、方向键、冷启动移动端和 WebGL 画布。

验收：桌面与手机截图基线无非预期差异；影院大厅、顶部导航和媒体队列没有样式改动；`/mineradio/embed` 与 `/api/media/home` 正常。

完成记录：新增独立 `app/mineradio_runtime.py`，按原优先级解析 `MINERADIO_PUBLIC_DIR`、内置资源和 Windows 开发回退，并使用安全路径解析提供 `/mineradio/*`。注入头与桥接尾保存为 Python 运行时片段，自动测试逐字对照 Express 模板；五项尺寸替换保持 JavaScript 首次替换语义。Python 使用真实内置 `index.html` 返回完整嵌入页，注入脚本语法、整站认证、缺失首页和静态资产均有覆盖。浏览器完成桌面与 390×844 移动视口复核，原星河/WebGL 画布和视觉设置入口正常，控制台 0 条错误。未修改 React、影院大厅、顶部导航、媒体队列、Three.js/GSAP 或原 Mineradio 资源，Compose 与公开入口继续保持原状。

回滚：单独把 Mineradio 路由切回 Express，不影响订阅和任务链。

## 阶段 5：迁移只读外部适配器（已完成 2026-07-16）

目标：先迁移无副作用能力。

顺序：

1. Emby 媒体库、最近入库和索引读取。
2. qBittorrent 版本、速度和任务摘要。
3. Torra 订阅摘要和查重读取。
4. Symedia 转存历史摘要。
5. TMDB、外部图片代理和其他只读辅助接口。

每个适配器都保留当前超时、认证刷新、缓存、错误状态和脱敏规则。使用模拟服务跑契约；真实服务只做用户已授权的只读复验，不执行写动作。

进度记录（Emby，2026-07-16）：已迁入首页媒体库与最近条目、控制室概览、外部图片代理、Emby 图片代理和内部 TMDB 在库索引。API Key + User ID 与账号密码两种认证均保留，密码 Token 进程缓存、401/403 单次重登、12 秒超时和 10 分钟在库索引缓存已覆盖；网络异常不会把查询中的 API Key 带入浏览器错误。首页示例 ID 与 Express 源码逐项比对，图片代理校验 JPEG/PNG/GIF/WebP/AVIF 魔数并拒绝私网 IP、凭据 URL、重定向和私网 DNS 解析。当前未切 Compose，本次只用固定模拟响应且未再次访问真实 Emby，刷新状态及刷新 POST 留在阶段 6。

进度记录（qBittorrent，2026-07-16）：已迁入 `GET /api/qbittorrent/summary`。单次摘要执行一次可选账号登录，版本、传输信息和任务列表并行读取并共享该 Cookie；10 秒超时、非 2xx、无效 JSON 和网络错误均返回脱敏离线摘要。任务字段、五种状态、中文标签、计数与排序按 Express 固定样本覆盖。Python 未注册暂停/恢复 POST，本次未连接真实 qBittorrent。

进度记录（Torra，2026-07-16）：已迁入 `GET /api/torra/summary`、内部订阅列表和在线查重读取。固定 Token 与账号密码换 Token 均保留；密码模式进程缓存 Token，401/403 最多清理并重登一次，15 秒超时和脱敏网络错误已覆盖。订阅列表兼容三种上游包络，计数及 TMDB/类型/季号优先的身份匹配保持 Express 语义。Python 不包含保存订阅、运行搜索或其他 Torra 写方法，本次未连接现网 Torra。

进度记录（Symedia，2026-07-16）：已迁入 `GET /api/symedia/summary` 和内部转存历史分页读取。固定 Token 与账号密码 Token 单次刷新、15 秒超时、第一页 50 条、最多 20 页今日统计、第一页失败数及最近 5 条字段均按固定样本覆盖。阶段 5 的四组外部只读适配器与图片辅助接口全部完成，当前 Compose 仍未切换，所有真实写动作保持原闸门。

验收：React 不改页面即可读取 Python 响应；同一固定样本与 Express 的业务字段一致；离线不显示假在线或示例成功。

回滚：按适配器逐个切回 Express。

## 阶段 6：迁移任务链和可回滚动作（已完成 2026-07-17）

目标：把当前中控独有的聚合与安全动作移到 Python。

动作：

- 移植任务链身份、关联置信度、四步状态、115 邻接推断、卡住原因和未关联记录。
- 移植 qB 暂停/恢复：hash 校验、20 项上限、目标复查、结果复查和脱敏审计。
- 移植 Emby 证据驱动刷新：Symedia 证据、执行锁、10 分钟持久冷却和 202 语义。
- Node 与 Python 不同时执行动作；写接口切换采用单开关、单目标。

验收：任务链固定样本和边界样本结果一致；动作测试只使用模拟 qB/Emby；浏览器只打开确认层并取消，不触发现网写入。

完成记录：Python 已直接从 NasEmby 只读订阅快照构建四步任务链，保留 TMDB、文件名、季号、115 邻接推断、未关联行和 qB 控制摘要。qB 暂停/恢复保留 40 位 hash 校验、去重、20 项上限、整批存在性检查、动作前后复查和脱敏审计；Emby 刷新保留 Symedia 北京时间解析、证据复查、非阻塞执行锁、10 分钟持久冷却、同证据去重和 202 语义。Python 测试增至 63 项，Node 契约 57 项与生产构建通过；React、影院大厅、顶部导航、媒体队列和原 Mineradio 资源未修改。全部写动作只使用模拟客户端，本次未连接真实 qB、Emby 或 Symedia。

回滚：关闭 Python 动作路由并恢复 Express 路由；持久冷却状态保留，避免回滚后重复动作。

## 阶段 7：收口订阅与调度所有权

状态：已完成 2026-07-17

目标：确认 NasEmby Python 是唯一订阅业务，不再保留 Node 兼容写路径。

动作：

- 确认发现、订阅、配置、日历、资源搜索和活动日志全部直接由 Python 提供。
- 删除或归档 `SubscriptionStore`、`AutoSubscribeRunner`、Node 发现抓取和 Torra 推送等过渡实现。
- 移除 Express 到内部 Core 的代理层，因为 Core 已成为统一后端本身。
- 保持订阅台账原文件和单实例调度，不做数据迁移。
- 再次核对默认 `pt_only` 与自动云盘兜底关闭。

验收：进程检查只有一套调度器；任何订阅动作只写 NasEmby 原台账；关闭 Python 后系统明确不可用，不回退 Node 写入。

完成记录：新增 Python 发现、订阅和内部诊断兼容层，47 条冻结路由全部由 Python 提供。React 公共响应使用字段白名单映射；订阅保存转换为 NasEmby 原 `item`，分类和改季直接更新唯一台账且不排队 provider。部署默认关闭订阅写闸门和调度器；旧 115、Telegram、HDHive、缓存预热和 provider 管理入口在统一端口返回 404。测试使用临时台账和模拟 Torra 客户端，未连接真实服务执行写动作。

回滚：使用上一阶段镜像恢复路由；不得重新开启两套调度器。

## 阶段 8：单容器 Docker 收口

状态：已完成 2026-07-17

目标：fnOS 最终只运行一个应用容器和一个对外端口。

动作：

- Dockerfile 改为 Node/Vite 构建阶段 + Python 3.13 slim 运行阶段。
- 运行镜像只复制 React 构建产物、Mineradio 资源、Python 源码和依赖。
- Compose 删除 Express 与内部 Core 的双服务关系，只保留媒体控制中心服务。
- 将 fnOS 一个持久根目录映射为应用的 `data/`、`db/`、`upload/` 子目录。
- 健康检查改为统一 `/api/health`。
- 编写备份、升级、回滚和恢复说明。

验收：容器内无常驻 Node 进程；只开放一个端口；重启后订阅和运行状态保留；Gunicorn 只有一个 worker 和一套后台调度线程。

完成记录：根 Dockerfile 已改为 Node `web-build` + Python 3.13 runtime；Compose 只保留 `media-control-center` 一个服务和 8787 一个端口，并通过 `MCC_DATA_ROOT` 持久化 `data/db/upload`。本地真实镜像构建和隔离容器冒烟通过：未登录 API 401、登录后 React 与订阅读取 200、订阅写入 403、legacy 写入口 404、运行时报告 Python、容器无 Node 可执行文件、Gunicorn 常驻、重启健康恢复且临时持久标记保留。随后删除 `server/`、Express 依赖、服务端 TypeScript 配置和旧 Node 后端测试；React/Vite 构建链保留。

回滚：恢复双服务 Compose 和上一个镜像；挂载同一份只读备份验证后再恢复写入。

## 阶段 9：受控实机验收

状态：未开始。根据用户决定，本轮只完成部署和只读验收。

前提：用户明确进入实机测试窗口。此前所有写开关保持关闭。

顺序：

1. fnOS 单容器启动、登录、页面和服务只读检查。
2. 从媒体控制中心创建一条测试订阅。
3. 验证 PT/Torra 查重、搜索和 qB 下载。
4. 验证进入 115、Symedia 处理和 Emby 入库证据。
5. 验证任务中心展示同一条完整链路。
6. 云盘自动兜底继续关闭；另开测试窗口验证人工资源搜索。
7. 只有 PT 抑制、防重复和状态核对全部可靠后，才单独评估自动云盘兜底。

验收：单条“中控订阅 → PT/Torra → qB → 115 → Symedia → Emby”闭环成立，不重复订阅、不重复下载、不误报完成。

## 每阶段通用校验

```powershell
npm test
npm run build
python -m unittest discover -s tests -v
git diff --check
```

Python 命令在 `services/nasemby-core/` 执行。除此之外，每阶段必须完成：

- API 契约和错误脱敏检查。
- `.env`、密码、Token、Cookie、API Key 和真实服务地址扫描。
- 影院大厅、顶部导航、媒体队列的浏览器回归。
- Docker 外部写开关检查。
- 文档、补丁记录和回滚说明同步。

## 最终删除条件

以下删除条件已于 2026-07-17 全部满足：

- Python 已覆盖全部公开路由和静态资源。
- React 在生产环境不再请求 Express。
- 契约、Node 历史回归、Python 测试和浏览器回归全部通过。
- 单容器重启和持久化通过。
- 迁移前标签和最后一个双服务镜像均可恢复。

Express 运行代码和依赖已经删除。Node/Vite 仅作为 React 构建工具保留；最终 Docker 镜像不含 Node。
