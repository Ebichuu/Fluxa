# 首页 qB 活跃任务与资源问题口径实施计划

对应设计：`docs/superpowers/specs/2026-07-29-home-qb-active-resource-count-design.md`

## 1. 共享 qB 快照

### 1.1 失败测试

- 在 `test_qbittorrent_runtime.py` 覆盖 5 秒内顺序调用只读取一次上游，并返回相同 `lastCheckedAt/counts.active`。
- 覆盖并发调用单飞、5 秒后刷新、失败快照复用、`reconfigure()` 与成功暂停/恢复后失效。
- 验证缓存不改变可选 `assessment`、原始 `counts.active/stalled` 或公开错误脱敏。

### 1.2 实现

- 为 `QbittorrentClient` 增加线程安全的短时缓存、单调时钟和统一失效方法。
- `summary()` 的一次真实刷新只捕获一个业务检查时间；所有复用消费者拿到同一快照。
- 配置变化和成功写动作后清空缓存，不新增持久化状态。

## 2. 首页 qB 事实源

### 2.1 失败测试

- 构造媒体链为 `action_required`、qB 共享摘要 `counts.active=2` 的样本，验证首页仍显示 2。
- 覆盖 qB 在线 0、离线、未配置、异常和缺少有效 active 字段。
- 验证首页与 qB 摘要共用同一客户端快照，不因首页再次读取产生第二次上游调用。

### 2.2 实现

- `HomeSummaryService` 优先读取应用扩展中的共享 qB 客户端摘要；测试/兼容装配缺少客户端时读取任务链 `services.qb`。
- `activeDownloadTasks`、首页详情和“当前下载”焦点项改为 qB 全局 `counts.active`。
- 首页标签统一为“qB 活跃任务”，真实零值显示 0，证据不可读返回 `null`。

## 3. qB 当前活跃任务视图

### 3.1 后端失败测试

- 为 `TaskChainV2Service.list_items()` 和 HTTP 路由补充 `qbActive=1` 测试。
- 覆盖 `action_required` 与 `in_progress` 混合结果、orphan qB、分页顺序和无效参数 400。
- 验证合并任务保留 `qbControl.active`，同一媒体链多个 qB 活跃任务只形成一张卡但保留任务数。

### 3.2 后端实现

- `_qb_control()` 按现有 `downloading/stalled` 口径增加 `active`。
- v2 合并投影保留 `qbControl.active`。
- `/api/v2/tasks/chains` 增加可选 `qbActive=1`，在分页前独立过滤，不附加 outcome 条件。

### 3.3 前端实现

- `TaskNavigationTarget`、URL 解析/写入、`TaskChainQuery` 和 API 请求增加 `qbActive`。
- 首页指标与焦点项跳转 `/tasks?qbActive=1`。
- 任务中心增加“qB 当前活跃任务”上下文，标题读取 `services.qb.active`；qB 不可读显示未知。
- 进入 qB 视图时不提交普通 outcome 筛选；用户点击普通结果筛选后清除 `qbActive`。

## 4. 顶部资源单位

- 顶部数量优先读取 `actionRequiredResources`，兼容回退 `mediaActionRequired`。
- 状态文案和角标 `aria-label` 统一为“Y 个资源需要处理”。
- 首页“问题组”指标及问题组/资源/身份未确认说明保持不变。

## 5. 契约与文档

- 更新 v2 机器契约的 `/api/v2/tasks/chains` 可选查询参数 `qbActive`。
- 更新 `docs/API_CONTRACT.md`、前后端 `DESIGN.md`、`ROADMAP.md`、README 测试基线与 URL 状态文档。
- 所有新字段/参数为可选增量，不改变旧 URL、方法、状态码和字段类型。

## 6. 验证与发布

- 运行 qB、首页、任务链 v2 和导航定向测试。
- 运行全部 Python 回归、TypeScript、生产构建、Compose 与 JSON 契约解析。
- 使用本地隔离快照验收首页 qB=2、顶部资源=15、首页问题组=5，以及 `/tasks?qbActive=1` 混合结果列表。
- 运行 API 兼容性检查和变更关卡；始终排除 `services/nasemby-core/mcc_data.db`。
- 提交并推送 `main`，等待源码校验、双架构构建、amd64/arm64 冒烟和 `latest` 提升全部成功。
- 关机任务保持取消，不执行关机。
