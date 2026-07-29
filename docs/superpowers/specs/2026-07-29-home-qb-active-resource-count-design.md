# 首页 qB 活跃任务与资源问题口径设计

## 目标

收口两个跨页面口径差异：

1. 首页“qB 活跃任务”回答下载器当前有多少活跃任务，不再受媒体链最终健康状态影响。
2. 顶部明确使用资源级异常数量，使“15 个资源需要处理”可与首页“5 个问题组 · 涉及 15 个资源”直接对账。

本波次不修改问题组算法、任务身份、资源所有权、六阶段结果优先级或外部写能力。

## 固定事实语义

### qB 活跃任务

首页 `counts.activeDownloadTasks` 固定读取当前 qB 只读摘要的 `counts.active`：

- qB 在线且明确返回 `0`：显示 `0`。
- qB 在线且返回正数：显示真实活跃任务数。
- qB 离线、未配置、读取失败或快照结构无效：返回 `null`，界面显示“未知”。

`counts.active` 沿用现有 qB 兼容口径，即规范状态为 `downloading` 或 `stalled` 的当前任务数；15 分钟评估结果继续决定是否需要处理，但不改写该下载器原始活跃计数。

该数字只描述 qB 下载器当前任务，不回答媒体身份、资源链是否需要处理、任务是否已入库或是否可播放。某条媒体链即使因 Symedia 失败或某个并发 qB 单元异常进入 `action_required`，其他仍处于 qB 活跃集合的任务也不能被过滤掉。

兼容字段 `downloading`、`concurrentDownloadGroups` 和任务链结果统计保持原语义；不得重新解释为 qB 全局任务数。

### 问题组与资源数

- 首页主指标继续使用 `actionRequiredGroups`，文案为“X 个问题组”。
- 首页解释继续使用 `actionRequiredResources`，文案为“涉及 Y 个资源”。
- 顶部角标与状态文案使用资源级 `actionRequiredResources`；旧响应缺少该字段时回退 `mediaActionRequired`。
- 顶部完整文案固定为“Y 个资源需要处理”，角标无障碍名称使用相同单位。

问题组只用于日常概览聚合，资源数用于说明实际需要处理的任务资源；两者不要求数值相等。

## 共享 qB 快照

首页、控制室和任务链必须读取应用内同一个 `QbittorrentClient` 实例生成的共享快照，不能各自直接访问 qB 上游。

`QbittorrentClient.summary()` 增加线程安全的短时单飞缓存：

- 同一刷新中的并发调用只发起一次上游读取，其余调用复用结果。
- 成功或失败快照使用同一 `lastCheckedAt`，并在 5 秒内复用，保证同一页面周期内首页、顶部、控制室和任务链看到同一份 qB 事实。
- 缓存按单调时钟判断年龄，不使用业务发生时间。
- `reconfigure()`、成功执行暂停/恢复动作后立即失效缓存。
- 缓存只减少重复只读请求，不持久化、不写入资源事件，也不改变 15 分钟 stalled 评估窗口。

首页生产路径直接调用该共享摘要。仅在测试或兼容装配中没有 qB 客户端扩展时，才读取任务链响应内已经生成的 qB 服务摘要，不额外访问上游。

## qB 当前活跃任务视图

首页“qB 活跃任务”卡片和“当前下载”焦点项统一进入：

```text
/tasks?qbActive=1
```

不得继续跳转 `/tasks?outcomeState=in_progress`，因为媒体链结果与 qB 当前活跃任务是两个独立维度。

### 后端筛选

`GET /api/v2/tasks/chains` 新增可选查询参数 `qbActive=1`：

- 只接受缺省或 `1`，其他值返回 `400 TASK_QB_ACTIVE_FILTER_INVALID`。
- 在分页前筛选具有当前 qB 活跃任务的媒体链或 qB orphan 链。
- 每条链使用与全局 `counts.active` 相同的 `downloading/stalled` 分类汇总 `qbControl.active`，不得改用媒体结果或仅统计正常下载。
- 筛选不附加 `outcomeState` 条件，因此可以同时返回 `in_progress`、`action_required`、`waiting` 等不同媒体结果。
- 任务链合并时保留 `qbControl.active`，并继续返回 `activeDownloadTasks/concurrentDownloadCount`；旧字段类型不改变。

qB 全局活跃任务数与列表卡片数可能不同：同一媒体链可以拥有多个并发 qB 任务。页面必须显示“qB 活跃任务 N 个”，并在对应卡片继续显示“同一目标有 M 个 qB 任务”，不能把卡片数冒充任务数。

未关联媒体身份的 qB 活跃任务继续以 orphan 任务卡展示，不能因为没有 TMDB 身份从该视图消失。

### 前端状态

`TaskNavigationTarget`、URL 解析/写入、`TaskChainQuery` 和任务中心增加可选 `qbActive`。

进入该视图时：

- 不自动选择或提交普通 `outcomeState` 筛选。
- 显示“qB 当前活跃任务”上下文和 qB 服务摘要中的任务数。
- qB 不可读时显示“当前活跃任务未知”，不能显示为 0。
- 用户主动点击“需要处理 / 处理中 / 已可播放 / 无需处理”任一普通筛选时，清除 `qbActive` 并恢复原有结果维度。

## 数据流

```text
QbittorrentClient 共享快照
  ├─ /api/qbittorrent/summary → 控制室
  ├─ TaskChainService → 任务链 services.qb 与 qB 任务事实
  └─ HomeSummaryService → 首页 activeDownloadTasks

任务链问题统计
  ├─ actionRequiredGroups → 首页问题组
  └─ actionRequiredResources → 首页解释 + 顶部资源角标
```

## API 兼容性

本波次只增加可选查询参数和现有响应对象中的兼容字段读取，不修改 URL、HTTP 方法、认证、成功状态码或旧字段类型。

- `/api/qbittorrent/summary` 响应结构不新增必填字段。
- `/api/v2/home/summary.counts.activeDownloadTasks` 类型继续为 `number | null`，只修正事实来源。
- `/api/v2/tasks/chains?qbActive=1` 是可选增量筛选；旧消费者不传参数时行为不变。
- `actionRequiredWorks/actionRequiredResources/actionRequiredGroups/mediaActionRequired` 的原数值和语义保持不变。

## 验收

自动化必须覆盖：

1. 同一媒体链同时有 qB 活跃任务和真实失败时，首页仍返回 qB `counts.active`，不会归零。
2. 首页与 `/api/qbittorrent/summary` 在同一 5 秒窗口内只触发一次 qB 上游读取，并返回相同 `lastCheckedAt/counts.active`。
3. qB 在线明确为 0 时显示 0；离线、未配置、失败或无有效计数时显示未知。
4. `qbActive=1` 同时返回有活跃 qB 任务的 `action_required` 与 `in_progress` 链，不受结果筛选影响。
5. qB orphan 活跃任务进入视图；分页在 qB 筛选之后执行。
6. 同一链 2 个 qB 活跃任务时，视图标题显示 2 个任务，列表可以只有 1 张媒体链卡，并解释并发数量。
7. 首页 qB 卡片和焦点项均进入 `/tasks?qbActive=1`；点击普通任务结果筛选后清除该参数。
8. 顶部文字和角标无障碍名称均为“15 个资源需要处理”，首页继续显示“5 个问题组 · 涉及 15 个资源”。
9. 原有 15 分钟 qB 观察窗、永久事件白名单、任务身份和外部写保护测试继续通过。

实机验收以实时数据为准，目标关系为：

```text
控制室 qB 活跃任务 = 首页 qB 活跃任务 = qB 活跃视图标题任务数
顶部资源数 = 首页“涉及资源”数
```

其中 qB 任务数不要求等于媒体链卡片数，资源数也不要求等于问题组数。
