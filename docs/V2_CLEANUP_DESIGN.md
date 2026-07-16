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

## 8. 完成后的下一步

1. 从 v2 启动本地预览，检查影院大厅和各工作页。
2. 把 v2 单容器镜像部署到 fnOS，先只读运行。
3. 在 fnOS 配置持久目录和 Emby、qB、Torra、Symedia 连接，仍保持三个写开关关闭。
4. 备份持久目录后进入受控实机窗口：先开启订阅写入，只创建一条测试订阅。
5. 核对分类、保存路径、Torra 查重与下载器 ID，再开启单条 Torra 推送。
6. 验证 PT / Torra → qB → 115 → Symedia → Emby 完整链路。
7. 主链稳定后最后开启订阅调度；自动云盘兜底继续保持关闭，除非用户以后明确启用。

## 9. 非目标

- 本轮不改变 UI 视觉设计。
- 不升级为 `/api/v2`，继续保持现有 v1 浏览器契约。
- 不迁移或合并外部 NasEmby 台账。
- 不删除原 `D:\Projects\媒体控制中心` 归档目录。
- 不执行关机。
