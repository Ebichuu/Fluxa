# 首页后台摘要缓存与部分确认统计实施计划

对应规格：`docs/superpowers/specs/2026-08-01-home-summary-cache-design.md`

状态：待实施。

实施边界：本波次只处理首页 P0 与三处相关文案；只修改 Fluxa；不接入 Symedia 后六项能力，不清理身份台账或电影追更，不推断 Torra 搜索策略，不执行 Torra、qB、秒传、Symedia、Emby 或追更写动作；始终排除 `services/nasemby-core/mcc_data.db`。

## 阶段 1：SQLite 模块缓存与刷新租约

目标：先建立可独立测试的持久边界，不改变现有首页接口行为。

1. 新增 `services/nasemby-core/app/home_summary_repository.py`，集中管理 `home_summary_module_cache` 与 `home_summary_refresh_state`。
2. 缓存主键固定为 `module_key + scope_key`；全局模块使用 `global`，日期模块使用 `date:YYYY-MM-DD`。
3. 成功写入以单模块行为原子边界，完整替换 payload 与成功元数据；写入异常必须回滚并保留上一份完整 JSON。
4. 失败写入只更新尝试时间、稳定错误码和公开脱敏原因；已有成功 payload 不删除，并将公开确认状态投影为 `partial`。
5. 实现 SQLite `BEGIN IMMEDIATE` 租约领取、续期/完成和崩溃过期恢复；租约 token 不进入公开响应。
6. 更新 `sqlite_runtime.py` 的 schema version，并新增 `test_home_summary_repository.py` 覆盖建表、幂等初始化、成功/失败写入、半写回滚、租约竞争和过期恢复。

验收：第二个并发领取者不能获得有效租约；失败写入后仍能逐字读取上一份 payload；日期范围键不会回退到前一天。

独立提交：`feat(home): add module summary cache repository`

## 阶段 2：实时聚合拆成模块采集器

目标：把现有 `HomeSummaryService.snapshot()` 的外部读取与公开响应组装分开，但暂不切换 HTTP GET。

1. 在 `home_summary_runtime.py` 保留现有事实判断辅助函数，将实时工作拆为显式采集输入和纯模块投影。
2. 模块键固定为 `task_pipeline`、`qb_activity`、`archive_today`、`secupload`、`subscription_progress`、`rss_resource_center`、`service_health`。
3. 同一轮只调用一次任务链完整快照；qB、归档、秒传、订阅进度和 RSS 读取结果先在事务外形成版本化本地输入，再供多个投影复用。
4. 单个外部读取或模块投影失败只产生该模块失败结果，不抹除同轮已成功模块。
5. 模块 payload 只保存首页需要的公开摘要，禁止保存文件名、路径、hash、URL、Cookie、Passkey、Authorization、远端原始 ID 或上游错误原文。
6. 将现有 `test_home_summary_runtime.py` 的事实语义测试迁到模块投影层，确认问题组、qB 口径、秒传状态、归档和服务健康结果不回归。

验收：采集阶段数据库事务数为 0；任务链完整快照每轮最多调用一次；任一模块抛错时其他模块仍可生成成功 payload。

独立提交：`refactor(home): split live summary into modules`

## 阶段 3：后台单飞刷新器

目标：让所有慢读取离开首页请求，并在多线程和重复触发下只运行一轮。

1. 新增 `services/nasemby-core/app/home_summary_refresh_runtime.py`，实现进程内非阻塞锁、SQLite 租约和模块逐项落库。
2. 刷新器显式接收 clock、采集器与仓储；内部禁止使用请求上下文，也不从 GET 路由反向触发。
3. 外部读取全部在事务外进行；每个成功或失败模块使用独立短事务落库，最后用短事务释放租约。
4. 在 `main.py` 注册 `home-summary-refresh` 后台线程：服务启动后尽快首刷，后续固定周期刷新；重复调用 `start_background_runtime()` 不重复创建线程。
5. 调整 `register_home_summary()` 的注册顺序，确保刷新器创建时任务链、qB、Symedia、Emby、RSS 与追更工作台依赖均已注册。
6. 新增 `test_home_summary_refresh_runtime.py`，并扩展 `test_source_contract.py` 覆盖进程锁、SQLite 租约、重复定时、模块超时、租约过期和后台线程单例。

验收：连续或并发触发刷新只执行一份外部采集；一项超时不会阻止其他模块提交；刷新进行时 SQLite 读请求不被长事务阻塞。

独立提交：`feat(home): refresh cached summary in background`

## 阶段 4：纯读首页 GET 与空缓存响应

目标：正式把 `/api/v2/home/summary` 切换为 SQLite 纯读路径。

1. `HomeSummaryService.snapshot()` 只用一次 SQLite 只读连接加载全部 `global` 行和当前北京时间的日期行，再在内存中组装兼容响应。
2. GET 禁止调用任务链 `full_snapshot()`、Torra、qB、Symedia、Emby、RSS、追更工作台、刷新器或任何写方法。
3. 空缓存返回结构完整的 HTTP 200；`healthState=evidence_insufficient`，可空的关注项值为 `null`，旧非空数值字段保持原类型并通过模块/统计确认状态投影为未知。
4. 保留现有顶层字段、数组和对象；新增可选 `modules` 元数据，不删除字段、不改名、不改变现有状态码语义。
5. `generatedAt` 只表示本次缓存组装时间；每项事实的新鲜度读取模块 `observedAt/freshUntil/confirmation`。
6. 北京时间午夜后只读取新日期的 `archive_today` 与 `rss_resource_center`；新日缓存尚未建立时立即显示未知，禁止沿用昨日值。
7. 扩展 `test_home_summary_runtime.py`：对所有外部客户端设置“调用即失败”探针，验证首次空缓存、部分缓存、过期缓存、昨日缓存和 SQLite 不可读错误。

验收：容器冷启动时首次 GET 在 500ms 内返回；GET 的 Torra、qB、Symedia、RSS、Emby、任务全快照和追更工作台调用数全部为 0；只有 SQLite 无法读取或响应无法组装时返回 `502 HOME_SUMMARY_READ_FAILED`。

独立提交：`refactor(home): serve summary from sqlite cache`

## 阶段 5：两项部分确认统计

目标：保留已确认事实，同时诚实表达未确认覆盖范围。

1. 下载完成未入库按规范任务目标互斥划分为 `confirmedNotArchived`、`confirmedArchivedOrProtected` 和 `unconfirmed`。
2. 旧 `value` 只计 `confirmedNotArchived`；新增 `unconfirmedCount` 只计无当前可靠 Symedia 事实的目标，两组不得相交。
3. 追更缺集按追更条目互斥划分 `confirmedProgress` 与 `unconfirmedProgress`；旧 `value` 只汇总明确数组中的缺集数，新计数表示尚未提供进度的追更条数。
4. 为相关 `focusItems` 增加可选 `confirmation/unconfirmedCount/unconfirmedUnit/observedAt/freshUntil/errorReason`，旧 `value/state/detail` 保持兼容。
5. 刷新失败但有历史成功值时返回“上次确认值 + 当前暂未确认”；从未成功过才返回 `null/unknown`。
6. 测试 2+14、6+131、确认 0、确认 0 加未确认、模块失败保留旧值，以及两组集合严格无交集。

验收：能够稳定显示“已确认未入库 2 个 · 另有 14 个暂未确认”和“已确认缺失 6 集 · 131 条追更尚未提供进度”；未知对象不按 0 或未完成计算。

独立提交：`feat(home): expose partial confirmation counts`

## 阶段 6：前端兼容展示

目标：让首页正确消费模块新鲜度与部分确认字段，不把加载失败投影成 0。

1. 扩展 `src/types/homeSummary.ts` 的可选模块元数据与关注项增量字段，保留所有旧字段定义。
2. 更新 `Overview.tsx`：首次加载、超时和请求失败显示未知；成功返回的明确 0 继续显示 0。
3. 两个关注项优先显示“已确认 + 暂未确认”；其余旧响应仍按 `value/state/detail` 正常渲染。
4. 模块当前刷新失败时展示上次确认值、对应证据时间和“当前暂未确认”，不让整个首页进入统一未知态。
5. 刷新按钮只重新 GET 当前缓存，不触发后台重汇总。
6. 增加前端纯函数或组件测试；运行 TypeScript 类型检查和生产构建，检查桌面与 390×844 下文案不溢出。

验收：任一模块未知不遮蔽其他模块；真实 0 与未知可辨认；旧响应不带新增字段时页面仍可用。

独立提交：`feat(home): render cached confirmation metadata`

## 阶段 7：文案、契约与发布门禁

目标：收口同波次低风险文案，并完成 API、性能和安全验证。

1. 将 `MCC_SUBSCRIPTION_SCHEDULER_ENABLED` 的普通文案改为“Torra 订阅搜索”，明确关闭不影响“候选来源更新”；设置页不再显示“后台订阅定时扫描”。
2. 候选刷新响应可选新增 `skippedCount/errorCount`，旧 `skipped` 保持原值；活动文案分别显示正常跳过与真实错误。
3. 重点活动中只对已被后端确认可折叠的后台记录显示“累计后台同步 N 次”；人工操作和错误记录继续逐条展示。
4. 使用 `api-contract-review` 复核：新增字段可选、旧字段与状态码兼容、GET 无副作用、错误结构一致、公开 payload 与错误原因已脱敏。
5. 同步 `README.md`、`services/nasemby-core/README.md`、`services/nasemby-core/DESIGN.md`、`src/DESIGN.md` 与相关总计划状态。
6. 执行完整回归、构建、差异检查和敏感字段扫描；记录冷启动与热读取耗时。

验收：设置页同时清楚区分“候选来源更新”和“Torra 订阅搜索”；活动中跳过与错误可分别对账；首页冷启动与模块隔离门禁全部通过。

独立提交：`fix(ui): clarify summary and automation status copy`

## 完整门禁

```powershell
Set-Location services/nasemby-core
python -m unittest tests.test_home_summary_repository tests.test_home_summary_refresh_runtime tests.test_home_summary_runtime tests.test_source_contract -v
python -m unittest discover -s tests -t .
Set-Location ../..
npm run build
git diff --check
```

另外执行：

1. 冷启动空库连续请求 20 次，首次与 P95 均小于 500ms。
2. 对 Torra、qB、Symedia、Emby、RSS、任务全快照和追更工作台注入调用计数，确认首页 GET 全部为 0。
3. 并发触发 10 次后台刷新，只产生一次采集和一份租约。
4. 注入单模块超时、SQLite 写入中断和进程崩溃，确认可靠旧值可读且租约最终可恢复。
5. 在 Asia/Shanghai 23:59:59 与次日 00:00:00 两侧验证日期缓存隔离。
6. 扫描缓存表、HTTP 响应和日志，确认不存在路径、文件名、hash、URL 查询、Cookie、Passkey、Authorization 或远端原始 ID。

## 最终提交顺序

1. `feat(home): add module summary cache repository`
2. `refactor(home): split live summary into modules`
3. `feat(home): refresh cached summary in background`
4. `refactor(home): serve summary from sqlite cache`
5. `feat(home): expose partial confirmation counts`
6. `feat(home): render cached confirmation metadata`
7. `fix(ui): clarify summary and automation status copy`

每个阶段独立通过对应测试后再进入下一阶段。任何阶段回滚都不得删除缓存表或把新状态反写旧任务字段；旧版本可以忽略新表继续运行。
