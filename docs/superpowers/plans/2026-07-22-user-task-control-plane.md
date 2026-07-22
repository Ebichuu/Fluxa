# Fluxa 用户任务中枢实施计划

设计依据：`docs/superpowers/specs/2026-07-22-user-task-control-plane-design.md`  
日期：2026-07-22  
目标：在不改影视大厅的前提下，将管理工作台改造成可信的任务中枢。

## 实施原则

- 先建立后端事实和状态合同，再调整页面，避免做出新的状态误报。
- 对账状态、履约状态、健康状态使用独立字段，禁止一个枚举承载三个维度。
- `media_key → target_key → artifact_key` 负责资源身份，`chain_id` 负责任务串联，`subscription_id` 只负责来源关联。
- 新能力通过 `v2` 聚合接口和可关闭开关上线，保留现有接口作为兼容回退。
- 每个阶段可以单独部署、验收、关闭和回滚。
- 影视大厅只保留现有入口和代码，不修改 `src/components/media-hall`。

## 阶段 0：基线与回归保护

### 任务 0.1：保留浅色主题修复

文件：

- `src/styles/workbench.css`

步骤：

1. 保留当前浅色主题下活动筛选按钮的文字和背景对比度修复。
2. 在 1920×1080 深色、浅色主题下检查发现、追更、任务和控制室。
3. 单独提交，避免和后续结构改造混在一起。

验收：`npm run typecheck`、`npm run build`、桌面截图检查通过。

## 阶段 A：真实状态合同与导航壳

### 任务 A.1：统一健康状态判定

新增：

- `services/nasemby-core/app/health_state_runtime.py`
- `services/nasemby-core/tests/test_health_state_runtime.py`

修改：

- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/app/private_rss_repository.py`
- `services/nasemby-core/app/rss_subscription_match_runtime.py`

步骤：

1. 定义 `reconciliationState`、`fulfillmentState`、`healthState` 三个字段。
2. 定义 `observedAt`、`freshUntil`、`source`、`reasonCode`、`reasonText`。
3. 增加健康聚合器，按 `需要处理 → 证据不足 → 等待 → 正常保护 → 正常` 取最高优先级。
4. 调度器启动、执行、失败和最近成功时间写入应用级状态；工作台不得只读取订阅配置开关。
5. 调度全局关闭时，即使来源配置开启，也必须返回“已关闭/等待”，不得显示“已启用、正常”。
6. RSS 摘要增加采集状态、匹配器是否运行、匹配数、最近匹配时间和过期时间。

测试：

- 全局调度关闭、来源开关开启；
- 调度线程启动但没有成功轮询；
- 采集成功但匹配器未运行；
- 匹配器运行但 0 条命中；
- 证据超过 `freshUntil`；
- 一个资源同时存在正常保护和真实失败。

### 任务 A.2：TMDB 负缓存和匹配口径

修改：

- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/app/subscription_repository.py`
- `services/nasemby-core/tests/test_subscription_repository.py`
- 新增针对标题匹配的运行时测试。

步骤：

1. 空匹配结果使用短 TTL，并允许手动“重新检查”绕过负缓存。
2. 保存标题别名、年份、原始标题和最后匹配原因，避免可识别作品永久卡在空结果。
3. 统一工作台、发现和任务中心的 TMDB 未匹配计数口径。
4. 未匹配条目显示“证据不足/需要处理”，不能显示正常。

### 任务 A.3：原生 URL 导航和移动端管理菜单

新增：

- `src/app/navigation.ts`

修改：

- `src/app/App.tsx`
- `src/components/layout/AppTopNav.tsx`
- `src/styles/shell.css`

步骤：

1. 使用 History API 建立 `/`、`/discover`、`/following`、`/tasks`、`/control`、`/settings`、`/hall` 等路径映射，不新增路由依赖。
2. 保留旧 `/subscriptions`、`/overview`、`/rss-library`、`/calendar` 地址跳转，并保留查询参数、作品 ID、订阅 ID 和目标季集。
3. 桌面保留控制室和设置主导航；移动端主导航只保留首页、发现、追更、任务中心，控制室和设置收进管理菜单。
4. 将页面显示名称从“订阅”调整为“追更”，内部 `PageId` 可以保持兼容。
5. 影视大厅仍独立进入，主题切换按钮在影院大厅继续隐藏。

验收：

- 直接打开旧地址不会回到空白首页；
- 浏览器前进、后退和刷新保持当前页面；
- 1920×1080 与窄屏下没有导航重叠；
- 影院大厅无管理工作台样式污染。

## 阶段 B：首页结果摘要与只读追更对账

### 任务 B.1：首页统一结果接口

新增：

- `services/nasemby-core/app/home_summary_runtime.py`
- `services/nasemby-core/tests/test_home_summary_runtime.py`
- `src/types/homeSummary.ts`

修改：

- `services/nasemby-core/app/main.py`
- `src/services/api.ts`
- `src/components/pages/Overview.tsx`
- `src/styles/overview.css`

接口：

- `GET /api/v2/home/summary`

返回：

- 一个系统结论和健康状态；
- 今日唯一媒体单元的入库、下载中、待处理、需要处理数量；
- 正常保护数量和残留文件清理提示；
- 最近读取时间、证据新鲜度和问题列表；
- 每个问题对应的 `chainId`、`targetKey` 或订阅定位。

步骤：

1. 聚合任务链、调度器、RSS、Torra、qB、Symedia 和 Emby 的真实证据。
2. 删除首页技术链路四节点的主视觉，只保留用户结果摘要；技术详情进入任务中心。
3. 失败后成功按 `target_key` 去重，不重复计数。
4. 首页异常只展示结论和下一步入口，不渲染完整任务列表。

验收：调度关闭、服务掉线、证据过期、正常保护和真实失败分别显示不同状态；绿色正常不能只由接口 200 触发。

### 任务 B.2：Fluxa/Torra 只读对账接口

新增：

- `services/nasemby-core/app/subscription_reconciliation_runtime.py`
- `services/nasemby-core/tests/test_subscription_reconciliation_runtime.py`

修改：

- `services/nasemby-core/app/torra_subscription_sync_runtime.py`
- `services/nasemby-core/app/subscription_repository.py`
- `src/types/subscriptions.ts`
- `src/services/api.ts`

接口：

- `GET /api/v2/subscriptions/reconciliation`
- 保留现有 `/api/v2/torra/subscription-sync/preview` 作为兼容入口。

步骤：

1. 使用远端 ID、TMDB ID + 类型 + 季号、最后才使用标题别名进行匹配。
2. 标题猜测只能产生候选冲突，不能自动标记为已关联。
3. 返回对账状态、履约状态、健康状态和证据字段，不把“已完成”塞入对账枚举。
4. 158 条 Fluxa 和 112 条 Torra 必须以差异摘要显示，不能强行拼成一套数字。
5. 重复预览或重复导入同一远端 ID 不得创建重复镜像。
6. 远端消失只标记 `remote_missing`，不删除本地意图。
7. 第一阶段不开放远端删除、屏蔽、切季或反向编辑。

### 任务 B.3：追更页面拆分和意图语义

新增或拆分：

- `src/components/pages/FollowingPage.tsx`
- `src/components/subscriptions/SubscriptionReconciliationCard.tsx`
- `src/components/subscriptions/TorraSyncPreviewDialog.tsx`

修改：

- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/SubscriptionSettingsPage.tsx`
- `src/styles/discover.css`
- `src/styles/workbench.css`

步骤：

1. 发现页只保留榜单、搜索、作品详情和“添加追更”。
2. 追更页显示两层状态：对账、履约，并附健康状态和证据新鲜度。
3. 添加追更成功文案固定为“已保存追更意图，尚未同步到 Torra”。
4. 空列表提供“预览 Torra 订阅 → 确认导入”的只读导入流程。
5. 追更卡片支持刷新、失败重试读取、查看差异和进入对应任务链。
6. 工作台按游标或 limit 分页，避免一次返回数百 KB。

## 阶段 C：任务链身份、证据和异常分类

### 任务 C.1：资源身份和事件账本

新增：

- `services/nasemby-core/app/resource_identity_runtime.py`
- `services/nasemby-core/app/resource_task_repository.py`
- `services/nasemby-core/tests/test_resource_identity_runtime.py`
- `services/nasemby-core/tests/test_resource_task_repository.py`

修改：

- `services/nasemby-core/app/sqlite_runtime.py`
- `services/nasemby-core/app/task_chain_runtime.py`

数据表：

- `resource_chains`：保存 `chain_id`、`media_key`、`target_key`、来源和当前聚合状态；
- `resource_artifacts`：保存 `artifact_key`、qB hash、文件指纹和远端文件 ID；
- `resource_events`：保存阶段、状态、证据、时间、来源、原因和幂等键。

步骤：

1. 用稳定身份优先关联手工下载、补档和洗版任务。
2. 临时身份升级时写入别名事件，不生成第二条 chain。
3. 事件读取按 `observed_at` 和 `fresh_until` 判定是否仍可信。
4. 所有审计摘要走统一脱敏函数，过滤 passkey、Token、Cookie、密码和鉴权 URL。

### 任务 C.2：任务链 v2 第一段

新增：

- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`

修改：

- `services/nasemby-core/app/main.py`
- `src/types/taskChain.ts`
- `src/services/api.ts`

接口：

- `GET /api/v2/tasks/chains`
- 旧 `/api/tasks/chain` 保留并继续返回旧结构。

第一段只串：`Torra → qB → 115`。

每个阶段必须返回：状态、证据、时间、来源、原因、动作资格。证据不全时返回 `evidence_insufficient`，不能自动降级为 waiting。

### 任务 C.3：任务中心结果化

修改：

- `src/components/pages/TasksCenter.tsx`
- `src/styles/tasks.css`
- `src/components/layout/ConfirmDialog.tsx`

步骤：

1. 任务卡以 `chain_id` 为主，展示媒体、目标季集和当前阶段。
2. 增加“需要处理、证据不足、等待、正常保护、正常”筛选。
3. 订阅页、首页和发现详情跳转到任务中心时，使用 `chainId`、`targetKey` 精准聚焦，不再只跳任务中心首页。
4. 任务和活动列表使用游标分页或 limit + next cursor。
5. 低分保护显示“已保留低分源文件，可进入存储清理”，不显示重试按钮。

### 任务 C.4：异常中心作为任务筛选

新增：

- `services/nasemby-core/app/task_exception_runtime.py`
- `services/nasemby-core/tests/test_task_exception_runtime.py`

修改：

- `src/components/pages/TasksCenter.tsx`
- `src/types/taskChain.ts`

分类规则：

- 需要处理：可重试且达到动作资格，或超过重试上限；
- 证据不足：缺日志、数据过期或阶段未接入；
- 等待：仍在下载或已有计划重试；
- 正常保护：低分拒绝、重复跳过、已有更高版本。

## 阶段 D：安全操作、连接职责与完整验收

### 任务 D.1：阶段动作资格和预览

新增：

- `services/nasemby-core/app/task_action_runtime.py`
- `services/nasemby-core/tests/test_task_action_runtime.py`

复用：

- `services/nasemby-core/app/quality_watch_repository.py`
- `services/nasemby-core/app/automation_action_runtime.py`
- `services/nasemby-core/app/qbittorrent_action_runtime.py`
- `services/nasemby-core/app/emby_refresh_runtime.py`

接口统一返回：

- `allowed`；
- `blockedReason`；
- `affectedObjects`；
- `previewSupported`；
- `requiresConfirmation`；
- `idempotencyKey`；
- `cooldownUntil`。

动作边界：

1. 秒传失败且已有计划重试时禁止主动重试。
2. 达到上限且原始上传仍失败时允许预览重试。
3. Symedia 低分拒绝禁止重试。
4. Emby 刷新必须有新入库证据并通过冷却检查。
5. “停止追更”第一阶段只停止 Fluxa 本地跟踪，不操作 Torra 远端。
6. 修改分类暂不作为第一阶段普通操作。

所有真实动作必须：预览、确认、执行、留痕。活动记录引用 `chain_id` 和 `resource_event_id`，不得记录敏感凭据。

### 任务 D.2：控制室与设置职责收敛

修改：

- `services/nasemby-core/app/runtime_settings.py`
- `services/nasemby-core/tests/test_runtime_settings.py`
- `src/components/pages/ControlRoom.tsx`
- `src/components/pages/SettingsPage.tsx`
- `src/components/pages/RuntimeSettingsPanel.tsx`
- `src/styles/control-room.css`
- `src/styles/settings.css`

步骤：

1. 控制室统一维护 Torra、qB、Symedia、Emby 的地址、凭据测试、能力开关和高级系统配置。
2. 设置只保留 Fluxa 登录账号、界面偏好、追更偏好和通知对象/方式。
3. 同一个服务地址只能在控制室出现一次。
4. 工程字段只在控制室高级区域展开，普通设置不显示 `MCC_*`、`ENV_*`。
5. 已保存敏感值不回显，空输入不覆盖原值，显式清除才删除。

### 任务 D.3：分页、日志脱敏和发布门禁

修改或新增：

- `services/nasemby-core/app/activity_log.py`
- `services/nasemby-core/tests/test_activity_api_runtime.py`
- `.github/workflows/*`
- `README.md`
- `docs/superpowers/plans/2026-07-22-user-task-control-plane.md`

步骤：

1. 工作台、任务列表和活动记录统一分页；日历按月加载，日期详情延迟读取。
2. 审计日志脱敏 RSS passkey、Token、Cookie、密码和外部鉴权参数。
3. GitHub Actions 先构建版本标签和镜像摘要，运行冒烟验证后才移动 `latest`。
4. 发布同时记录 Git SHA、镜像 digest 和版本号。

## 验证矩阵

### 后端

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
```

重点覆盖：

- 调度配置与真实运行状态不一致；
- RSS 采集成功但匹配未运行或无命中；
- TMDB 负缓存重试和临时身份升级；
- Fluxa/Torra 重复导入、远端消失和类型冲突；
- chain_id 跨订阅、下载、洗版和补档任务稳定；
- 证据过期、脱敏、动作资格、幂等和冷却。

### 前端

```powershell
npm run typecheck
npm run build
git diff --check
```

人工验收：

1. 1920×1080 深色和浅色：首页结论、追更对账、任务聚焦、控制室和设置。
2. 窄屏：四个主入口、管理菜单、抽屉和弹窗不重叠。
3. 旧 URL：收藏链接跳转后保留作品、订阅和 chain 定位。
4. 影院大厅：进入、主题、播放和现有视觉完全不受影响。
5. 失败场景：调度关闭、依赖掉线、RSS 0 命中、证据过期、正常保护和真实失败。

### 实机与发布

- fnOS 只读检查首页结论、追更对账和任务聚焦；
- 不在验收中删除 Torra 远端订阅，不执行未经确认的批量写入；
- GitHub Actions 冒烟成功后再更新 `latest`；
- 镜像启动端口保持 8987，Compose 与 `.env` 同目录。

## 回滚策略

- 阶段 A：关闭新导航和首页摘要开关，回退到现有内存页签和旧首页接口。
- 阶段 B：保留本地订阅台账，关闭对账展示，不执行任何 Torra 写操作。
- 阶段 C：保留旧 `/api/tasks/chain` 和旧任务卡，停止读取 v2 事件账本。
- 阶段 D：关闭动作入口，保留只读诊断和原工具跳转。
- 数据库迁移只增不删；回滚代码不能删除新表或历史事件。

## 建议提交拆分

1. `fix(ui): preserve light workbench contrast`
2. `feat(status): make scheduler and rss health truthful`
3. `feat(navigation): add task-oriented route shell`
4. `feat(home): add outcome summary`
5. `feat(subscriptions): add torra reconciliation states`
6. `feat(tasks): add resource identities and evidence timeline`
7. `feat(actions): add stage-aware preview and audit`
8. `chore(release): gate latest image on smoke checks`
