# 质量观察生产桥接与历史基线初始化设计

日期：2026-07-31

状态：设计已确认，待实施

范围：只修改 Fluxa，不修改 Torra，不执行真实搜索或下载

## 1. 背景与结论

阶段 F 实机验收确认安全边界有效，但生产主链尚未接入 `QualityWatchRuntime.reconcile()`：任务快照、qB 下载完成和 Symedia 归档不会自动创建或推进观察单元。无观察单元时，RSS 匹配器没有可靠目标，规则评分、基线比较和冠军选择均无法运行。

本波次必须同时完成两条互相独立的链路：

1. 生产桥接：以后出现新的 Torra/qB 下载完成事实时，自动创建观察单元；Symedia 成功只推进已有单元的基线。
2. 历史初始化：由管理员预览并确认，将已有可靠资源补成历史观察基线。

两条链路均只写 Fluxa 本地 SQLite，不修改 Torra 订阅，不触发搜索、下载、qB、秒传、Symedia 或 Emby 动作。

## 2. 核心原则

- 事实只从任务快照的版本化本地证据读取，数据库事务内禁止访问外部服务。
- 历史事实与新事实由永久激活水位隔离，历史事实绝不自动创建观察单元。
- `plan_reconcile()` 是纯函数，`apply_reconcile_plan()` 只在已有 SQLite 事务中执行。
- 收据和观察单元变更必须原子提交。
- 历史初始化采用“预览 → 明确确认 → 本地执行 → 审计留痕”。
- `firstSuccessAt`、`baselineReadyAt` 只使用正式上游发生时间，不使用 `updatedAt`、`observedAt`、轮询时间或初始化时间。
- `observation_expired` 是明确终态，不进入候选查询、RSS 回扫或调度队列。

## 3. 数据模型

### 3.1 `quality_watch_bridge_state`

单例桥接状态：

- `bridge_version`
- `mode`：`off | shadow | apply`
- `activated_at`
- `created_at`
- `updated_at`
- `version`

`activated_at` 在桥接器首次从 `off` 进入 `shadow` 时以 UTC 写入，只写一次。服务重启、关闭再开启、`shadow/apply` 切换均不得刷新。普通产品接口不提供重置能力；只有未来单独设计的管理员迁移操作才允许重置。

### 3.2 `quality_watch_bridge_receipts`

收据唯一键由以下字段规范化后哈希：

```text
bridgeVersion
+ stage
+ factType
+ ownerTargetKey
+ artifactKey
+ sourceResultId 或 upstreamOccurredAt
```

保存字段至少包括：

- `receipt_id`
- `receipt_key`
- `bridge_version`
- `stage`
- `fact_type`
- `owner_target_key`
- `artifact_key`
- `source_result_ref`
- `upstream_occurred_at`
- `status`
- `reason_code`
- `attempt_count`
- `last_attempt_at`
- `next_retry_at`
- `evidence_version`
- `ownership_version`
- `created_at`
- `updated_at`

公开响应只返回脱敏引用，不返回路径、Torra 原始 ID、qB Hash、远端结果 ID 或文件名。

状态语义：

- `pending`：影子模式确认是新事实，等待正式模式重新验证。
- `applied`：收据与观察单元已在同一事务内提交；当前 `bridgeVersion` 下终态。
- `historical`：发生时间早于或等于永久水位，等待用户历史初始化。
- `needs_review`：缺少可靠时间、身份或唯一所有权；证据变化后允许重新判定。
- `rejected`：稳定且明确不符合桥接条件；当前 `bridgeVersion` 下终态。
- `retryable_failed`：临时失败，按退避策略重试；成功后可转为 `pending/applied`。

`bridgeVersion` 变化后进入新的收据命名空间，旧收据永久保留审计。

### 3.3 `quality_watch_baseline_init_runs`

预览时即创建批次：

- `run_id`
- `status`：`previewed | applied | stale | failed`
- `preview_fingerprint`
- `bridge_version`
- `policy_version`
- `idempotency_key`
- `selected_target_count`
- `summary_json`
- `created_at`
- `updated_at`
- `completed_at`

### 3.4 `quality_watch_baseline_init_items`

仅保存批次逐项审计，不写媒体事件：

- `run_id`
- `public_target_id`
- `owner_target_key`
- `artifact_ref`
- `season_number`
- `episode_number`
- `evidence_source`
- `first_success_at`
- `baseline_ready_at`
- `result`
- `reason_code`

失败批次不得写入成功初始化项。

## 4. 纯判定与原子应用

### 4.1 `plan_reconcile()`

纯函数必须显式接收：

- `now`
- 观察单元快照及版本
- 订阅身份和策略版本
- Torra 映射的本地版本化证据
- artifact 所有权及版本
- 当前事实证据
- 调用类型：新事实、已有单元推进或历史初始化

函数不得读库、读取系统时间或调用 Torra、qB、Symedia、Emby 和网络接口。输出为不可变的 reconcile 计划、判定状态和原因。

### 4.2 `apply_reconcile_plan()`

只接受已有 SQLite connection 和计划：

- 使用观察单元版本进行乐观校验。
- 不覆盖已有观察单元。
- 不迁移身份或修改订阅。
- 使用计划中的真实 UTC 时间。
- 过期基线直接写为 `observation_expired`。

普通 `reconcile()`、生产桥接和历史初始化均复用这两层，禁止维护第二套状态逻辑。

## 5. 生产桥接

### 5.1 三态发布

- `off`：不生成收据，不写激活水位。
- `shadow`：首次进入时写永久 `activatedAt`；只生成和更新收据，不创建观察单元。
- `apply`：重新验证 `pending` 和新事实，原子应用观察单元。

上线先运行 `shadow` 一天，核对新事实、历史事实、复核和拒绝数量后，再明确切换到 `apply`。

### 5.2 时间边界

- 全部时间转换为 UTC。
- `upstreamOccurredAt <= activatedAt`：`historical`。
- 缺少可靠发生时间：`needs_review`。
- 基线时间晚于 `now`、时间相互倒置或时间来源不在白名单：`needs_review`。
- 时间来源白名单仅包括 Torra/qB 正式下载完成时间和 Symedia 唯一文件归档成功时间。

### 5.3 阶段职责

- Torra/qB 明确下载完成：具备可靠订阅、季集和唯一 artifact 所有权时，允许规划 `is_new=True` 创建观察单元。
- Symedia 成功：只对已有观察单元规划 `is_new=False`，将“等待基线”推进为“基线确认”。
- Symedia 重新扫描或历史归档：无前置观察单元时标记 `historical`，进入历史初始化预览，不冒充新下载。
- 已有观察单元每次快照都可推进，但必须重新验证订阅、季集和唯一所有权；冲突时保持原状态并写独立诊断。

### 5.4 主事务与失败诊断

正式应用：

```text
BEGIN IMMEDIATE
  读取或创建 pending 收据
  重新读取观察单元并检查版本
  重新验证本地订阅、策略、事实和所有权版本
  plan_reconcile(...)
  apply_reconcile_plan(connection, plan)
  更新收据为 applied
COMMIT
```

主事务失败时：

```text
ROLLBACK
独立最小事务 upsert retryable_failed、attemptCount、lastAttemptAt、nextRetryAt
```

诊断事务也失败时不留收据；稳定媒体事实会在下次快照重新发现，不会丢失。

桥接器异常不得影响任务快照响应，也不得写永久媒体事件。

## 6. 历史基线初始化

### 6.1 资格

预览证据只来自当前版本化任务快照与 `resource_events` 中已持久化的稳定成功事实；两者使用相同的规范 artifact 身份合并去重。预览不读取外部服务，Symedia 暂时离线或当前快照裁剪旧记录时，已有稳定历史事实仍可参与判定。

只处理同时满足以下条件的目标：

- 已关联 Torra 订阅。
- 媒体类型、TMDB、季、集明确。
- 存在唯一成功文件证据。
- artifact 只有一个规范所有者。
- 当前没有观察单元。

预览分类：

- `safe_to_initialize`
- `needs_review`
- `skipped`

按订阅和季分组，展开后展示具体集数、脱敏证据来源和跳过原因。

### 6.2 预览与指纹

预览使用 POST 创建 `status=previewed` 的持久批次。指纹至少包含：

```text
bridgeVersion
+ 排序后的目标键
+ artifactKey
+ evidenceVersion
+ ownershipVersion
+ successTime
+ currentPolicyVersion
```

指纹不包含路径、远端原始 ID、文件名、URL、Cookie 或 Passkey。

### 6.3 确认和原子批次

确认请求必须包含：

- `confirm=true`
- `runId`
- `previewFingerprint`
- `selectedTargetIds`
- `idempotencyKey`

每批最多 200 个目标。超过 200 个必须拆成多个分别预览、分别确认的批次。

正式事务中重新计算全部选中目标的身份、所有权、事实、时间和策略版本。任一目标漂移则整批回滚并返回“预览已过期”，不允许部分跳过。

成功时在一个事务内创建全部观察单元、写 items 并把 run 更新为 `applied`。冲突或失败时主事务回滚，再用独立审计事务把 run 标记为 `stale/failed`。

### 6.4 历史时间和过期

- `firstSuccessAt` 使用最早可靠成功事实时间，并记录时间依据。
- `baselineReadyAt` 使用唯一基线文件的正式成功时间。
- 观察截止时间为真实 `baselineReadyAt + 24/48h`。
- 已过截止时间的单元直接写为 `observation_expired`，只保留历史，不进入 RSS 回扫、候选查询或调度。

## 7. API 与界面

设置页位置：

```text
追更设置
  → 追更洗版策略
    → 生产桥接：关闭 / 影子 / 正式
    → 历史基线初始化
```

建议 API：

- `PATCH /api/v2/subscription-automation/settings`：可选增量字段 `bridgeMode`。
- `GET /api/v2/subscription-automation/bridge-summary`：读取水位和收据汇总。
- `POST /api/v2/subscription-automation/baseline-initialization-previews`：创建预览批次。
- `POST /api/v2/subscription-automation/baseline-initializations`：确认执行。
- `GET /api/v2/subscription-automation/baseline-initializations/:runId`：读取批次审计。

接口只增加可选字段和新路由，不修改旧状态码或旧字段。GET 无副作用。所有写接口继续使用现有认证、同源校验、明确确认、幂等和公开脱敏规范。

执行结果至少返回：

- `initialized`
- `alreadyExisting`
- `insufficientEvidence`
- `expired`
- `conflicts`
- `reasonCounts`

## 8. 验收

### 8.1 生产桥接

- `activatedAt` 只写一次，重启、关闭再开启不变化。
- qB 和 Symedia 对同一文件生成不同收据。
- 水位之前和等于水位的事实只进入 `historical`。
- 无发生时间、未来时间、时间倒置进入 `needs_review`。
- 影子模式只生成收据，不创建单元。
- 正式模式重新验证 `pending` 后原子创建单元。
- 收据与观察单元无法出现一边成功、一边失败。
- 主事务失败后独立写 `retryable_failed`；重试成功转为 `applied`。
- Symedia 无前置单元时不能创建新下载单元。
- 已有单元可由 Symedia 成功推进基线。
- 桥接失败不影响任务快照 HTTP 响应。

### 8.2 历史初始化

- 预览分为安全、复核、跳过并按订阅季分组。
- 单批最多 200 个目标。
- 预览后任一事实或所有权变化，整批 stale 且零写入。
- 重复幂等请求只返回原结果。
- 已有观察单元不被覆盖。
- 真实历史时间被保留，初始化时间不进入业务时间字段。
- 已过期目标直接成为 `observation_expired`。
- 失败批次没有成功 items。
- 响应和日志不包含路径、远端 ID、文件名、下载地址、Cookie 或 Passkey。

### 8.3 回归与实机

- 后端完整回归、v2 机器契约、前端类型检查和生产构建通过。
- 390×844 与桌面设置页无横向溢出，预览列表可展开。
- fnOS 先运行影子模式一天，核对收据分类。
- 明确切换正式模式后，用一条新下载验证观察单元自动创建。
- 用一条 Symedia 成功验证已有单元基线推进。
- 执行一个不超过 200 项的历史初始化批次，核对真实时间和过期状态。
- 下载升级和缺集 PT 兜底继续关闭，直到阶段 F 复验通过。

## 9. 阶段状态

- E0-E2：代码完成，安全边界已实机确认。
- F：未完成，阻断于生产桥接和历史基线初始化。
- 本设计实施并通过影子、正式桥接和历史初始化实机复验后，才可重新判定阶段 F。
