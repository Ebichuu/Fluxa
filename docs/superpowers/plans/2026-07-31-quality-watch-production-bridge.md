# 质量观察生产桥接与历史基线初始化实施计划

对应规格：`docs/superpowers/specs/2026-07-31-quality-watch-production-bridge-design.md`

实施边界：只修改 Fluxa；不修改 Torra；不执行真实搜索、下载、qB、秒传、Symedia 或 Emby 动作；始终排除 `services/nasemby-core/mcc_data.db`。

## 阶段 1：纯判定与事务基础

目标：在不改变现有行为的前提下，将质量观察状态机拆成纯计划和事务应用。

1. 为 `plan_reconcile()` 增加纯函数测试：显式 `now`、观察单元快照、订阅身份、策略、所有权和事实输入；禁止隐式读库或系统时间。
2. 为时间白名单、未来时间、时间倒置、身份冲突、已有单元推进和 `observation_expired` 增加边界测试。
3. 在 `QualityWatchRepository` 增加 connection 级读取和 `apply_reconcile_plan()`；观察单元版本必须乐观校验。
4. 将现有 `QualityWatchRuntime.reconcile()` 改为同一计划器和应用器的兼容包装。
5. 跑 `test_quality_watch_runtime.py`、`test_quality_watch_repository.py` 和现有 RSS 基线测试，确认旧行为不回归。

验收：现有调用者响应不变；同一输入计划完全确定；`observation_expired` 不进入候选和调度查询。

## 阶段 2：生产桥接与影子收据

目标：接入任务快照，但默认只做影子判定。

1. 新增桥接状态和收据表；`activatedAt` 首次进入 `shadow` 时写一次。
2. 新增稳定收据键：`bridgeVersion + stage + factType + ownerTargetKey + artifactKey + sourceResultId/upstreamOccurredAt`。
3. 新增本地事实投影器，从任务快照提取 Torra/qB 下载完成和 Symedia 唯一文件成功；不得在事务内访问外部服务。
4. 在 `TaskChainV2Service.full_snapshot()` 完成适配和本地台账持久化后调用桥接器；桥接异常不得影响快照响应。
5. `shadow` 只写 `pending/historical/needs_review/rejected` 收据。
6. `apply` 模式中，收据和观察单元在一个 `BEGIN IMMEDIATE` 内提交。
7. 主事务失败后，用独立最小事务记录 `retryable_failed`；诊断事务失败则不留收据。
8. 已有单元每次快照允许由 Symedia 成功推进；无单元的 Symedia 事实只能进入历史分类。

验收：重启、关闭再开启不刷新水位；qB/Symedia 同文件收据不碰撞；影子模式零观察单元写入；正式模式崩溃恢复不漏事实。

## 阶段 3：历史基线初始化

目标：提供最多 200 个目标的持久预览和原子确认。

1. 新增 `baseline_init_runs/items` 表和公开脱敏 ID。
2. 从当前任务快照与 `resource_events` 稳定成功事实合并历史证据，使用统一 artifact 身份去重。
3. 预览分类为 `safe_to_initialize/needs_review/skipped`，按订阅和季分组；POST 预览时写 `previewed` run。
4. 指纹覆盖 bridgeVersion、排序目标、artifact、证据版本、所有权版本、成功时间和策略版本。
5. 确认请求严格校验 `confirm/runId/previewFingerprint/selectedTargetIds/idempotencyKey`，最多 200 项。
6. 正式事务重新核验全部选中项；任一漂移整批回滚。
7. 成功时原子写观察单元、items 和 applied run；失败后独立审计事务标记 stale/failed。
8. 历史时间直接驱动 `firstSuccessAt/baselineReadyAt`；过期项直接写 `observation_expired`。

验收：已有单元不覆盖；失败批次无成功 items；重复幂等请求只返回原结果；Symedia 离线时仍可读取持久历史事实。

## 阶段 4：API、设置页与契约

目标：完成受控产品入口和兼容响应。

1. 设置响应增加可选 `bridgeMode`；只允许 `off/shadow/apply`。
2. 新增只读 bridge summary、新建预览、确认初始化和读取 run 路由。
3. POST 创建资源返回 `201 + Location`；错误统一使用现有 `{code,error,request_id}`。
4. 所有写接口启用认证、同源校验、严格字段验证、明确确认和幂等。
5. 追更设置的“追更洗版策略”增加生产桥接三态控制和“历史基线初始化”区。
6. 专区展示影子收据汇总、永久水位、预览分类、订阅季分组和逐集证据来源；不显示路径、文件名或远端 ID。
7. 同步 `src/types`、API client、HTTP 契约、README、DESIGN、ROADMAP 和总计划状态。

验收：普通旧消费者不受影响；GET 无副作用；390×844 和桌面无溢出；高级诊断保持可读；下载升级和缺集兜底继续关闭。

## 完整门禁

```powershell
python -m unittest discover -s tests -t .
npm run build
git diff --check
```

另外执行：v2 机器契约计数、公开响应敏感字段扫描、SQLite 原子失败注入、影子到正式模式重启测试，以及浏览器桌面/390×844 验收。

## 提交顺序

1. `refactor(quality-watch): make reconciliation transactional`
2. `feat(quality-watch): add shadow production bridge`
3. `feat(quality-watch): add historical baseline initialization`
4. `feat(settings): control quality-watch bridge rollout`

阶段 F 只有在 fnOS 完成一天影子核对、正式桥接新下载验证、Symedia 基线推进和一次受控历史初始化后才能标记完成。
