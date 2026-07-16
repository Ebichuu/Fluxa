# 媒体控制中心 v2 目录收口设计

状态：待用户确认后实施  
日期：2026-07-17

## 1. 目标

`D:\Projects\媒体控制中心v2` 成为后续唯一开发主目录，包含当前最新代码、运行所需资源、测试、部署文件和有效资料。v2 使用全新 Git 历史，不复制旧仓库的 `.git`。

原目录 `D:\Projects\媒体控制中心` 只作为迁移前归档保留，本轮不删除、不覆盖，也不继续作为开发目录。

## 2. 迁移基线

- 代码基线：原仓库 `codex/python-backend-unification` 分支最新提交 `7368790`。
- 后端：Python 3.13、Flask、Gunicorn。
- 前端：React、TypeScript、Vite；Node.js 只负责构建。
- 订阅业务：直接使用已经可运行的 NasEmby Python 源码和唯一台账。
- 获取策略：PT / Torra 优先，自动云盘兜底关闭。
- UI 边界：不修改影院大厅、顶部导航、媒体队列和 Mineradio 原视觉。

## 3. v2 保留内容

### 3.1 生产代码与资源

- `src/`：React 页面、导航、影院大厅外壳和工作页。
- `services/nasemby-core/app/` 中当前统一后端与 NasEmby 业务源码。
- `vendor/mineradio-public/` 与 `public/`：Mineradio 原始视觉资源。
- 根 `Dockerfile`、`docker-compose.yml`、`.env.example`。
- `package.json`、`package-lock.json`、TypeScript 与 Vite 配置。
- Python 与前端自动测试。
- `docs/contracts/http-api-contract-v1.json`：47 条冻结接口契约。

### 3.2 仍属于业务依赖的 NasEmby 模块

以下代码虽然来自 NasEmby 旧工程或名称带有 legacy，但仍会被发现、资源搜索、provider、通知或可选获取逻辑动态调用，因此不能仅凭目录名删除：

- `discover_runtime.py`、`services.py`。
- `telegram_runtime.py`。
- `hdhive_auth.py` 与 `hdhive/` 受保护运行资产。
- `legacy/` 中仍被可选 115 / 123 获取逻辑引用的模块。

这些模块继续受统一入口、写入闸门和默认关闭开关保护，不恢复旧管理页面或独立服务。

### 3.3 参考资料

- 保留解压后的 `media-automation-maintenance` 资料目录。
- 在 v2 中统一放入 `docs/references/media-automation-maintenance/`。
- 不保留内容相同的 ZIP 副本。

## 4. v2 删除与重构内容

### 4.1 不复制的生成物和本机状态

- 原 `.git/` 历史。
- `node_modules/`、`dist/`、`__pycache__/`、`.pyc`。
- `.env`、真实凭据、Cookie、Token、会话和用户配置。
- `runtime/`、`data/`、`db/`、`upload/` 中的本机运行数据。
- 临时预览、测试容器数据和日志。

### 4.2 确认无生产入口的旧代码

- 删除 NasEmby 原静态管理页面 `app/static/`。
- 删除 NasEmby 原页面模板 `app/templates/index.html`，保留当前登录模板。
- 把 `app/main.py` 收口为统一应用装配、健康/状态接口和后台调度入口；移除已经被 404 守卫遮蔽的旧 115、Telegram、HDHive、provider 和原页面 Flask 路由外壳。
- 删除 `services/nasemby-core/Dockerfile` 与该目录重复的环境示例；v2 只允许根 Dockerfile 和根 Compose 作为正式部署入口。

删除路由外壳不等于删除 NasEmby 业务函数。当前 React、47 条契约、订阅台账、发现、资源搜索、Torra、qB、Symedia、Emby 和任务链必须继续工作。

### 4.3 旧文档和重复说明

- 删除迁移期 `docs/superpowers/plans/`、`docs/superpowers/specs/`。
- 删除根目录与 `docs/` 中重复的 Mineradio 迁移计划、旧 UI 对比、临时 HTML 和已经完成的阶段记录。
- 不在 v2 主文档继续叙述 Express → Python 的逐阶段迁移过程。
- 不把已删除的 `server/*.ts`、双服务 Compose、内部 Core 端口或旧 Node 台账写成当前结构。

## 5. v2 正式文档结构

v2 最终只保留面向当前项目的文档：

- `README.md`：项目定位、开发、部署和安全开关。
- `docs/PLAN.md`：当前完成状态、剩余实机阶段和下一步。
- `docs/FRAMEWORK.md`：一套 Python 后端的总体框架。
- `docs/API_CONTRACT.md`：当前 v1 接口和兼容边界。
- `docs/DEPLOYMENT.md`：fnOS 单容器部署、备份与回滚。
- `docs/UI_STANDARD.md`：页面与固定 UI 边界。
- `docs/IMPLEMENTATION_SOURCES.md`：当前文件到能力的映射，不写迁移流水账。
- `services/nasemby-core/README.md`、`DESIGN.md`、`SECURITY_REVIEW.md`：Python 模块说明、安全边界和已知限制。
- `docs/references/`：原始参考资料，不参与运行。

## 6. 安全边界

- v2 不复制任何真实账号、密码、API Key、Token 或 `.env`。
- 生产默认继续固定关闭：
  - `MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false`
  - `NASEMBY_CORE_WRITE_ENABLED=false`
  - `TORRA_PUSH_ENABLED=false`
- 本次迁移不创建真实订阅，不调用 Torra 搜索、qB 动作、115 / Symedia 转存或 Emby 刷新。
- 旧管理路由删除后必须继续返回 404；不能因删除守卫而重新暴露原页面。

## 7. 验收标准

### 7.1 结构

- v2 是独立新 Git 仓库，旧提交历史不存在。
- v2 不包含旧 `server/`、重复 Docker 入口、旧管理页面、历史迁移计划、缓存和真实运行数据。
- 所有主文档只引用实际存在的当前文件。

### 7.2 代码与接口

- Python 测试全部通过。
- 前端类型检查与 Vite 生产构建通过。
- 47 条冻结接口仍全部存在，无重复方法/路径。
- 42 条受保护路由和全部受保护写路由继续通过认证与 Origin 测试。
- legacy 管理接口与 `/static/app.js` 继续返回 404。

### 7.3 Docker

- 根镜像重新构建成功。
- Compose 只有 `media-control-center` 一个服务和 8787 一个端口。
- 最终容器只有 Gunicorn/Python，无 Node 可执行文件。
- 登录、只读订阅、写闸门、重启与持久化复验通过。

### 7.4 质量与安全

- 变更检查和 `git diff --check` 通过。
- 质量扫描没有新增高风险问题。
- 安全扫描没有真实凭据、Critical 或未处理的 High 问题。
- 参考资料与生产代码明确隔离。

## 8. v2 代码实施顺序

实机测试不属于当前代码收口阶段。v2 先按以下顺序完成本地代码工作：

### 阶段 0：冻结迁移输入

- 记录原仓库提交、受控文件清单和参考资料清单。
- 扫描 `.env`、运行目录、缓存和凭据，确保它们不进入 v2。
- 记录迁移前 Python、TypeScript、Vite 和 Docker 验收结果，作为清理后的对照基线。

### 阶段 1：建立干净代码快照

- 复制当前受控生产代码、测试、资源和根部署文件，不复制旧 `.git` 与生成物。
- 把维护资料的解压目录整理到 `docs/references/`，不复制重复 ZIP。
- 建立 v2 全新 Git 基线，确认没有旧 `server/`、`dist-server`、Node 后端依赖或双服务 Compose。

### 阶段 2：收口 Python 应用入口

- 缩减 `services/nasemby-core/app/main.py`，只保留统一应用装配、`/api/status`、`/api/health` 和三类后台调度入口。
- 移除已经被统一入口隐藏的旧管理路由外壳，但保留当前订阅、发现和 provider 仍会动态调用的 Python 业务函数。
- 删除原 NasEmby 静态管理页和 `templates/index.html`，保留登录模板、React 静态托管和 Mineradio 桥接。
- 删除模块内重复 Dockerfile 和环境示例，根 Dockerfile / Compose 成为唯一部署入口。

### 阶段 3：补强结构回归测试

- 更新源码结构测试，明确 `main.py` 不再包含旧 115、Telegram、HDHive、provider 管理路由。
- 保留 47 条冻结接口、42 条认证路由、全部受保护写接口 Origin、legacy 404 和 `/static/app.js` 404 回归。
- 增加当前 Python 入口不得注册第二套页面、第二套台账或第二套调度器的检查。
- 继续使用临时台账和模拟外部客户端，不连接真实服务执行写操作。

### 阶段 4：重写当前文档

- 从当前运行架构重新编写 README、计划、框架、接口、部署、实现来源和安全说明。
- 删除迁移流水账、旧技术选型讨论、重复 UI 计划和已经删除的文件引用。
- `docs/PLAN.md` 分成“已完成代码状态”“当前剩余代码工作”“以后实机阶段”，避免再次把两者混在一起。

### 阶段 5：全量本地验收

- 运行 Python 全部测试、前端类型检查和 Vite 构建。
- 重新构建单容器镜像，验证登录、只读 API、403 写闸门、404 旧入口、无 Node 运行时和重启持久化。
- 执行变更、质量、安全和凭据扫描；修复所有新增 Critical / High 问题。
- 从 v2 启动本地预览，检查影院大厅、顶部导航、媒体队列和全部工作页。

### 阶段 6：v2 代码交付

- 更新最终计划，记录删除清单、保留的动态依赖、测试结果和已知限制。
- 提交 v2 全新代码基线，使 v2 成为后续唯一开发目录。
- 原目录只保留为只读归档，不再继续修改。

## 9. 以后再进行的实机阶段

以下工作等待用户明确进入实机窗口后再做，不计入当前 v2 代码收口：

1. 把 v2 单容器镜像部署到 fnOS，先只读运行。
2. 配置持久目录和 Emby、qB、Torra、Symedia 连接，保持三个写开关关闭。
3. 备份持久目录后只开启订阅写入，创建一条测试订阅。
4. 核对分类、保存路径、Torra 查重与下载器 ID，再开启单条 Torra 推送。
5. 验证 PT / Torra → qB → 115 → Symedia → Emby 完整链路。
6. 主链稳定后最后开启订阅调度；自动云盘兜底继续关闭。

## 10. 非目标

- 本轮不改变 UI 视觉设计。
- 不升级为 `/api/v2`，继续保持现有 v1 浏览器契约。
- 不迁移或合并外部 NasEmby 台账。
- 不删除原 `D:\Projects\媒体控制中心` 归档目录。
- 不执行关机。
