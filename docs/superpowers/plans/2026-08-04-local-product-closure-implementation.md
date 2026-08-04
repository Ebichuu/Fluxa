# Fluxa 本地产品闭环实施计划

依据：`docs/superpowers/specs/2026-08-04-local-product-closure-design.md`

## 当前进度

截至 2026-08-04：

- 方案 A 与书面规格已确认。
- 本波次只实施日历持久缓存、独立对账待处理、RSS 失效匹配归档、海报兜底。
- Symedia 剩余能力和 Torra 订阅搜索模式留到独立只读证据波次。
- 不读取、修改或提交 `services/nasemby-core/mcc_data.db`。
- 批次 1–6 已完成。
- 日历、追更、首页与 RSS 定向回归 147 项通过；Python 全量回归 694 项、TypeScript 检查和生产构建通过。
- API 契约声明与实际路由均为 80 条；变更完整性、质量和安全检查通过。
- 隔离浏览器实机验收通过；全过程使用临时 SQLite 和虚构数据，未启动后台生产运行时，也未连接或触发 Torra、qB、115、Symedia、Emby 与真实 RSS 动作。

## 批次 1：日历缓存仓储与队列

涉及文件：

- `services/nasemby-core/app/calendar_snapshot_repository.py`
- `services/nasemby-core/tests/test_calendar_snapshot_repository.py`

实施：

1. 新增 `calendar_snapshot_cache` 与 `calendar_snapshot_refresh_queue`。
2. 规范化 `year/month/mediaType/includeUnlinked` scope。
3. 成功写入原子替换 payload；失败保留最后可靠 payload。
4. 实现幂等排队、SQLite 租约领取、完成和失败状态。
5. 只续刷当前月默认 scope 和最近 30 天使用过的过期 scope。

验证：

- scope 无串扰；失败不删除旧快照；并发只能领取一次。
- 冷缓存返回 `unknown`，不返回虚假零值。

## 批次 2：日历后台刷新与纯读 API

涉及文件：

- `services/nasemby-core/app/calendar_timeline_runtime.py`
- `services/nasemby-core/app/calendar_snapshot_refresh_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_calendar_timeline_runtime.py`
- `services/nasemby-core/tests/test_calendar_snapshot_refresh_runtime.py`
- `src/services/api.ts`
- `src/types/subscriptions.ts`
- `src/components/pages/CalendarPage.tsx`

实施：

1. 保留现有月快照构建器，拆出后台可调用的构建入口。
2. `GET /api/v2/calendar` 只读取持久缓存并做 summary/detail/range 投影。
3. 新增 `POST /api/v2/calendar/refresh-requests`，只排队并返回 `202`。
4. worker 每 30 秒运行，进程锁加 SQLite 租约保证单飞。
5. 启动只排队当前上海月份的 `all/false` 默认 scope。
6. 前端在冷缓存或过期缓存时幂等请求刷新并轮询 GET。

验证：

- 首个 GET 不调用 calendar loader、任务链、Torra 或历史索引。
- 日详情从月缓存投影；重启后仍能读取最后可靠快照。
- 上游失败显示旧值和 `partial`，首次失败显示 `unknown`。

## 批次 3：独立对账待处理

涉及文件：

- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `services/nasemby-core/app/home_summary_runtime.py`
- `services/nasemby-core/tests/test_subscription_workbench_runtime.py`
- `services/nasemby-core/tests/test_home_summary_runtime.py`
- `src/types/subscriptions.ts`
- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/Overview.tsx`

实施：

1. `reconciliationActionRequired` 只统计 `conflict/remote_missing`，按规范订阅键去重。
2. 新增 `status=reconciliation_action_required` 深链和独立筛选入口。
3. 保留媒体 `action_required` 的原统计与筛选。
4. 首页只增加中性辅助提醒，不并入媒体异常总数。

验证：

- 下载/入库异常为 0 时，对账待处理可独立为非零。
- 刷新和前进/后退保持筛选；旧筛选完全不变。

## 批次 4：RSS 失效归属与归档闭环

涉及文件：

- `services/nasemby-core/app/private_rss_repository.py`
- `services/nasemby-core/app/private_rss_api_runtime.py`
- `services/nasemby-core/tests/test_private_rss_repository.py`
- `services/nasemby-core/tests/test_private_rss_api_runtime.py`
- `src/types/rssSeedLibrary.ts`
- `src/services/api.ts`
- `src/components/pages/RssSeedLibraryPage.tsx`
- `src/styles/rss-seed-library.css`

实施：

1. 匹配记录增加归档状态、原因、运行 ID 和版本。
2. 新增清理运行与项目审计表。
3. 候选组同时投影有效归属和失效归属，归档项退出评分主列表。
4. 新增预览和确认 POST；确认前重新核验全部目标。
5. 整批事务更新，漂移时全部回滚；幂等键复用首次结果。
6. 普通标签改为“追更待识别”，全库未关联保持中性。

验证：

- 只归档 `subscription_missing` 等明确失效匹配。
- 冲突候选、有效候选、`rss_items` 和下载链均不被修改。
- 预览漂移无部分结果，公开响应不含 URL、路径、Cookie 或 Passkey。

## 批次 5：统一海报兜底

涉及文件：

- `src/components/layout/PosterImage.tsx`
- 使用 `PosterImage` 的发现页和列表页

实施：

1. 缺 URL、加载失败、零尺寸、透明空图和保守判定的纯白图统一走标题占位。
2. 同源代理图使用 16×24 采样，不读取跨域像素。
3. 页面生命周期内记忆失效解析地址，地址变化后才重试。
4. 保持现有尺寸、主题、焦点和响应式布局。

验证：

- 失败占位始终包含标题信息。
- 正常浅色海报不误判，同一失效 URL 不循环请求。

## 批次 6：契约、质量、安全与实机验收

1. [x] 运行日历、追更、RSS 专项 Python 测试。
2. [x] 运行 Python 全量回归、TypeScript 和生产构建。
3. [x] 更新 API 契约与本地计划状态。
4. [x] 运行 `git diff --check`、变更完整性、质量和安全检查。
5. [x] 浏览器验证日历冷缓存/刷新、对账深链、RSS 预览确认和海报兜底。
6. [x] 确认没有 Torra、qB、115、Symedia、Emby 或 RSS 原始条目写动作。

实机结果：

- 日历冷缓存首次 GET 约 69 ms 返回完整 `unknown` 结构；刷新完成后缓存 GET 约 8 ms。
- 进程重启且不运行刷新 worker 时，仍能直接读取 SQLite 中最后可靠日历结果。
- 当日详情从持久月份缓存投影，白色无效海报显示标题首字占位。
- `/following?status=reconciliation_action_required` 正确显示 1 条对账待处理，媒体“需要处理”仍为 0。
- RSS 待整理组同时显示有效归属和失效归属；预览与确认仅归档 1 条失效匹配。
- 归档后 `rss_items` 仍为 1 条、匹配审计仍为 2 条、有效候选组仍保留 1 条候选。
