# 媒体控制中心 v2 目录收口与源码保留设计

状态：用户已确认“不要继续删除核心源码”
日期：2026-07-17

## 1. 目标

`D:\Projects\媒体控制中心v2` 成为后续唯一开发主目录，包含最新代码、运行资源、测试、部署文件、接口调用依据和有效资料。v2 使用全新 Git 历史，不复制旧仓库的 `.git`。

原目录 `D:\Projects\媒体控制中心` 继续作为迁移前只读归档保留，不删除、不覆盖，也不再作为开发目录。

## 2. 已确认架构

- 后端：Python 3.13、Flask、Gunicorn 单服务。
- 前端：React、TypeScript、Vite；Node.js 只负责构建。
- 订阅业务：使用 NasEmby Python 源码和唯一台账。
- 获取策略：PT / Torra 优先，网盘为受开关控制的第二通道。
- 自动云盘兜底：默认关闭。
- UI 边界：不修改影院大厅、顶部导航、媒体队列和 Mineradio 原视觉。
- 部署：fnOS 上使用一个 Docker 容器和 8787 端口。

## 3. 源码保留原则

v2 不再根据“旧页面”“legacy”“当前 React 没调用”或“路由暂时返回 404”判断代码无用。

必须保留：

- 订阅、发现、日历、资源规则和调度源码。
- 115、Telegram、HDHive / pansou、Symedia 和 provider 能力。
- Torra、qBittorrent、Emby 和四步任务链适配器。
- 原 NasEmby 接口实现、参数处理、错误语义和调用关系。
- 原静态管理页面源码，作为页面合并和接口行为参考；生产不启用该页面。
- Python 与前端测试、Docker 文件和 Mineradio 运行资源。
- 未完成的代码计划和实机验证计划。

可以排除的内容仅限：

- `.git/`、`node_modules/`、`dist/`、`__pycache__/` 等可重新生成内容。
- `.env`、Cookie、Token、会话、真实账号和其他敏感状态。
- `runtime/`、`data/`、`db/`、`upload/` 中的本机运行数据。
- 已确认内容完全相同的重复压缩包。

业务源码没有等价替代证明前，只允许隔离、停止注册或标记待迁移，不允许删除。

## 4. 核心接口恢复策略

原 `services/nasemby-core/app/main.py` 同时保存页面路由和核心业务入口，不能压缩成只剩应用装配。v2 已恢复其中的 115、Telegram、HDHive、provider、配置和活动日志接口。

安全策略：

- 当前 React 接口保持原样。
- 原核心接口保留在 Flask URL map 中。
- `MCC_PRESERVED_CORE_API_ENABLED=false` 时，尚未统一接入的接口返回 `503 PRESERVED_CORE_API_DISABLED`。
- 模拟测试可以显式开启该开关并替换外部函数，不连接真实服务。
- 生产不得整体开启该兼容开关；后续为每组操作建立独立安全接口和细分写入开关。
- 原静态管理页面不注册为第二套生产 UI，但源码必须保留。

逐接口用途、调用函数、副作用和替代状态见 `docs/CORE_API_CAPABILITY_MATRIX.md`。

## 5. v2 正式内容

### 5.1 生产代码与资源

- `src/`：React 页面、顶部导航、影院大厅外壳和工作页。
- `services/nasemby-core/app/`：统一 Python 后端与 NasEmby 业务源码。
- `vendor/mineradio-public/`、`public/`：Mineradio 视觉资源。
- 根 `Dockerfile`、`docker-compose.yml` 和 `.env.example`。
- Python、TypeScript、Vite 配置和全部自动测试。

### 5.2 参考源码与资料

- NasEmby 原静态页面源码：只作为调用关系和迁移参考，不进入生产页面入口。
- `docs/references/media-automation-maintenance/`：fnOS、Torra、qB、115、Symedia 和 Emby 维护资料。
- 原目录只读归档：用于核对尚未迁入 v2 的文件和二进制资源。

### 5.3 活动文档

- `README.md`：项目定位、开发和部署入口。
- `docs/PLAN.md`：当前完成状态和下一步。
- `docs/FRAMEWORK.md`：产品与技术总体框架。
- `docs/API_CONTRACT.md`：当前 React v1 接口。
- `docs/CORE_API_CAPABILITY_MATRIX.md`：原核心接口逐条用途与迁移状态。
- `docs/ROADMAP.md`：所有未完成代码和实机工作。
- `docs/CLOUD_ACQUISITION_PLAN.md`：PT 优先、网盘可开关的详细路线。
- `docs/DEPLOYMENT.md`：fnOS 部署、备份、回滚和写入闸门。
- `docs/IMPLEMENTATION_SOURCES.md`：文件到能力的映射。

## 6. 完整性验收

### 源码

- 原提交中的业务 Python 模块在 v2 均有对应文件。
- 原 `main.py` 的核心接口和调用函数保留。
- 原页面源码作为参考迁入，不要求生产可访问。
- 不存在第二套订阅台账、第二套调度器或 Node 运行后端。

### 接口

- 当前 47 条 React v1 契约继续通过。
- 原核心接口默认返回明确的 503 保留状态，而不是伪装成不存在。
- 测试开启保留接口时全部使用 mock，不发出真实外部写入。
- 每条外部动作都能在能力矩阵中查到用途、副作用和替代计划。

### 安全

- 生产默认关闭：
  - `MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false`
  - `NASEMBY_CORE_WRITE_ENABLED=false`
  - `MCC_PRESERVED_CORE_API_ENABLED=false`
  - `TORRA_PUSH_ENABLED=false`
- 本阶段不创建真实订阅，不调用 Torra 搜索、qB 动作、115 / Symedia 转存或 Emby 刷新。
- v2 不包含真实凭据和运行数据。

### 构建与部署

- Python 测试、前端测试、类型检查和 Vite 构建通过。
- Docker 只有 `media-control-center` 一个服务和 8787 一个端口。
- 最终容器只有 Gunicorn / Python 常驻，无 Node 运行时。
- 登录、只读 API、写闸门、持久化和重启验证通过。

## 7. 实施顺序

### 阶段 0：迁移基线

- [x] 记录原提交 `7368790`。
- [x] 建立 v2 全新 Git。
- [x] 保留原目录只读归档。
- [x] 暂缓实机写入测试。

### 阶段 1：源码完整性恢复

- [x] 迁入当前 React、Python、测试、Docker 和 Mineradio 文件。
- [x] 恢复原 `main.py` 的核心路由和调用关系。
- [x] 恢复“接口存在但默认关闭”的测试语义。
- [x] 迁入原静态页面参考源码和二进制资源，不注册生产页面。
- [x] 对照原提交生成最终文件完整性报告，152 个文件均已在当前路径或参考快照中保留。

### 阶段 2：接口梳理

- [x] 建立核心接口能力矩阵。
- [ ] 为 115、Telegram、HDHive / pansou、MoviePilot、Torra 和 Symedia 补齐逐接口模拟契约。
- [ ] 为原配置接口建立字段白名单与脱敏说明。
- [ ] 建立旧接口到统一 React 接口的逐项映射。

### 阶段 3：统一页面接入

- [ ] 实现 115、Telegram 和 HDHive / pansou 只读连接状态。
- [ ] 实现网盘候选预览和全局、订阅级开关。
- [ ] 实现单条受控转存、查重、幂等、冷却和审计。
- [ ] 在任务中心补充 PT 主链和网盘支线证据。
- [ ] 自动云盘兜底代码完成后仍保持默认关闭。

### 阶段 4：文档与测试收口

- [x] 同步 README、FRAMEWORK、PLAN、ROADMAP、API、DEPLOYMENT 和模块文档。
- [x] 运行全部 Python 与前端测试。
- [x] 运行 Docker 隔离冒烟、质量、安全、凭据和引用检查。
- [x] 从 v2 最终镜像启动独立本地预览 `http://127.0.0.1:18789/`。

### 阶段 5：v2 基线交付

- [x] 提交完整源码基线 `4112c22`。
- [x] 确认后续开发只使用 v2。
- [x] 保持原目录为只读回退资料。

## 8. 以后进行的实机阶段

以下工作等待用户明确进入实机窗口：

1. 在 fnOS 只读部署 v2 单容器。
2. 配置持久目录和 Emby、qB、Torra、Symedia 连接，保持写开关关闭。
3. 备份持久目录后，只创建一条测试订阅。
4. 核对分类、保存路径、Torra 查重和下载器 ID。
5. 验证 PT / Torra → qB → 115 → Symedia → Emby 完整链路。
6. 主链稳定后最后开启订阅调度；自动云盘兜底继续关闭。

## 9. 非目标

- 本轮不改变 UI 视觉设计。
- 不升级为 `/api/v2`。
- 不导入或合并外部 NasEmby 台账。
- 不执行真实外部写入。
- 不删除原归档目录。
- 关机不是项目功能或长期部署步骤，只在用户当次明确要求时作为收尾运维动作执行。
