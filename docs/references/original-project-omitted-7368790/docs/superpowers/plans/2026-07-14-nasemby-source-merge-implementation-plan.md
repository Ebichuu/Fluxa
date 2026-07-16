# NasEmby 订阅与发现源码合并实施计划

状态：阶段 0、2、3、4、5 完成；阶段 1 源码纳入完成，Docker 镜像构建待 Docker Hub 网络恢复后复验
日期：2026-07-14
设计依据：`docs/superpowers/specs/2026-07-14-nasemby-source-merge-design.md`
源码基线：`D:\Projects\NasEmby_friend_clean\NasEmby_friend_clean_20260630_171606`

## 目标

把 NasEmby 的 Python 订阅、发现、日历、资源搜索、调度和 Torra 推送源码合并进媒体控制中心项目。最终用户只访问媒体控制中心，所有相关页面由现有 React 应用渲染；NasEmby Core 作为内部业务运行时，不使用 iframe、独立页面或外部跳转。

实施遵循“先纳入源码、再切只读、然后切写入、最后停旧调度器”。在用户明确进入实机测试阶段前，不调用真实 Torra、115、Symedia 或其他外部写接口。

## 阶段 0：冻结边界与建立基线

### 任务

1. 记录当前媒体控制中心测试、构建、订阅条数和发现来源快照。
2. 记录 NasEmby 源码目录、依赖版本、API 列表、数据文件和调度线程。
3. 备份但不修改：
   - 中控 `data/subscriptions.json`
   - 中控 `data/subscription-config.json`
   - NasEmby `data/`
   - NasEmby `db/`
   - NasEmby `upload/`
4. 确认所有真实写开关关闭：
   - `TORRA_PUSH_ENABLED=false`
   - `ENV_TORRA_AUTO_SUBSCRIBE=0`
   - `ENV_SYMEDIA_AUTO_SUBSCRIBE=0`
   - 115、Telegram、HDHive 自动操作关闭
   - 自动云盘兜底关闭

### 验证

- `npm test`
- `npm run build`
- 备份目录可读且不在 Git 跟踪范围。
- 仓库内没有真实密码、Token、Cookie、电话号码或服务地址。

## 阶段 1：纳入 NasEmby Core 源码

### 目标文件

- 新增 `services/nasemby-core/app/`
- 新增 `services/nasemby-core/requirements.txt`
- 新增 `services/nasemby-core/Dockerfile`
- 新增 `services/nasemby-core/patches/README.md`
- 修改 `docker-compose.yml`
- 修改 `.gitignore`
- 修改 `README.md`

### 任务

1. 从已确认可运行的 NasEmby 项目复制 Python 源码和依赖文件，保留原目录边界和模块名。
2. 不复制真实 `data/`、`db/`、`upload/` 内容，只创建忽略目录或卷挂载点。
3. `docker-compose.yml` 增加内部 `nasemby-core` 服务：
   - Python 3.13。
   - 只在 Docker 网络内暴露 `12388`。
   - 挂载独立持久卷或明确宿主目录。
   - 自动推送和辅助通道默认关闭。
4. `patches/README.md` 记录源码基线、复制日期和后续补丁格式。
5. 本地开发允许通过 `NASEMBY_CORE_URL=http://127.0.0.1:12388` 连接独立启动的 Python 服务；生产使用 `http://nasemby-core:12388`。

### 验证

- NasEmby Core 使用空配置和副本数据可以启动。
- `GET /api/status`、发现只读接口和订阅列表接口可返回。
- 不开放真实外部写动作。
- 中控现有页面和 API 不受影响。

当前状态（2026-07-15）：Core 镜像已用缓存的 `python:3.13-slim` 构建成功，空命名卷容器健康，`GET /api/status` 返回 200，内部端口和九个关闭的外部自动动作均已核对。完整双服务构建仍被 Docker Hub OAuth 网络失败阻塞，`node:20-alpine` 无本地缓存。另发现 Core 仍使用 Flask 开发服务器，且三个调度线程仅由 `python -m app.main` 启动；生产 WSGI 与单实例调度所有权需单独设计，不能在本阶段直接替换启动命令。

## 阶段 2：建立 Express 内部门面

### 目标文件

- 新增 `server/adapters/nasembyCoreAdapter.ts`
- 新增 `server/routes/nasembyCoreRoutes.ts`
- 修改 `server/config.ts`
- 修改 `server/index.ts`
- 修改 `server/routes/healthRoutes.ts`
- 修改 `src/types/media.ts` 或新增 `src/types/nasemby.ts`
- 修改 `tests/core-stability.test.ts`

### 任务

1. 新增内部配置：
   - `NASEMBY_CORE_URL`
   - 迁移期只读/写入开关，默认只读且写入关闭。
2. 适配器只负责：
   - 请求超时。
   - JSON 契约透传。
   - 内部 URL 隐藏。
   - 错误正文截断和凭据脱敏。
3. 增加只读健康检查、发现来源、订阅列表、详情和日历门面。
4. 暂不替换现有公开 `/api/discover` 和 `/api/subscriptions`，先使用内部迁移命名空间验证。
5. 写接口即使被请求，也必须在迁移期开关关闭时返回明确的禁止状态。

### 验证

- 模拟 NasEmby 在线、离线、超时、非 JSON 和 500 响应。
- 错误不泄露内部 URL、Token、Cookie 或完整上游正文。
- `npm test` 与 `npm run build` 通过。

## 阶段 3：把 JustWatch 迁入 NasEmby Core（已完成 2026-07-15）

### 来源与目标

- 来源：`server/services/discoverSourceService.ts` 中 TMDB watch-provider 逻辑。
- 目标：`services/nasemby-core/app/discover_runtime.py`
- 目标：`services/nasemby-core/app/main.py`
- 目标：NasEmby 发现契约测试。

### 任务

1. 在 NasEmby 发现运行时增加 `streaming` / 海外流媒体来源。
2. 迁入已经核对的 US 区平台和 provider ID。
3. 复用 NasEmby 的 TMDB 请求、缓存、图片和媒体标准化结构。
4. 响应字段与 NasEmby 其他发现来源保持一致。
5. 订阅动作继续走 NasEmby `/api/subscriptions/save`。
6. 暂时保留 Node JustWatch 实现，仅用于双读对比；完成验收后再退出。

### 验证

- Netflix、Disney+、HBO Max、Prime Video、Apple TV+、Hulu、Paramount+、Peacock 均能生成正确查询。
- 海外流媒体与其他来源显示在同一个内容发现响应中。
- 同一媒体保存后只进入 NasEmby 订阅台账。

## 阶段 4：切换内容发现只读数据（已完成 2026-07-15）

### 目标文件

- 修改 `src/components/pages/DiscoverPage.tsx`
- 修改 `src/services/api.ts`
- 修改 `src/types/subscriptions.ts`
- 修改 `server/routes/discoverRoutes.ts`
- 修改 `src/styles/global.css`

### 任务

1. 保留当前媒体控制中心工作页壳层和顶部导航。
2. 按 NasEmby `templates/index.html`、`static/app.js` 逐项合并：
   - 全球日播、TMDB、豆瓣、腾讯、优酷、爱奇艺、芒果、海外流媒体。
   - 搜索、来源筛选、分页和订阅状态。
   - 资源搜索和资源预览。
3. React 只消费 NasEmby Core 响应，不在前端重新推导业务规则。
4. 写动作仍保持禁用；订阅按钮只验证交互和请求载荷，不落真实数据。
5. 影院大厅、媒体队列和顶部导航不修改。

### 验证

- NasEmby 原七个来源和 JustWatch 都能在同一“内容发现”页面切换。
- 资源搜索和预览使用模拟响应。
- 1440×900、1024×768、390×844 无横向溢出。
- 浏览器控制台无错误或警告。

## 阶段 5：切换订阅、详情和日历只读数据（已完成 2026-07-15）

### 目标文件

- 修改 `src/components/pages/DiscoverPage.tsx`
- 修改 `src/components/pages/CalendarPage.tsx`
- 修改 `src/services/api.ts`
- 修改 `src/types/subscriptions.ts`
- 修改 `server/services/taskChainService.ts`
- 修改 `server/routes/taskRoutes.ts`
- 新增迁移审计脚本到 `server/scripts/` 或 `services/nasemby-core/scripts/`

### 任务

1. “我的订阅”改读 NasEmby：
   - 电影订阅。
   - 电视剧订阅。
   - 被屏蔽订阅。
   - 状态、更新时间、年份和关键词筛选。
   - 详情、演职员、季集和入库路径。
2. 日历页面改读 NasEmby 原日历接口。
3. 任务链订阅主干改读 NasEmby Core，不再读取本地 `SubscriptionStore`。
4. 编写只读差异报告：
   - 条目总数。
   - TMDB ID、媒体类型、季号。
   - 手动订阅、自动订阅和屏蔽项差异。
   - 冲突项和无法迁移项。
5. 不自动写入或合并冲突数据。

### 验证

- 发现页、日历和任务中心显示同一订阅数量和身份。
- 任务链仍能关联 Torra、qB、Symedia、Emby。
- 无法关联项保留为“未关联”，不猜测合并。
- 旧中控订阅 JSON 没有被修改。

## 阶段 6：切换订阅写入（已完成 2026-07-15）

### 目标文件

- 修改 `server/routes/subscriptionRoutes.ts`
- 修改 `src/services/api.ts`
- 修改 `src/components/pages/DiscoverPage.tsx`
- 修改 `src/components/pages/SettingsPage.tsx`
- 修改 NasEmby Core 契约测试

### 任务

1. 保存、删除、屏蔽、取消屏蔽、清空、配置和手动执行全部指向 NasEmby Core。
2. 恢复 NasEmby 原页面动作：搜索资源、推送 Torra、复制标题等。
3. 真实外部写开关继续关闭，使用模拟 Torra/115/Symedia 验证。
4. 写请求不自动重试；未知结果先重新读取状态。
5. 中控 `SubscriptionStore` 和配置文件改为只读备份，不再写入。
6. 活动日志明确区分中控动作和 NasEmby 订阅业务动作。

### 验证

- 每个写操作只改变 NasEmby 副本数据。
- 中控旧 JSON 校验和保持不变。
- 已有 Torra 订阅与新订阅语义对齐 NasEmby 源码。
- 真实 Torra、115、Symedia 未被调用。

## 阶段 7：停止重复调度和退出旧实现（已完成生产断路，2026-07-15）

### 目标文件

- 修改 `server/index.ts`
- 修改或移除 `server/services/autoSubscribeRunner.ts` 的启动引用
- 清理 `server/services/subscriptionStore.ts`
- 清理 `server/services/subscriptionConfigStore.ts`
- 清理 `server/services/subscriptionPush.ts`
- 清理 `server/services/discoverSourceService.ts` 重复来源
- 清理 `server/services/doubanSource.ts`
- 更新 `README.md`、`docs/PLAN.md`、`docs/IMPLEMENTATION_SOURCES.md`

### 任务

1. 停止中控 `AutoSubscribeRunner`。
2. 确认只有 NasEmby Core 启动订阅调度线程。
3. 移除无引用的 TypeScript 订阅写入和重复来源实现。
4. JustWatch 完成切换后删除 Node 平行实现。
5. 保留迁移备份和只读审计工具，不保留第二条生产写路径。
6. 所有删除先通过 `rg` 引用检查和完整构建。

### 验证

- 代码库中只有一套订阅数据路径和调度器。
- 自动订阅不会重复执行。
- 任务链、发现、日历和设置均使用 NasEmby Core。
- 测试、生产构建和浏览器验收通过。

## 阶段 8：实机测试闸门

只有用户明确确认实机测试窗口后执行写动作；只读连通性和数据核对可先推进：

1. 使用中控临时 Core 数据目录验证订阅保存、重新读取和任务链映射；不导入外部台账。
2. 核对 Torra、115、Symedia、Telegram、HDHive 配置，但不输出凭据。
3. 先验证一条只读订阅与任务链。
4. 再由用户选择一条可控订阅执行手动动作。
5. 自动 Torra 推送、Symedia 推送和云盘兜底最后单独开启。

当前状态（2026-07-16 修正）：qB、Torra、Symedia、Emby 的外部只读预检已完成，真实写动作未开始。NasEmby 是中控内部引擎，不需要从源码备份或外部实例寻找真实台账；用户将在媒体控制中心直接创建订阅，数据写入 Core 持久卷。当前只等待受控实机窗口验证单条真实链路。

## 全程质量与安全检查

每个阶段完成后运行：

```bash
npm test
npm run build
git diff --check
```

NasEmby Core 增加 Python 契约测试后，同时运行对应 Python 测试命令。浏览器验收覆盖桌面、平板和手机。

固定检查：

- 真实账号、密码、Token、Cookie、电话号码、频道信息和服务地址未写入仓库。
- 真实外部写开关保持关闭。
- 影院大厅、Mineradio iframe、媒体队列和顶部导航未修改。
- 不以“请求已提交”冒充“外部任务已完成”。
- 不因 NasEmby Core 离线而回退到旧 TypeScript 写入。

## 文档同步

每阶段更新：

- `README.md`
- `docs/PLAN.md`
- `docs/IMPLEMENTATION_SOURCES.md`
- 本实施计划的阶段状态
- `services/nasemby-core/patches/README.md`

任何 NasEmby bug 修复必须记录：原文件、问题证据、修改原因、行为差异、测试和回滚方法。

## 2026-07-14 实施记录

### 阶段 0：已完成

- 中控基线 `npm test` 24/24 通过。
- 中控生产构建通过。
- 中控本地订阅文件只检查存在性和大小，未输出内容。
- NasEmby 干净源码共 28 个 `app/` 文件；未复制预览或外部实例台账，生产订阅将由用户在媒体控制中心创建并写入 Core 持久卷。
- 本机 Python 为 3.14.3，Docker CLI / Compose 已安装；HDHive 运行时仍以 Docker Python 3.13 为准，不能用本地 Python 3.14 或已有的 Python 3.11 镜像代替。

### 阶段 1：基本完成

- NasEmby `app/`、`requirements.txt`、`Dockerfile`、`.env.example` 已机械复制到 `services/nasemby-core/`，初始哈希对比 0 个差异。
- `data/`、`db/`、`upload/` 未复制，并由模块 `.gitignore` 排除。
- 已补充模块 `README.md`、`DESIGN.md`、`patches/README.md` 和 `SECURITY_REVIEW.md`。
- 安全补丁：MoviePilot 自动订阅和 Telegram 订阅通知默认改为关闭；Compose 显式关闭全部外部自动动作。
- Python 源码语法编译通过；模块契约测试 3/3 通过；模块完整性检查 0 错误、0 警告。
- Compose 固定英文项目名，解决中文项目目录无法生成 Compose 项目名的问题；`docker compose config` 通过。
- 已启动 Docker Desktop 并执行 `docker compose build nasemby-core`。构建在拉取 `python:3.13-slim` 元数据时因 Docker Hub 鉴权地址网络不可达而失败，尚未进入依赖安装或源码构建步骤。该项不影响源码纳入，但必须在进入依赖 Python 3.13 运行时的联调前复验。
- 尚未修改媒体控制中心 API、React 页面、订阅数据源或调度器；未调用真实外部写接口。

### 阶段 2：已完成

- 新增 `NasembyCoreAdapter` 和 `/api/internal/nasemby-core/*` 内部迁移命名空间，公开发现、订阅、日历和任务链 API 尚未切换。
- 只读门面已覆盖状态、四个原发现来源、订阅列表、订阅详情和日历；健康摘要可识别 NasEmby Core 是否已配置。
- 写入闸门 `NASEMBY_CORE_WRITE_ENABLED` 默认关闭，迁移期写请求返回 403；未知来源或路由返回 404。
- 未配置/不可达、上游错误、非法 JSON、响应过大和超时分别使用脱敏的 503、502、502、502 和 504 错误，不回传内部 URL、Token、Cookie 或上游正文。
- Node 回归测试 30/30 通过，生产构建和 `git diff --check` 通过；Python 契约测试 3/3、语法编译、Compose 配置、模块完整性和阶段 2 TypeScript 安全扫描通过。
- 未修改影院大厅、媒体队列、顶部导航或其他 React 页面；未调用任何真实外部写接口。

### 阶段 3：已完成

- 将过渡 Node 实现中的八个 US 区 TMDB watch-provider ID 迁入 `services/nasemby-core/app/discover_runtime.py`，包括 Paramount+ 的联合 ID `2303|2616`。
- NasEmby Core 新增 `fetch_streaming()` 和 `GET /api/discover/streaming`，复用原 TMDB discover、分页、图片、媒体标准化、库内状态回填和 SQLite 发现缓存；缓存分类独立为 `streaming`。
- provider 仅允许八个已核对键，未知值回退 Netflix；请求固定 `watch_region=US`，响应提供 JustWatch 平台标签。订阅动作未改，仍统一进入 NasEmby `/api/subscriptions/save`。
- Express 内部门面已允许 `streaming` 来源。Node 过渡实现暂时保留用于阶段 4 双读对比，没有新增第二套订阅台账。
- Python 契约测试 6/6、Node 回归 30/30、Python 语法编译和生产构建通过；测试覆盖八个平台 ID、未知 provider 回退和实际 TMDB 查询参数。
- 本阶段未修改任何 React 页面，未请求真实 TMDB 或执行 Torra、115、Symedia、Telegram 写动作。

### 阶段 4：已完成

- 公开 `/api/discover/browse` 和 `/api/discover/search` 在配置 NasEmby Core 时读取 Core；TMDB、JustWatch、豆瓣、全球日播及四个国内平台均通过同一字段映射进入现有 React 工作页。
- 新增筛选兼容层，只把中控现有枚举映射为 NasEmby 原中文参数，不在 Node 或 React 重算发现业务；Core 未配置时保留旧 Node 只读来源作为本地开发回退，Core 已配置但离线时不回退。
- 新增 `/api/discover/resources/search`，读取 NasEmby 原资源搜索结果；公开响应使用字段白名单移除 `raw` 上游对象和内部错误，只保留来源、标题、画质、大小、季集、链接和预览文本。
- 发现卡增加资源搜索和本地预览；不调用 NasEmby 的资源预览 POST，不提供转存动作。订阅按钮只生成并显示请求预览，删除、屏蔽、执行一轮和改季均禁用且不会发写请求。
- 订阅列表和详情仍读取旧公开接口，等待阶段 5 切读 NasEmby；旧 Node 发现实现暂不删除，供迁移对照和无 Core 的本地开发使用。
- Node 回归测试 33/33、Python 契约测试 6/6、生产构建和语法编译通过。浏览器使用离线 Core 模拟响应完成八个来源切换、资源结果/预览、上游对象脱敏和只读动作验收。
- 1440×900、1024×768、390×844 均无横向溢出，浏览器控制台 0 错误/警告；影院大厅、媒体队列和顶部导航文件无改动。

### 阶段 5：已完成

- 公开 `/api/subscriptions/items|detail|calendar` 在配置 Core 后读取 NasEmby；列表、详情和日历响应使用字段白名单，Core 已配置但离线时返回脱敏错误，不回退旧中控台账。
- 修复只读边界：NasEmby 基线的 `include_progress=1` 会删除完成项并回写台账；Core 现支持内部 `read_only=1`，仍计算进度但不删除、不持久化，原默认行为不变。补丁已记录到 `services/nasemby-core/patches/README.md`。
- “我的订阅”恢复电影、电视剧、被屏蔽订阅，以及状态、更新时间、年份、关键词筛选；详情恢复元数据、演职员、全部季集、分集入库状态和入库路径。搜索资源保持只读，屏蔽、删除、改季和执行一轮继续禁用。
- 日历改读 NasEmby 原日历契约，增加全部/电视剧/电影类型切换；Core 故障显示“订阅引擎不可用”，不再用示例数据掩盖故障。
- 任务链在配置 Core 时读取同一订阅快照，订阅证据标记为 `NasEmby Core`；无可靠身份或无法关联的 qB/Symedia 条目继续显示“未关联”，不猜测合并。
- 配置 Core 后，公开订阅 POST/PUT/PATCH/DELETE 在写入尚未迁移时统一返回 403/501，不会旁路写入旧 `SubscriptionStore`。Core 未配置的本地开发回退保持不变。
- 新增 `npm run audit:nasemby-subscriptions`：只向终端输出两边总数、TMDB ID/类型/季号、manual/auto/unknown、屏蔽差异、重复、冲突和无法映射摘要，不写文件、不修改台账。
- Node 回归 40/40、Python 契约 7/7、生产构建通过；发现页 1920 / 1440 / 1180 / 900 / 390px 断点无横向溢出，订阅标签、筛选、详情、屏蔽和日历类型切换通过。
- 浏览器验收以临时本地 Core 模拟订阅主干，并只读接入真实 qB、Torra、Symedia、Emby 摘要；未调用 Torra、115、Symedia、Telegram、Emby 或 qB 写接口。页面无脚本异常，影院大厅启动时只有浏览器 Web Audio 自动播放限制提示；影院大厅、媒体队列和顶部导航未修改。

### 阶段 6：已完成

- `NasembyCoreAdapter` 增加无自动重试的受限 JSON POST，请求体上限 512 KiB，沿用响应上限、统一超时和脱敏错误。
- 公开保存、删除、屏蔽、取消屏蔽、清空、配置和手动执行全部切向 NasEmby Core；改季严格复用原保存条目语义，以 `target_season`、`season_number` 和 `season_name` 更新原条目。
- 删除和屏蔽会先从 Core 重新读取原条目，再按源码契约传 `{ key, item }`；未知结果不在门面自动重试。
- 发现页恢复订阅、删除、屏蔽、取消屏蔽、改季和执行一轮，均提供确认和动作状态；设置页清空要求输入完整确认短语。
- 可变内存模拟 Core 已验证新增、删除、执行和重新读取；旧 `data/subscriptions.json`、`data/subscription-config.json` 前后 SHA-256 一致。
- 真实 Torra、115、Symedia、Telegram、Emby 写动作未调用；Torra 人工推送继续保持关闭，等待实机闸门阶段。

### 阶段 7：生产断路已完成

- 配置 `NASEMBY_CORE_URL` 后不再启动 Node `AutoSubscribeRunner` 定时器，只有 NasEmby Core 承担订阅调度。
- Core 写闸门开启时，公开路由只允许已迁移的保存、删除、屏蔽、取消屏蔽、清空、配置、改季和手动执行；旧 Torra push、分类覆盖和一次性导入等 Node 写入口统一返回 404。
- `SubscriptionStore`、`SubscriptionConfigStore` 和旧 runner 文件暂时保留，仅供未配置 Core 的本地开发回退、迁移审计和回归测试，不再构成配置 Core 后的生产写路径。
- 物理删除旧文件延后到迁移备份保留期结束，避免同时破坏审计工具和无 Core 开发模式；此决定不影响生产单写源约束。
- 设置页随后按 NasEmby 原前端和 Python 保存函数复核：补齐 `mode`、`resource_rules.enabled`、`max_per_run` 以及规则组，默认预览选择 `torra`；移除与订阅中枢无关的大厅视觉设置卡。
- 订阅表单从系统设置迁到独立中控工作页，由“发现 → 我的订阅 → 订阅设置”进入；系统设置仅保留连接与全局安全策略，顶部导航未新增项目。
- 订阅页继续按原源码补齐五种模式、四组来源和资源规则三态；配置保存固定 `mode_switch_push=false`。“使用最新规则”仅修改未保存表单，“同步全球日播”在阶段 8 前保持禁用。
- Emby 地址于 2026-07-15 首次测试因 IPv6 不可达在认证前超时；网络恢复后复验通过：API Key、测试用户、31 个媒体库和 9,090 个电影/剧集条目均可只读访问。账号密码临时认证成功后立即注销，未保存地址、密码、API Key 或 Token，也未执行媒体库刷新。
- 阶段 8 已完成外部四服务只读预检：qB、Torra、Symedia、Emby 均能由中控实时读取；Core 继续使用模拟数据，真实暂停/恢复、刷新、推送、转存和全球日播同步均未执行。
- 阶段 8 尚未执行单条真实订阅：当前 3 条模拟订阅只用于验证中控聚合可运行，不进入生产，也不能用于评价用户从媒体控制中心创建订阅后的真实关联质量。
