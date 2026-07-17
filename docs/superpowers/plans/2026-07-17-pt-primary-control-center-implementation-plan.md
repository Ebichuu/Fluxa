# 媒体控制中心 PT 主链收口实施计划

状态：代码、自动化、本地页面和最终镜像只读验收完成

日期：2026-07-17

依据：`docs/superpowers/specs/2026-07-17-pt-primary-control-center-design.md`

## 1. 目标与边界

本轮只完成媒体控制中心对 PT 主线路的控制与观察：

```text
NasEmby 订阅 → Torra → qB → Torra 秒传 115 → Symedia → Emby
```

必须遵守：

- 订阅只推送到 Torra，不推送到 Symedia 或 MoviePilot。
- 不启动 NasEmby `legacy/ptto115.py`。
- Telegram、HDHive/pansou、影巢、115 分享转存和自动云盘兜底只保留底层源码。
- 当前代码验证不调用任何真实外部写接口。
- Mineradio 核心视觉与桥接协议不改；允许调整导航按钮与媒体抽屉。

## 2. 验收基线

### 任务 1：记录修改前基线

执行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
git status --short
```

验收：

- Python 现有 83 项通过。
- TypeScript 和 Vite 生产构建通过。
- 工作区只有本轮计划文档修改。

## 3. Torra 单一推送入口

### 任务 2：补强 Python Torra 推送动作

修改：

- `services/nasemby-core/app/subscription_compat_runtime.py`
- `services/nasemby-core/tests/test_mcc_compat_runtime.py`
- `services/nasemby-core/tests/test_source_contract.py`

实现：

1. 保留现有 `/api/subscriptions/push-preview` 和 `/api/subscriptions/push` 兼容行为。
2. 新增细分 v2 Torra 预览与动作路由，订阅 ID 放在路径中，目标服务不可由浏览器选择。
3. 服务端从唯一台账重新读取条目，并复用现有 `_push_preview()`、分类、路径、下载器 ID 和在线查重逻辑。
4. 动作要求 `confirm=true` 和 12–128 字符幂等键。
5. 增加单 worker 内存幂等回放与 60 秒订阅冷却；服务重启后的重复仍由 Torra 在线查重阻止。
6. 继续同时要求 `NASEMBY_CORE_WRITE_ENABLED=true` 与 `TORRA_PUSH_ENABLED=true`。
7. 响应只返回成功、是否创建/更新、Torra 订阅标识的安全摘要、预览和请求 ID；不透传 Token 或完整上游异常。
8. 结果写入原活动日志。

测试：

- 未认证与错误 Origin 仍由统一保护拒绝。
- 缺少确认、幂等键、订阅、分类、下载器 ID、保存路径或在线查重时不调用 Torra。
- 同一幂等键只调用一次并返回 `replayed=true`。
- 同订阅不同幂等键在冷却内返回 409。
- 模拟 Torra 成功、重复、异常和结果脱敏。

### 任务 3：在订阅详情接入 Torra 预览和确认

修改：

- `src/services/api.ts`
- `src/types/subscriptions.ts`
- `src/components/pages/DiscoverPage.tsx` 或拆出的订阅页组件
- `src/styles/global.css`

实现：

1. 新增类型化的 Torra v2 预览与动作客户端。
2. 订阅详情显示“检查并推送到 Torra”。
3. 先展示分类、保存路径、季号、查重结果、警告和阻塞原因。
4. 只有 `ready=true` 时显示确认动作。
5. 执行期间禁止重复点击，完成后刷新订阅和任务链相关状态。
6. 不增加 Symedia 或 MoviePilot 推送按钮。

验收：

- 预览不触发写请求。
- 关闭写闸门时页面明确说明原因。
- 用户能区分“已触发 Torra”与“下载完成”。

## 4. PT 页面收口

### 任务 4：隐藏延期的 Telegram 网盘 UI

修改：

- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/SettingsPage.tsx`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/ControlRoom.tsx`
- `src/services/api.ts`
- 相关前端类型与样式

实现：

1. 发现/订阅移除网盘兜底开关、候选预览和转存按钮。
2. 订阅设置不显示云盘策略，但保存其他配置时保留原 `cloud_acquisition` 数据。
3. 系统设置不显示 Telegram 登录、HDHive 授权、影巢和 115 分享转存管理。
4. 任务中心移除“网盘支线”状态，只保留“进入 115”主链步骤。
5. 控制室不把 Telegram/HDHive/影巢当作当前运行服务；MoviePilot 可标记为未来 PT 补齐但不自动调用。
6. 删除只因本轮隐藏界面而失去用途的 React 导入、状态和客户端引用，不删除 Python 底层模块、路由或测试。

测试：

- 前端契约扫描确认 React 不再引用网盘动作接口。
- 原 v2 网盘接口仍存在、默认关闭且模拟测试继续通过。

### 任务 5：校正 PT 主链文案与证据

修改：

- `src/components/pages/Overview.tsx`
- `src/components/pages/TasksCenter.tsx`
- `services/nasemby-core/app/task_chain_runtime.py`（仅在字段含义确需修正时）
- `services/nasemby-core/tests/test_task_chain_runtime.py`

实现：

1. 页面统一使用“订阅 → Torra/qB → 进入 115 → Symedia/Emby”。
2. 保留兼容步骤键 `cloud115`，但不再显示为网盘获取支线。
3. 只有直接证据显示“已验证”；qB 完成或 Symedia 相邻记录继续明确标注推断。
4. 总览删除“人工云盘/自动兜底”描述。

## 5. 导航与媒体抽屉

### 任务 6：增加订阅入口并消除重复导航

修改：

- `src/components/layout/AppTopNav.tsx`
- `src/app/App.tsx`
- `src/components/pages/DiscoverPage.tsx` 或新建的订阅页面组件
- `src/styles/global.css`

实现：

1. 主导航顺序改为：总览、影院大厅、发现、订阅、任务中心、日历。
2. 控制室从主按钮移除，右侧健康状态按钮继续进入控制室。
3. 新增独立 `subscriptions` 页面状态；复用现有 NasEmby 订阅组件和台账，不复制业务逻辑。
4. 订阅设置继续从订阅页面内部进入。
5. 小屏导航允许安全横向滚动或压缩，不出现不可达按钮。

验收：

- 发现和订阅各自保留筛选状态。
- 健康按钮、搜索和设置快捷动作仍有效。
- 键盘焦点和 `aria-current` 正确。

### 任务 7：优化影院大厅媒体抽屉

修改：

- `src/components/media-hall/MediaQueuePanel.tsx`
- `src/components/media-hall/MediaHall.tsx`
- `src/styles/global.css`

实现：

1. “当前队列”改为“本库内容”。
2. 当前项目变化时把选中行滚动到可见区域。
3. 移动端增加明确打开/关闭按钮，不依赖悬停。
4. 保留媒体库切换、项目选择、固定状态和当前焦点。
5. 不加入 PT 任务、下载任务或虚构的 Emby 播放队列。

验收：

- 桌面悬停、固定和键盘选择保持可用。
- 390px 宽度下可打开、切换、选择和关闭。
- Mineradio iframe 消息协议与冻结资源不变。

## 6. 活动日志与系统指标

### 任务 8：增加缓存的系统指标接口

修改或新增：

- `services/nasemby-core/app/system_metrics_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_system_metrics_runtime.py`

实现：

1. 调用原 `dashboard_system_metrics()`，不重写 CPU、内存、磁盘和网络采样。
2. 只返回百分比、容量和速率等白名单字段；不返回内部路径、Emby 库列表或凭据。
3. 服务端缓存 30 秒，同一缓存窗口内只采样一次。
4. 采样失败返回固定脱敏错误，不影响其他接口。

### 任务 9：在总览和任务中心显示指标与活动

修改：

- `src/services/api.ts`
- 新增前端指标/活动类型
- `src/components/pages/Overview.tsx`
- `src/components/pages/TasksCenter.tsx`
- `src/styles/global.css`

实现：

1. 总览可见时读取系统指标，刷新间隔不短于 60 秒。
2. 指标卡展示 CPU、内存、磁盘、网络，局部失败有明确空状态。
3. 任务中心读取最近 100 条活动，支持订阅、推送、qB 和系统筛选。
4. 不提供清空日志动作。

## 7. 契约与文档

### 任务 10：同步契约和当前事实

修改：

- `docs/contracts/http-api-contract-v2.json`
- `docs/API_CONTRACT.md`
- `docs/CORE_API_CAPABILITY_MATRIX.md`
- `docs/FRAMEWORK.md`
- `docs/PLAN.md`
- `docs/ROADMAP.md`
- `docs/CLOUD_ACQUISITION_PLAN.md`
- `README.md`
- `services/nasemby-core/README.md`
- `services/nasemby-core/DESIGN.md`

要求：

- 记录 Torra 是唯一当前订阅推送目标。
- 记录 Torra 独占 qB 到 115 秒传，NasEmby `ptto115.py` 不启动。
- 将 Telegram 网盘功能标记为延期，不标记为删除或完成。
- 保留 MoviePilot 未来 PT 补齐计划和官方文档链接。
- 更新 v2 路由数、客户端引用和默认写保护。
- 记录导航与媒体抽屉的新边界。

## 8. 最终验证

### 任务 11：自动化和静态校验

执行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -v
npm run typecheck
npm run build
npm audit --omit=dev
docker compose config --services
docker build -t media-control-center:v2-pt-final .
```

补充：

- 运行 API 契约审查。
- 运行变更、质量和安全校验脚本。
- 扫描真实密码、Cookie、Token 和完整 qB hash。
- 检查最终镜像只有 Python/Gunicorn 常驻，Node 仅在构建阶段存在。

### 任务 12：本地浏览器验收

验收尺寸：

- 1440×900
- 1024×768
- 390×844

检查：

- 新导航、订阅入口和健康按钮。
- Torra 推送预览在写闸门关闭时只显示阻塞原因，不执行真实推送。
- 任务中心没有网盘支线，115 仍是 PT 主链第三步。
- 总览指标与任务活动局部失败不影响整页。
- 媒体抽屉桌面和移动端交互。
- 影院大厅 Mineradio 核心视觉和桥接无回归。

## 9. 交付条件

- 全部自动测试和构建通过。
- Critical/High 安全问题为 0。
- 文档与接口契约同步。
- Git 工作区只包含本轮可解释改动。
- 没有连接真实外部服务执行写动作。
- `docs/PLAN.md` 明确下一步是 fnOS 只读部署和单条 Torra 实机验证，而不是 Telegram 网盘开发。

## 10. 2026-07-17 执行结果

已完成：

- Torra v2 预览与固定目标推送，包含确认、幂等回放、60 秒冷却、服务端复查和脱敏审计。
- 修复服务启动不足 60 秒时首次 Torra 推送被误判为冷却的问题，并用低单调时钟回归覆盖。
- 导航、订阅入口、PT 任务中心、活动日志、缓存 NAS 指标和媒体抽屉收口。
- 自动测试活动日志改用临时路径，完整回归不再污染真实活动台账。
- Python 85 项、TypeScript 类型检查、Vite 生产构建和 `npm audit --omit=dev` 全部通过。
- API 契约审查通过：v1 路径、方法和历史状态码未破坏；v2 共 16 条，Torra 目标不可由浏览器选择。
- 质量扫描 0 错误；安全扫描 0 Critical / 0 High；新增 diff 未发现硬编码凭据。
- 1440×900、1024×768、390×844 页面验收通过；移动端媒体抽屉可明确展开和关闭；Mineradio iframe 与桥接正常。
- `media-control-center:v2-pt-final` 构建通过；独立临时容器验证登录、指标 200、订阅/Torra 写闸门 403、保留接口 503 和无 Node/npm 运行时。
- 2026-07-18 基于最新提交 `bde3eba` 重建 `media-control-center:v2-pt-rc-bde3eba`；静态资源、登录、只读 API、写闸门、保留接口、Python-only 运行层和重启恢复再次通过。

尚未完成：

- fnOS 部署、真实 Torra/qB/115/Symedia/Emby 链路和订阅调度继续等待实机窗口。
- Telegram 网盘、HDHive / pansou、影巢、115 分享转存、自动兜底和 MoviePilot 补齐仍按路线图延期。
