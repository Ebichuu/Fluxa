# 候选来源调度与日历详情收口实施计划

对应规格：`docs/superpowers/specs/2026-08-01-candidate-scheduler-calendar-detail-design.md`

状态：阶段 1～4 已在本地完成；660 项后端回归、TypeScript 类型检查、生产构建、安全扫描和资源中心深链验收通过；fnOS 候选调度与日历单日约 2 秒响应仍待实机复验。

实施边界：只修改 Fluxa；现有 `subscription-task` 继续关闭；不执行 Torra 搜索、追更推送、下载、qB、秒传、Symedia 或 Emby 写动作；始终排除 `services/nasemby-core/mcc_data.db`。

## 阶段 1：候选调度状态与单一执行器

目标：手动和定时刷新先共用同一个可审计、不并发的候选执行器。

1. 在 `SubscriptionRepository` 新增单例候选调度状态表和读写方法。
2. 测试状态默认值、版本增长、原子领取、完成、中断恢复和同计划键不重复。
3. 新增 `CandidateSourceScheduler`，显式接收 clock、配置加载器、刷新回调和仓储。
4. 将手动 `/api/subscriptions/run` 切换到新执行器；并发请求返回 `already_running`。
5. 保持旧响应字段，可选增加 `trigger/runId/scheduler/sourceCounts`；旧 `douban.last_*` 只由新状态投影。

验收：手动刷新仍更新候选池；两个并发刷新只执行一次；任何响应和审计都不包含 URL 查询、Cookie、Passkey 或路径。

## 阶段 2：独立调度循环与能力状态

目标：在不开启危险总调度的前提下自动更新候选池。

1. 新增独立 `candidate-source` 后台线程，每分钟只调用候选调度器 `run_due()`。
2. 使用 Asia/Shanghai 计算计划键，使用 UTC 持久化时间。
3. 实现当日计划、宽限、手动满足当日计划和重启最多补跑一次。
4. 单个来源失败不阻断其他来源，汇总成功、失败、跳过和候选写入数。
5. `subscription_workbench_runtime` 改读 `candidate-source` 权威状态，不再从 `subscription-task` 推断候选调度。
6. 补充后台线程启动契约和“未开启/等待/运行中/部分失败/逾期/正常”状态测试。

验收：候选刷新运行时 `subscription-task`、Torra 搜索、频道轮询、追更推送和下载调用数均为 0。

## 阶段 3：日历单日快速路径

目标：日历摘要已存在时，当日详情只做日期投影。

1. 将完整快照键扩展为 `year/month/mediaType/includeUnlinked`。
2. 新增从完整快照生成单日响应的纯投影函数，重算当日统计和版本。
3. `view=detail` 命中快照时禁止调用月份 loader、Torra 对账、任务 `full_snapshot()` 和历史事件索引。
4. 为相同快照键增加单飞构建，避免摘要与详情并发重复构建。
5. 前端深链详情等待对应摘要请求完成后再请求日期详情。
6. 测试快照 TTL、未关联口径隔离、当日统计、并发单飞和历史事件保留。

验收：fnOS 正常点击当日详情约 2 秒返回；日历快照过期后历史成功时间不消失。

## 阶段 4：资源中心路由、文案与发布门禁

目标：完整恢复待整理深链，明确列表与全库的数量口径。

1. 将 `cleanup` 加入 `TaskNavigationTarget.resourceView`、`readNavigation()` 和导航路径测试。
2. 验证 `/rss-library?view=cleanup` 刷新、前进和后退保持待整理视图。
3. 列表标题按当前时间窗口显示“当前范围 N 条”；五个视图标签继续显示“全库 N”。
4. 运行 API 契约复核，确认只增字段、旧状态码兼容、GET 无副作用且公开响应已脱敏。
5. 同步 `README`、`DESIGN`、专项规格和 UI 总计划的实施状态。

验收：TypeScript 类型检查和生产构建通过；普通 `/rss-library` 行为不变；精准下载和 Symedia 未接入能力仍保持阻断。

## 完整门禁

```powershell
Set-Location services/nasemby-core
python -m unittest discover -s tests -t .
Set-Location ../..
npm run build
git diff --check
```

另外执行：调度竞态与重启测试、日历快照计数测试、公开响应敏感字段扫描、`/rss-library?view=cleanup` 深链验收，以及当前范围/全库文案检查。
