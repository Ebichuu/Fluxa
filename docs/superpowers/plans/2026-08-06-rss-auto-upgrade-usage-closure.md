# Fluxa RSS 自动追更洗版与使用闭环代码更新计划

日期：2026-08-06
状态：实施中（A–C 已完成；D 代码与模拟回归已完成，待实机链路验收）
实施边界：只修改 Fluxa；不修改 Torra、qBittorrent、Symedia 或 p115client
核心优先级：RSS 种子数据与既有 Torra 订阅链路优先，Fluxa 尚未正式启用的其他历史数据不做清理

## 实施进度（2026-08-06）

- 阶段 A：状态契约、基线五态、执行模式和用户文案已完成。
- 阶段 B：产物级候选投影、多集范围和全覆盖冠军约束已完成。
- 阶段 C：历史基线预览、确认初始化和生产桥接已完成。
- 阶段 D：产物级预检、10 分钟令牌、人工确认、qB 精准添加、稳定动作收据、限流、下载器映射核验和重启恢复已完成模拟回归。
- 阶段 D 实机验收尚未完成：仍需确认 qB 保存目录与分类、mover、p115client、Symedia 和 Emby 全链路。
- 阶段 E 未开始；设置接口继续拒绝 `automatic`，前端只允许 `disabled/manual`。

## 1. 结论

当前 Fluxa 已完成 RSS 收集、订阅匹配、Torra 权重规则读取、本地兼容评分和季集冠军判定，但尚未形成完整自动洗版闭环。

目前真实链路为：

`PT RSS → Fluxa 收集 → 关联 Torra 追更 → Torra 规则评分 → 展示候选 → 人工预检`

目标链路为：

`PT RSS → 关联既有 Torra 追更 → 持续评分并保留唯一冠军 → 与当前版本比较 → 安全提交 qB → Torra 既有目录/搬运插件 → p115client 秒传 → Symedia 入库 → 新版本成为基线 → 后续继续观察`

“保留一份最高分”表示同一目标同一时刻只有一个可执行冠军、只提交一次下载，不删除 RSS 原始记录，也不提前删除当前媒体版本。实际覆盖仍交给 Symedia 权重保护。

## 2. 当前已确认的问题

### 2.1 自动执行没有闭环

- `torra_quality_watch_enabled` 已开启，质量观察会运行；
- “允许下载升级候选”当前关闭；
- 即使开启，现有接口仍要求人工确认；
- `preview_exact_download()` 最后固定返回 `TORRA_EXACT_RESOURCE_ENDPOINT_UNAVAILABLE`；
- `QbittorrentClient` 当前只有读取、状态汇总和文件查询能力，没有精准添加种子的写入能力。

因此当前只能自动评分，不能把指定 RSS 冠军自动送入下载链路。

### 2.2 多集种子在界面重复出现

后端按 `subscription_key + unit_key` 返回候选组。同一 `S01E26-E33` 产物会投影成 8 个集级组，前端显示 8 张相似卡片。

底层按集比较是正确的，但用户操作必须按唯一 `artifactKey` 聚合，避免重复提交同一 torrent。

### 2.3 空状态混用了不同原因

“追更洗版 0”目前统一解释为“没有严格高于当前版本的候选”，无法区分：

- 尚未建立基线；
- 观察窗口未激活；
- 身份或所有权未确认；
- 规则暂时不可读取；
- 已评分但确实没有更高分版本。

### 2.4 普通页面仍有状态口径和操作断点

- 首页把 qB 活跃任务写成“正在下载”，会把停滞任务误报成正常下载；
- 候选来源刚完成更新时，追更/发现页仍可能显示旧的调度时间；
- Symedia 失败详情只有“查看原因”，缺少可执行的下一步入口；
- 追更普通卡片仍可能展示 Symedia 原始文件路径；
- 任务中心重点活动默认返回过多历史记录；
- 发现页“最近追更”首次读取较慢，缺少明确的超时或缓存状态。

## 3. 不可突破的安全边界

1. 只有已经唯一关联到 Torra 订阅的 RSS 候选才允许进入下载预检。
2. 必须同时确认 TMDB、媒体类型、季号、集号范围、Torra 订阅 ID 和唯一 `artifactKey`。
3. 多集产物必须在它覆盖的所有集级目标中都是冠军；只赢一部分集时禁止提交。
4. Torra 规则必须在执行前实时重读并校验 `ruleHash`；读取失败时不使用过期规则执行。
5. 保存路径或下载器归属任一无法从 Torra 订阅可靠取得时禁止提交；qB 分类仅在 Torra 明确提供时使用，缺失时省略。
6. Fluxa 不根据标题推测目录、分类、订阅或季集归属。
7. 不访问 PT 详情页，不回显 RSS 下载地址、Cookie、Passkey 或 torrent 私密参数。
8. 不删除 RSS 条目、当前媒体文件、Torra 订阅或 qB 任务。
9. 自动模式必须由用户单独开启；默认保持关闭。
10. 所有提交都要有幂等收据、限流、冷却、执行前复查和脱敏审计。

## 4. 目标状态模型

### 4.1 分析与执行分离

新增两个独立状态：

- `analysisState`：`disabled | collecting | scoring | ready | blocked | unknown`
- `executionMode`：`disabled | manual | automatic`

兼容规则：

- 旧 `torra_quality_watch_enabled=false` 映射为 `analysisState=disabled`；
- 旧 `MCC_TORRA_REWASH_DOWNLOAD_ENABLED=false` 是下载硬门禁，任何执行模式都不能绕过；
- 旧字段继续返回，普通页面只读取新状态；
- 不再用“影子评分”作为用户可见名称，统一显示“Torra 规则评分”。

### 4.2 候选状态

每个唯一 RSS 产物至少处于以下状态之一：

- `scoring`：正在根据最新 Torra 规则评分；
- `waiting_baseline`：缺少可比较的当前版本；
- `monitoring`：已评分，等待后续更高分候选；
- `upgrade_available`：严格高于当前基线；
- `protected`：同分、低分或被 Torra 规则拒绝；
- `partially_best`：季包只在部分集胜出，禁止提交；
- `ready_manual`：通过全部预检，可人工确认；
- `ready_automatic`：通过全部预检，可由自动调度提交；
- `submitted`：已提交 qB，等待下载或下游结果；
- `completed`：新版本已形成可靠成功基线；
- `blocked`：身份、所有权、规则、目录或外部服务证据不足。

### 4.3 基线状态

空状态必须分别返回：

- `baseline_ready`：已有唯一当前版本及评分；
- `baseline_pending`：已有下载/入库过程，等待结果；
- `baseline_missing`：尚未建立基线；
- `baseline_conflict`：存在多个当前版本或所有权冲突；
- `baseline_expired`：历史观察窗口结束，只保留历史结果。

## 5. 分波次实施

### 阶段 A：先修状态契约和用户文案

目标：不增加外部写操作，先让页面准确表达当前能力。

后端：

- 扩展 `/api/v2/subscription-automation/settings`：新增 `analysisState`、`executionMode`、`executionEnvironmentEnabled`、`baselineCounts` 和 `automaticEligibleCount`；
- 扩展 `/api/v2/rss-matches?view=groups`：每组增加 `baselineState`、`blockerCode`、`nextAction`；
- 调度状态统一从 `candidate_source_scheduler_state` 读取；人工刷新和定时刷新分别记录来源，但共享最近一次成功来源更新时间；
- 新字段全部可选，旧字段不删除、不改型。

前端：

- “影子评分”统一改为“Torra 规则评分”；
- 设置页明确显示“自动评分”和“允许执行”是两个开关；
- 追更洗版空状态按基线、身份、规则和无升级分别展示；
- 缺少基线时提供“预览历史基线”入口；
- 首页将“正在下载”改回“qB 活跃任务”，停滞项另标“需要检查”。

本阶段不改变 RSS 收集、评分和下载行为。

### 阶段 B：增加产物级候选投影

目标：一个 torrent 只显示一张决策卡、只产生一个执行动作。

后端保留现有集级冠军计算，新增产物级只读投影：

- 聚合键：`subscription_key + artifact_key`；
- 返回 `coveredUnits`、`coveredEpisodeStart`、`coveredEpisodeEnd`；
- 返回每个集级结果和 `winsAllCoveredUnits`；
- 只有覆盖范围内全部集均由该产物获胜时，才可成为可执行冠军；
- 产物级 ID 使用稳定哈希，不把内部 `artifactKey` 直接暴露给普通页面；
- `/api/v2/rss-matches` 新增兼容查询 `view=artifact-groups`，原 `view=groups` 保留给诊断和旧前端。

前端候选卡显示示例：

> Overdo · S01E26–E33
> 覆盖 8 集 · 77.2 分 · 当前唯一冠军

展开后再显示 E26–E33 的集级比较，不允许每集分别提交同一个产物。

### 阶段 C：补齐基线初始化与持续更新

目标：让“没有升级候选”具备可信含义。

- 复用现有 `quality_watch_baseline_init_runtime.py`；
- 设置页增加“预览历史基线 → 选择 → 明确确认”入口；
- 只初始化具备 Torra 订阅、TMDB、季集、唯一产物所有权和可靠成功时间的目标；
- 不自动吞入 Fluxa 其他历史数据；
- 未选择或证据不足的项目保持 `baseline_missing/needs_review`；
- 新的 qB/Symedia 成功事实通过现有桥接器持续更新基线；
- Symedia 成功或其他已确认的正式当前版本证据到达前，不把“已提交”直接当作新基线。

### 阶段 D：实现受 Torra 订阅约束的 qB 精准提交

目标：不修改 Torra，也能把 RSS 唯一冠军放入现有 Torra 分类和秒传链路。

新增 `QbittorrentClient.add_torrent()`：

- 调用 qB Web API `/api/v2/torrents/add`；
- 参数只允许 `download_url`、Torra 订阅的 `save_path`、可选的明确 qB 分类和 Fluxa 审计标签；
- 禁止前端提交任意 URL、任意保存路径或任意分类；
- RSS 下载地址只在后端内存中使用，不写入公开响应和普通日志；
- 提交后立即清除 qB 摘要缓存，再从 qB 当前任务确认结果；
- 使用 `fluxa-rss` 与动作短 ID 作为标签，分类仍保持 Torra 原配置，不影响现有 mover。

重构 `preview_exact_download()`：

- 删除固定的 `TORRA_EXACT_RESOURCE_ENDPOINT_UNAVAILABLE` 阻断；
- 改为纯预检计划，不执行写入；
- 从 Torra 订阅读取并复核 `save_path` 与下载器归属；明确的 `qb_category/download_category` 原样使用，缺失时省略，普通媒体 `category`、目录、标题和历史任务不得作为 qB 分类；
- 检查当前 qB 是否已有同目标或同产物任务；
- 检查候选仍是覆盖范围内唯一冠军；
- 检查基线、规则哈希、评分、订阅和资源地址没有变化；
- 返回 10 分钟有效的 `previewToken`、公开影响范围和脱敏阻断原因。

新增执行接口：

`POST /api/v2/rss-artifact-groups/<group_id>/exact-downloads`

请求必须包含：

```json
{
  "confirm": true,
  "previewToken": "...",
  "idempotencyKey": "..."
}
```

执行前重新验证全部事实。`provider_actions` 使用：

- `provider = qbittorrent`
- `action_type = rss-exact-download`
- 幂等键包含目标、产物、基线产物和规则哈希

发生超时或容器重启时，先使用 qB 标签和当前任务确认是否已提交，不得盲目重放。

### 阶段 E：增加自动洗版执行协调器

目标：不依赖固定 10 分钟汇总期；PT 一小时后再发高质量版本也能继续比较和升级。

新增 `RssUpgradeExecutionCoordinator`，由现有质量观察调度器调用，不新建第二套候选台账。

每轮按以下顺序执行：

1. 读取最新 Torra 订阅和权重规则；
2. 唤醒未评分 RSS 候选；
3. 按集计算冠军，再生成产物级冠军；
4. 跳过无基线、身份冲突、部分胜出和已有活动下载的目标；
5. 对严格高于基线的唯一冠军生成执行计划；
6. 在 `executionMode=automatic` 且硬门禁已开启时提交 qB；
7. 写入动作收据并等待 qB、p115client 和 Symedia 的真实结果；
8. 新成功版本成为基线后，继续观察以后到达的更高分 RSS。

自动模式安全规则：

- 全局同时只提交 1 个 RSS 升级动作；
- 同一季集同时只允许 1 个活动下载；
- 复用现有每小时、每日限流和冷却配置；
- 默认 `minScoreGain=0`，严格遵循 Torra 分数，不另造质量规则；
- 新冠军到达但旧升级仍在下载时只记录等待，不并发提交；
- 同分、低分和规则拒绝只标记正常保护；
- qB 接受提交不等于入库成功，最终状态继续读取下游事实；
- 自动执行失败只影响该候选，不阻断 RSS 收集和页面读取。

首次开启自动模式必须经过：

`预览影响范围 → 明确确认 → 保存模式 → 记录审计`

### 阶段 F：打通个人使用流程

#### 首页

- 固定显示“qB 活跃任务”，不把活跃数翻译成正在下载数；
- 活跃但停滞时显示“1 个 qB 任务需要检查”；
- RSS 卡片增加“评分中、可升级、等待基线、已提交”四项结果摘要。

#### 资源中心

- 候选评分和追更洗版使用产物级卡片；
- 卡片主操作根据能力显示“查看比较”“精准下载预览”或“等待自动处理”；
- 自动模式下展示最近决策和下一步，不要求用户逐集操作；
- 保留原始 RSS 条目列表作为只读来源台账。

#### 追更与发现

- “加入追更（仅保存）”成功后固定提示“尚未在 Torra 生效”；
- 提供“查看 Torra 状态”或进入追更对账的下一步入口；
- Torra 推送关闭时不得使用“追更成功”暗示远端已经生效；
- 最近追更使用轻量缓存，5 秒仍无结果时显示“读取较慢，可进入追更页查看”，不得无限加载。

#### 任务中心

- Symedia 失败提供“前往控制室 · Symedia”或“重新关联作品”入口；
- 外部地址不直接下发，使用 Fluxa 内部相对路由定位服务卡片；
- 普通卡片只显示中文原因，完整路径只在技术详情；
- 重点活动默认最多 10 条，错误和人工动作优先；
- 后台同步继续折叠，更多历史通过“加载更多”读取。

#### 日历

- 继续保留集级日历；
- 在数量旁明确单位和范围，例如“本月整理入库 12 集”“今日归档 19 个文件”；
- 点击已入库集时展示实际整理时间，避免把播出日期误认为入库日期。

## 6. 预计修改文件

### 后端

- `services/nasemby-core/app/private_rss_repository.py`
  - 新增产物级候选投影和状态计数。
- `services/nasemby-core/app/private_rss_api_runtime.py`
  - 增加 `view=artifact-groups` 和产物级查询参数。
- `services/nasemby-core/app/rss_subscription_match_runtime.py`
  - 复用现有冠军计算，补产物级执行计划，移除固定不可用阻断。
- `services/nasemby-core/app/rss_shadow_scoring_runtime.py`
  - 内部兼容保留；公开语义改为 Torra 规则评分。
- `services/nasemby-core/app/qbittorrent_runtime.py`
  - 增加受限 `add_torrent()`、标签核对和提交后确认。
- `services/nasemby-core/app/subscription_automation_runtime.py`
  - 增加执行模式、自动资格和产物级动作编排。
- `services/nasemby-core/app/subscription_automation_api_runtime.py`
  - 增加产物级预览/确认接口并保持旧接口兼容。
- `services/nasemby-core/app/quality_watch_scheduler.py`
  - 调用 RSS 自动执行协调器，复用限流和单飞约束。
- `services/nasemby-core/app/quality_watch_baseline_init_runtime.py`
  - 输出可理解的基线状态和跳过原因。
- `services/nasemby-core/app/subscription_workbench_runtime.py`
  - 统一候选更新时间、来源和下一次运行时间。
- `services/nasemby-core/app/home_summary_runtime.py`
  - 增加 RSS 决策摘要，保持现有缓存架构。
- `services/nasemby-core/app/task_public_runtime.py`
  - 普通原因脱敏并增加安全的 Fluxa 内部处理入口。
- `services/nasemby-core/app/activity_log.py`
  - 收紧重点活动数量和优先级。
- `services/nasemby-core/app/runtime_settings.py`
  - 增加执行模式与影响说明。
- `services/nasemby-core/app/main.py`
  - 注册协调器和健康状态，不新增平行后台总调度器。

### 前端

- `src/components/pages/RssSeedLibraryPage.tsx`
  - 产物级卡片、真实空状态、自动模式状态和预览确认。
- `src/components/pages/Overview.tsx`
  - qB 文案及 RSS 决策摘要。
- `src/components/pages/DiscoverPage.tsx`
  - 仅保存后的下一步、路径隐藏和最近追更降级。
- `src/components/pages/TasksCenter.tsx`
  - 可执行问题入口及重点活动收口。
- `src/components/pages/SettingsPage.tsx`
- `src/components/pages/RuntimeSettingsPanel.tsx`
  - 分离评分、人工执行和自动执行模式。
- `src/components/pages/CalendarPage.tsx`
  - 补充单位与阶段时间说明。
- `src/services/api.ts`
  - 新增产物级组、预览和确认 API。
- `src/types/rssSeedLibrary.ts`
- `src/types/taskChain.ts`
- `src/types/homeSummary.ts`
  - 增加可选契约类型，不破坏旧字段。

### 测试

- `services/nasemby-core/tests/test_private_rss_repository.py`
- `services/nasemby-core/tests/test_rss_subscription_match_runtime.py`
- `services/nasemby-core/tests/test_subscription_automation_runtime.py`
- `services/nasemby-core/tests/test_subscription_automation_api_runtime.py`
- `services/nasemby-core/tests/test_quality_watch_scheduler.py`
- `services/nasemby-core/tests/test_qbittorrent_runtime.py`
- `services/nasemby-core/tests/test_subscription_workbench_runtime.py`
- `services/nasemby-core/tests/test_home_summary_runtime.py`
- 对应前端组件测试和 320/390/1440 视口回归。

## 7. 必须覆盖的验收场景

1. 同一 `S01E26-E33` 只显示一个产物级候选，底层仍保留 8 个集级比较。
2. 同一产物只赢部分集时显示“部分胜出”，不能提交 qB。
3. 候选严格高于基线且通过全部预检时，人工模式可以预览并确认一次。
4. 自动模式下，同一产物重复轮询不会产生第二个 qB 任务。
5. PT 一小时后发布更高分版本时，新版本能重新成为冠军并进入后续比较，不依赖固定汇总期。
6. 同分、低分和 Torra 规则拒绝均归为正常保护，不下载。
7. 规则在评分后发生变化时，旧预览失效并重新评分。
8. Torra 订阅、TMDB、季集、保存路径或下载器任一不确定时禁止提交；qB 分类如有值必须来自 Torra 明确字段，缺失时省略。
9. qB 已存在同目标任务时进入等待，不并发下载。
10. qB 接受后容器重启，恢复流程不会重复提交。
11. RSS 下载地址、Cookie、Passkey、内部路径不出现在响应、活动日志和普通页面。
12. qB 完成后，文件确实进入现有 Torra 分类目录，由 mover 和 p115client 接住，再被 Symedia 入库。
13. 下游失败时保留动作收据和真实阶段，不能把 qB 接受伪装为洗版完成。
14. RSS 条目数量在升级执行前后不减少。
15. 首页、资源中心、追更和任务中心对同一任务给出一致结论。

## 8. 实机灰度顺序

1. 阶段 A–C 先上线，只读验证 24 小时。
2. 阶段 D 上线后只开启人工模式。
3. 选择一条已有 Torra 订阅、保存路径与下载器明确的剧集执行一次精准下载；同时记录 qB 分类是否由 Torra 明确提供。
4. 依次确认：qB 保存路径与分类正确、mover 接力、p115client 秒传、Symedia 入库、Emby 后续可见。
5. 确认没有重复 qB 任务、没有跨分类、没有 RSS 数据减少。
6. 阶段 E 先对单条追更启用自动模式，再逐步扩大范围。
7. 稳定 48 小时后才允许开启全局自动模式。

## 9. 回滚方案

- 第一回滚手段：将 `executionMode` 切回 `disabled`，停止新提交，不影响 RSS 收集和评分；
- 第二回滚手段：关闭 `MCC_TORRA_REWASH_DOWNLOAD_ENABLED` 硬门禁并重启 Fluxa；
- 已提交的 qB 任务不自动删除，由用户在原工具确认；
- 新增 API 字段均为可选，旧前端仍能读取原接口；
- 产物级投影直接来自现有 RSS 匹配台账，不做破坏性数据库迁移；
- `provider_actions` 和活动记录保留审计，不因回滚清除。

## 10. 推荐实施顺序

严格按以下顺序推进：

1. A：状态与文案契约；
2. B：产物级候选卡；
3. C：基线状态与初始化入口；
4. D：人工精准 qB 提交及真实链路验收；
5. E：自动执行协调器；
6. F：个人使用流程收口。

在阶段 D 的实机链路未验证前，不进入阶段 E。这样既能保住现有 6,700 余条 RSS 数据，也能确保 Fluxa 的下载动作仍然沿用 Torra 分类、mover、p115client 和 Symedia 主链。
