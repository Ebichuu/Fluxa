# Fluxa 可信影音任务中枢迁移实施计划

设计依据：`docs/superpowers/specs/2026-07-27-trustworthy-media-pipeline-migration-design.md`

实施路线：立即执行方案 A；方案 B 是兼容迁移完成、观察期通过并删除旧字段后的目标结构；不采用只改前端文案的方案 C。

## 1. 完成定义

以下条件全部满足才算本计划完成：

1. `torra`、`qb`、`cloud115`、`symedia`、`strm`、`emby` 六类事实独立生成、公开和持久化；任何阶段成功都不补写相邻阶段。
2. 所有日常页面消费同一个 `pipelineOutcome`；`playable` 只能由当前目标范围内有效且明确的 Emby 证据生成。
3. Torra `completed` 只显示“获取目标已满足”，Symedia 成功只显示“整理入库完成”，STRM 成功只显示“播放入口已生成”。
4. 历史记录缺少明确事实时返回 `evidence_insufficient`；不再用 `false`、`0/N`、“未完成”或标题命中代替未知状态。
5. 旧字段全部由新事实单向投影，运行时不存在业务逻辑同时写新旧状态；新消费者不再读取旧字段派生状态。
6. 首页、导航角标、任务中心、全局搜索和作品总览对同一目标使用同一完成、处理中和需要处理统计。
7. 有真实异常时任务中心默认打开“需要处理”；没有异常但有活动任务时打开“处理中”；顶部媒体异常入口不再跳控制室。
8. 自动恢复有效的秒传失败计入处理中；存在 `recent_runs.result.failure_details` 时展示脱敏文件级详情，否则只说明“本次记录没有文件级详情”。
9. 榜单和发现来源只写候选池，不再直接新增追更或触发 Torra/其他 provider。
10. 用户明确加入后才建立追更意图；人工 `only_fluxa` 保留，不能按对账状态批量删除。
11. 历史自动污染数据具备只读预览、数据库备份、明确确认、幂等执行、条件迁移、复查和可审计回滚清单。
12. 追更不再显示错误的“Torra 运行中”或无证据 `0/N`；默认日历不包含候选和未确认污染记录。
13. Symedia 控制室文案准确区分历史接口、归档监控、STRM、调度和文件观察的已接入/未知状态。
14. 活动重点视图按业务运行聚合，默认不平铺大量 TMDB 未匹配和后台同步记录。
15. 后端全量回归、前端类型检查和生产构建、机器契约、Compose 配置、浏览器多视口与深浅主题回归全部通过。
16. 新模型经过至少 7 个连续自然日影子观察，并覆盖一次真实电影闭环和一次真实剧集集级闭环后，才允许另行提交旧字段删除。

## 2. 不可变约束

- 事实层不得新增混合多个阶段的 `acquisition`、`libraryDone` 或其他综合真值字段。
- `pipelineOutcome` 是唯一用户状态派生器；页面只能展示或分组，不能再次解释原始事实。
- 事实缺失与事实失败必须分开。`unknown/missing` 永远不等于 `failed/false`。
- 电视剧作品级 Emby 命中不能替代集级可播放证据。
- 下游成功不能反推上游执行方式；上游缺证据只进入诊断。
- 质量保护是正常结果，不计入需要处理。
- 来源不可用时保留最后事实并标记过期，不把连接异常写成媒体任务失败。
- 兼容响应只增加可选字段；旧字段删除必须进入后续契约版本。
- 自动化测试不得调用真实 Torra、qB、115、Symedia 或 Emby 写接口。
- 本计划不得自动开启生产调度、Torra 推送、下载、秒传、清理或数据迁移动作。
- 历史污染迁移执行必须由用户在预览后单独批准；计划实施和自动测试只允许使用临时数据库。
- 不处理现有未跟踪的 `services/nasemby-core/mcc_data.db`，除非用户另行要求。

## 3. 交付与提交规则

1. P0 的五个阶段必须分别提交，不得合并成一个大提交。
2. 每个 P0 阶段先增加失败用例，再实现，再运行该阶段定向回归和全部既有相关测试。
3. 当前阶段未通过验收前，不开始修改下一阶段消费者。
4. P0.1 和 P0.2 只增加事实与兼容响应，不改变旧页面结果。
5. P0.3 起逐页切换新结果；旧投影保留用于回滚和影子比较。
6. 所有数据库结构使用幂等初始化；现有数据库升级前使用 SQLite backup API 创建固定名称备份。
7. 公共 DTO 先经过 presenter 脱敏，再进入列表、详情、摘要、活动或日志。
8. 每个提交运行 `git diff --check`，并记录定向测试结果。
9. 本计划中的 `python -m unittest tests...` 命令默认在 `services/nasemby-core` 目录执行；全量 discovery 命令在仓库根目录执行。

建议提交序列：

```text
feat(tasks): add independent pipeline fact contract
feat(tasks): ingest six pipeline evidence sources
fix(tasks): derive user outcomes from playable evidence
fix(subscriptions): align following and calendar outcomes
feat(discover): separate candidates from follow intents
feat(symedia): expose service and strm evidence
fix(torra): consume secupload failure details
fix(operations): close task actions and migration review
refactor(ui): simplify media operations workspace
refactor(activity): aggregate business runs
docs(api): retire legacy pipeline fields
```

## 4. P0.1：事实契约、持久化与兼容投影

目标：建立六类事实和统一派生器的基础结构；旧消费者与旧响应行为保持不变。

### 4.1 涉及文件

- `services/nasemby-core/app/pipeline_fact_runtime.py`（新增）
- `services/nasemby-core/app/pipeline_outcome_runtime.py`（新增）
- `services/nasemby-core/app/resource_task_repository.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/app/task_public_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_pipeline_fact_runtime.py`（新增）
- `services/nasemby-core/tests/test_pipeline_outcome_runtime.py`（新增）
- `services/nasemby-core/tests/test_resource_task_repository.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`
- `services/nasemby-core/tests/test_source_contract.py`
- `src/types/taskChain.ts`
- `docs/API_CONTRACT.md`
- `docs/contracts/http-api-contract-v2.json`

### 4.2 实施步骤

1. 在 `pipeline_fact_runtime.py` 固定以下枚举和校验：
   - stage：`torra/qb/cloud115/symedia/strm/emby`；
   - state：`unknown/waiting/active/succeeded/failed/protected/not_applicable`；
   - scope：`movie/season/episode/file/system-category`；
   - evidence：`verified/inferred/missing`。
2. 定义 `PipelineFact` 规范化函数，统一字段白名单、时间解析、公开 `sourceRef`、原因码和脱敏文本。
3. 每个阶段公开一个当前摘要；存在多集或多文件时使用可选 `units`，每个 unit 必须有稳定 `unitKey` 和明确 scope。
4. 在 `pipeline_outcome_runtime.py` 建立纯函数派生器和真值表。第一阶段只生成新字段，不替换现有 `_user_state`。
5. 派生优先级固定为：当前 Emby 明确成功 -> `playable`；明确未恢复失败 -> `action_required`；活动任务/有效恢复计划 -> `in_progress`；正常保护 -> `protected`；正常等待 -> `waiting`；其余 -> `evidence_insufficient`。
6. 扩展任务 DTO，增加可选 `pipelineFacts`、`pipelineOutcome`、`outcomeCounts` 和 `contractVersion`。
7. 复用 `resource_events` 作为唯一事实事件台账：stage 只写六类事实；`payload_json` 保存 scope、unitKey 和已脱敏 sourceRef，不另建第二套任务事实表。
8. `resource_chains.state/health_state` 暂作为兼容投影；禁止任何来源适配器直接写它们。
9. 历史 `subscription/download/cloud115/library` 事件保持不可变；只有来源、evidence 和 scope 足以唯一映射时才转换成新事实，否则返回 `evidence_insufficient`，不得批量改写旧事件。
10. `task_public_runtime.py` 增加事实与结果 presenter，拒绝未知字段、路径、凭据、原始外部 ID 和未脱敏技术原因。
11. 现有 `state/userState/steps/stages/fulfillmentState/inLibrary` 保持响应类型与旧行为，但统一改由兼容适配器生成。
12. 增加影子差异结构的内部计算，只记录分类计数，不记录标题、路径或外部 ID。
13. 更新 TypeScript 可选类型；前端本阶段不读取新字段。

### 4.3 红线测试

- 单个事实包含非法 stage/state/scope 时拒绝，不静默降级。
- `unknown + missing` 不能转换为 `failed`。
- `inferred` Emby 事实不能生成 `playable`。
- 电视剧 title scope Emby 成功不能生成 `playable`。
- 集级 verified Emby 成功可以生成 `playable`。
- 同一阶段当前事实冲突返回 `EVIDENCE_CONFLICT`。
- presenter 不公开路径、Token、Cookie、Passkey、原始 qB hash、Symedia ID 或 Torra ID。
- 旧客户端响应快照在本阶段保持兼容。
- 同一事实重复写入不产生重复 `resource_events`。

### 4.4 验收与回滚

验证命令：

```powershell
python -m unittest tests.test_pipeline_fact_runtime tests.test_pipeline_outcome_runtime tests.test_resource_task_repository tests.test_task_chain_v2_runtime tests.test_source_contract -v
npm run typecheck
git diff --check
```

验收：新字段可选、稳定且经过脱敏；旧页面无行为变化。

回滚：删除可选字段注册和新模块调用即可；事实事件仍可保留为未消费审计记录，不根据旧字段反向重建。

## 5. P0.2：六类来源适配器

目标：把当前可验证来源转换成六类事实；没有明确来源的阶段保持未知。

实施状态：已完成（2026-07-28）。

### 5.1 涉及文件

- `services/nasemby-core/app/pipeline_fact_runtime.py`
- `services/nasemby-core/app/pipeline_source_fact_runtime.py`
- `services/nasemby-core/app/task_chain_runtime.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/app/episode_evidence_runtime.py`
- `services/nasemby-core/app/resource_identity_runtime.py`
- `services/nasemby-core/app/torra_read_runtime.py`
- `services/nasemby-core/app/qbittorrent_runtime.py`
- `services/nasemby-core/app/secupload_issue_runtime.py`
- `services/nasemby-core/app/symedia_read_runtime.py`
- `services/nasemby-core/app/emby_runtime.py`
- `services/nasemby-core/tests/test_task_chain_runtime.py`
- `services/nasemby-core/tests/test_pipeline_source_fact_runtime.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`
- `services/nasemby-core/tests/test_episode_evidence_runtime.py`
- `services/nasemby-core/tests/test_torra_read_runtime.py`
- `services/nasemby-core/tests/test_qbittorrent_runtime.py`
- `services/nasemby-core/tests/test_secupload_issue_runtime.py`
- `services/nasemby-core/tests/test_symedia_read_runtime.py`
- `services/nasemby-core/tests/test_emby_runtime.py`

### 5.2 实施步骤

1. Torra 适配器：
   - 远端订阅存在 -> `waiting` 或 `active`，只按真实运行字段区分；
   - Torra `completed=true` -> `torra.succeeded/TORRA_TARGET_SATISFIED`；
   - 不写 qB、115、Symedia、STRM 或 Emby 事实。
2. qB 适配器：
   - 使用明确任务状态、进度、完成时间和稳定 artifact 归属；
   - 缺少唯一媒体身份时事实保留在 artifact/诊断范围，不抢占媒体目标；
   - qB 完成只生成 `qb.succeeded`。
3. 115 适配器：
   - 只有插件明确文件结果或明确分类批次时生成 `cloud115` 事实；
   - Symedia 收到文件不能反推 115 已秒传；
   - 分类级证据使用 `system-category`，不伪造文件 unit。
4. Symedia 适配器：
   - 成功转移记录只生成 `symedia.succeeded`；
   - 低分、已有高质量、取消覆盖等生成 `protected`；
   - 明确真实失败生成 `failed`；
   - `dest` 含 `.strm` 只能作为 STRM 候选诊断，不能生成 verified STRM 成功。
5. STRM 适配器：
   - 当前没有独立明确来源时返回 `unknown/missing`；
   - 预留专用来源接口，但不根据 Symedia 成功或路径字符串推断。
6. Emby 适配器：
   - 电影 item 级 TMDB 命中可以生成 movie scope verified；
   - 电视剧必须唯一绑定 season/episode 后才生成 episode scope verified；
   - 作品级 series 命中只保留诊断，不生成 playable。
7. 删除 `_cloud_step` 中“有 Symedia 行即 115 done”的推断。
8. 删除 `_library_step` 和 `_item_state` 中“Symedia 成功即整链 completed”的事实写入；旧结果仅由兼容投影临时保持。
9. 扩充 artifact 类型，使 qB、115、Symedia、STRM 和 Emby 公开引用可以稳定去重，但只保存脱敏引用。
10. 事实适配失败时返回对应 stage unknown，并记录来源读取错误；不得阻断其他来源事实。

### 5.3 红线测试

- Torra completed、其余未知 -> 只有 torra succeeded，整体不是 playable。
- qB completed、115 未知 -> 只有 qb succeeded。
- Symedia 成功、Emby 未知 -> symedia succeeded，整体 evidence_insufficient。
- Symedia `dest=/x.strm` 但没有 STRM 服务结果 -> strm unknown。
- Emby 作品级剧集命中 -> emby 诊断存在，但整体不是 playable。
- Emby S01E03 明确命中 -> 仅对应 episode target playable。
- 季包成功不能批量生成整季各集 playable。
- 保护结果不进入 action_required。
- 各适配器局部失败不污染其他 stage。

### 5.4 验收与回滚

验证命令：

```powershell
python -m unittest tests.test_task_chain_runtime tests.test_task_chain_v2_runtime tests.test_episode_evidence_runtime tests.test_torra_read_runtime tests.test_qbittorrent_runtime tests.test_secupload_issue_runtime tests.test_symedia_read_runtime tests.test_emby_runtime -v
git diff --check
```

验收：六类事实来源互斥、可解释，旧页面仍从兼容投影读取。

回滚：恢复来源到兼容投影的调用，但保留新事实模块和台账；禁止用旧结果覆盖已保存事实。

## 6. P0.3：任务中心、首页与统一统计

目标：首批消费者切换到 `pipelineOutcome`，修正完成、处理中、异常、入口和默认筛选。

### 6.1 涉及文件

- `services/nasemby-core/app/pipeline_outcome_runtime.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/app/task_exception_runtime.py`
- `services/nasemby-core/app/task_public_runtime.py`
- `services/nasemby-core/app/home_summary_runtime.py`
- `services/nasemby-core/app/media_search_runtime.py`
- `services/nasemby-core/app/secupload_issue_runtime.py`
- `services/nasemby-core/tests/test_pipeline_outcome_runtime.py`
- `services/nasemby-core/tests/test_task_exception_runtime.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`
- `services/nasemby-core/tests/test_home_summary_runtime.py`
- `services/nasemby-core/tests/test_media_search_runtime.py`
- `src/types/taskChain.ts`
- `src/types/homeSummary.ts`
- `src/types/mediaSearch.ts`
- `src/services/api.ts`
- `src/app/navigation.ts`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/Overview.tsx`
- `src/components/layout/AppTopNav.tsx`
- `src/components/layout/GlobalMediaSearch.tsx`
- `src/styles/tasks.css`
- `src/styles/overview.css`
- `src/styles/shell.css`

### 6.2 实施步骤

1. 任务 summary/list/detail 增加 `outcomeState` 查询；允许重复值以支持页面分组。
2. 保留 `userState` 查询并映射到兼容投影；新前端只发送 `outcomeState`。
3. `outcomeCounts` 由同一批唯一 target 计算，列表、摘要和 ETag 使用相同快照。
4. 可播放目标去重固定为：电影按 mediaKey 计 1；电视剧按 episode targetKey 计数；只有明确完整集数范围且全部目标集 playable 时才额外生成季级完成，不用作品级 Emby 命中代替。
5. `completedAt` 的新替代字段改为 `playableAt`，只取 Emby 当前目标明确成功时间。
6. `resultText` 和 `primaryAction` 改为读取 `pipelineOutcome`；旧 `_user_state/_result_text/_completed_at` 只留在 legacy projector。
7. 首页拆分：
   - `mediaActionRequired`：任务中心可列出的媒体异常；
   - `auxiliaryAlerts`：RSS、服务和调度提醒；
   - `inProgress`：媒体活动任务加自动恢复中的系统任务；
   - `playableToday`：当日 Emby 明确可播放目标。
8. 首页标题和导航角标使用同一 `mediaActionRequired`；辅助提醒独立展示，不进入媒体红色数字。
9. 秒传 `recovering` 根据明确失败数计入处理中；没有文件名时仍可显示数量，但范围标记为分类级。
10. 任务中心无 URL 目标时先读取 summary 决定默认筛选：异常 -> 需要处理；活动 -> 处理中；否则 -> 已可播放/无需处理。
11. 任务中心主标签显示新状态；`waiting/protected/evidence_insufficient` 可在“无需处理”分组展示，但卡片必须保留真实状态徽标，不能统一写成正常。
12. 顶部健康按钮：媒体异常时深链任务中心；只有管理状态时进入控制室。
13. 首页问题卡：媒体项进入对应 chain/target；RSS 进入 RSS 页；服务进入控制室。
14. 删除任务 Hero 的空闲 `0 B/s` 主视觉；实时速度移到有活动下载时的次级信息。
15. 全局搜索和作品总览使用 `pipelineOutcome`，不再根据旧 `completed/inLibrary` 推断生命周期。
16. 影子差异计数暴露到高级诊断或内部指标，不显示具体私密标题。

### 6.3 红线测试

- Symedia 成功、Emby 未知不进入 completed/playable 计数。
- Torra completed 不进入 completed/playable 计数。
- Emby 集级明确成功同时出现在任务列表、首页和搜索 playable。
- 首页媒体异常数等于任务中心 action_required 总数和导航角标。
- RSS 单来源异常不增加媒体异常角标。
- 秒传计划重试有效时增加处理中，不增加需要处理。
- 无 URL 参数且 action_required > 0 时，任务中心首次列表请求就是 action_required。
- `userState=completed` 旧深链仍可用，但新 URL 写为 `outcomeState=playable`。
- 304、分页和刷新不产生统计漂移。

### 6.4 验收与回滚

验证命令：

```powershell
python -m unittest tests.test_pipeline_outcome_runtime tests.test_task_exception_runtime tests.test_task_chain_v2_runtime tests.test_home_summary_runtime tests.test_media_search_runtime -v
npm run typecheck
npm run build
git diff --check
```

浏览器验收：任务中心异常默认页、首页数字、导航角标、搜索结果、320/390/768/1440 与深浅主题。

回滚：前端恢复读取 legacy projector；新事实和新统计继续保留，不删除或改写事件。

## 7. P0.4：追更、对账与日历语义

目标：区分 Torra 获取目标与 Emby 可播放，取消无证据进度，限制默认日历数据范围。

### 7.1 涉及文件

- `services/nasemby-core/app/subscription_reconciliation_runtime.py`
- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `services/nasemby-core/app/calendar_timeline_runtime.py`
- `services/nasemby-core/app/contract_mapping.py`
- `services/nasemby-core/app/media_search_runtime.py`
- `services/nasemby-core/tests/test_subscription_reconciliation_runtime.py`
- `services/nasemby-core/tests/test_subscription_workbench_runtime.py`
- `services/nasemby-core/tests/test_calendar_timeline_runtime.py`
- `services/nasemby-core/tests/test_mcc_compat_runtime.py`
- `src/types/subscriptions.ts`
- `src/services/api.ts`
- `src/app/navigation.ts`
- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/CalendarPage.tsx`
- `src/styles/discover.css`
- `src/styles/calendar.css`

### 7.2 实施步骤

1. 对账响应增加 `torraFact` 和 `pipelineOutcome`；旧 `fulfillmentState` 只由兼容投影生成。
2. Torra completed 显示“获取目标已满足”，不再生成新语义 completed 或 chainState completed。
3. 追更状态文案固定为：已在 Torra、Torra 获取中、获取目标已满足、仅 Fluxa 保存、对账异常。
4. 删除 `linked -> Torra 运行中` 的固定映射；只有明确活动 run 才显示获取中。
5. `contract_mapping._progress` 不再使用 TMDB 总集数生成 `0/N`。只有明确集级事实可以生成 `confirmed/total`。
6. 有总集数但没有集级事实时返回结构化 `progress.state=unconfirmed`，前端显示“集数进度未确认”。
7. 日历数据源只接受：
   - 明确人工追更意图；
   - 已关联 Torra 的有效追更；
   - 明确媒体、季号和集号范围。
8. 自动候选、`migration_review` 和范围不明确记录不进入默认月/周视图。
9. 日历状态增加 playable；只有 Emby 集级事实可进入。
10. 状态未关联保留独立高级筛选和数量，不进入默认 `status=all`；兼容 URL 提供显式 `includeUnlinked=1`。
11. 日历、追更详情和作品总览复用相同 targetKey/outcome，不按标题再次匹配。
12. 追更统计中的 completed 替换为 playable；旧 completed 仅保留兼容字段。

### 7.3 红线测试

- Torra completed 的追更显示获取目标满足，但 completed/playable 统计为 0。
- 没有集级事实的 25 集剧返回 unconfirmed，不返回 0/25。
- 人工 only_fluxa 且身份/季集明确时保留在追更与日历。
- 自动来源候选不进入日历。
- Emby 作品级命中不把整季日历标记可播放。
- 单集 Emby 命中只改变对应日期和集号。
- 默认月视图不包含 unlinked；显式筛选可读取。
- 旧日历接口和旧字段类型保持兼容。

### 7.4 验收与回滚

验证命令：

```powershell
python -m unittest tests.test_subscription_reconciliation_runtime tests.test_subscription_workbench_runtime tests.test_calendar_timeline_runtime tests.test_mcc_compat_runtime tests.test_media_search_runtime -v
npm run build
git diff --check
```

浏览器验收：追更五类状态、未确认进度、默认日历范围、状态筛选、详情返回与 URL 刷新。

回滚：恢复追更/日历读取 legacy 字段；新事实不变，兼容查询继续可用。

## 8. P0.5：候选池、追更意图与历史污染迁移

目标：彻底停止榜单污染追更台账，并提供安全的历史数据分类和迁移工具。

### 8.1 涉及文件

- `services/nasemby-core/app/sqlite_runtime.py`
- `services/nasemby-core/app/subscription_repository.py`
- `services/nasemby-core/app/discover_candidate_runtime.py`（新增）
- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/app/subscription_compat_runtime.py`
- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_subscription_repository.py`
- `services/nasemby-core/tests/test_discover_candidate_runtime.py`（新增）
- `services/nasemby-core/tests/test_subscription_workbench_runtime.py`
- `services/nasemby-core/tests/test_mcc_compat_runtime.py`
- `src/types/subscriptions.ts`
- `src/services/api.ts`
- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/SubscriptionSettingsPage.tsx`
- `src/styles/discover.css`
- `docs/API_CONTRACT.md`
- `docs/contracts/http-api-contract-v2.json`

### 8.2 数据库结构

在同一个订阅 SQLite 中增加：

```text
discover_candidates
candidate_migration_runs
```

`discover_candidates` 至少包含 candidate_id、media_type、tmdb_id、season_number、title、year、source_key、state、payload_json、first_seen_at、last_seen_at、expires_at 和 version。

`candidate_migration_runs` 保存幂等键、预览指纹、备份标识、迁移数量、跳过数量、冲突摘要、响应 JSON 和时间。`sqlite_runtime.SCHEMA_VERSION` 随结构升级，并保持幂等初始化。

### 8.3 实施步骤

公开 API 固定为：

```text
GET  /api/v2/discover/candidates
POST /api/v2/discover/candidates/:candidateId/follow-previews
POST /api/v2/discover/candidates/:candidateId/follows
GET  /api/v2/subscriptions/candidate-migrations/preview
POST /api/v2/subscriptions/candidate-migrations
GET  /api/v2/subscriptions/candidate-migrations/:runId
```

1. 在 `SubscriptionRepository` 增加候选分页、来源 upsert、过期、读取和候选转追更事务。
2. 新增候选服务和公开 DTO；列表不返回来源原始响应、URL、Cookie、Passkey 或内部 ID。
3. `run_subscription_now` 改为只 upsert 本轮候选并写一次运行摘要：扫描数、新增、更新、跳过、错误。
4. 删除自动来源刷新后的 `merge_subscription_source_items -> write subscriptions -> queue provider` 路径。
5. 手动加入动作可以接收 candidateId；服务端重新读取候选、复核 TMDB/季号、建立 `intent_origin=manual` 的订阅，再按现有 activation 进入 provider。
6. 新建追更意图和外部动作保持两个事实：本地保存成功不等于 Torra 推送成功。
7. 迁移预览对历史订阅分四类：
   - manual：明确人工来源，保留；
   - downstream-owned：Torra link、resource chain 或下游 artifact 存在，保留；
   - candidate-eligible：明确 auto/source origin 且无人工/下游证据，可迁候选；
   - migration-review：来源不明、身份冲突或证据矛盾，保留待审。
8. 预览返回脱敏行、原因码、数量和数据库指纹，不修改任何数据。
9. 执行前使用 SQLite backup API 创建固定版本备份；备份失败则整个动作失败。
10. 执行接口要求 `confirm=true`、12–128 字符幂等键和预览指纹；数据库变化后返回 409，要求重新预览。
11. 单事务完成候选 upsert、eligible 订阅删除、迁移记录和审计；任何一行条件变化则整批回滚。
12. 生成补偿清单，记录原 subscription payload 与目标 candidateId；回滚动作必须再次预览和确认，不自动执行。
13. 不调用 Torra、qB、115、Symedia 或 Emby；数据整理完成后再由用户决定是否处理 ambiguous 行。
14. 追更页不显示候选；发现页从候选 API 展示榜单并提供“加入追更”。
15. 自动订阅设置文案改为“候选来源更新”，不再暗示自动建立追更。
16. `follow-previews` 只返回保存能力、身份复核、重复检查和 provider 预期；`follows` 要求确认和幂等键，并返回现有 activation 语义。
17. 迁移 preview 为只读 GET；执行 POST 仅接受 confirm、idempotencyKey 和 previewFingerprint；run 查询只返回脱敏摘要。

### 8.4 红线测试

- 榜单刷新后 subscriptions 数量和 Torra 调用次数不变。
- 同一候选重复刷新只更新 last_seen/version，不重复插入。
- 手动加入候选才创建订阅，并返回真实 activation。
- 人工 only_fluxa 永远分类为保留。
- 已关联 Torra 或已有 resource artifact 的 auto 行不迁候选。
- candidate-eligible 预览不写数据库。
- 指纹过期、幂等冲突、备份失败、并发变化均不产生部分迁移。
- migration-review 不自动删除。
- 迁移响应和活动记录不泄露原始路径与外部 ID。

### 8.5 验收与回滚

验证命令：

```powershell
python -m unittest tests.test_subscription_repository tests.test_discover_candidate_runtime tests.test_subscription_workbench_runtime tests.test_mcc_compat_runtime -v
npm run build
git diff --check
```

验收：自动来源刷新只改变候选池；追更和日历数量不增加；迁移仅在临时数据库演练。

回滚：代码可回滚到前一提交，候选表保留且不会影响旧读取；已经执行的真实迁移只能使用备份或经确认的补偿清单恢复。

## 9. P0 总关卡

P0.1–P0.5 全部完成后统一运行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
docker compose config --services
docker compose config --images
git diff --check
```

统一验收矩阵：

| 输入事实 | Torra | Symedia | STRM | Emby | 最终状态 |
| --- | --- | --- | --- | --- | --- |
| Torra completed | succeeded | unknown | unknown | unknown | evidence_insufficient |
| Symedia success | 任意 | succeeded | unknown | unknown | evidence_insufficient |
| STRM success | 任意 | succeeded | succeeded | unknown | evidence_insufficient |
| Emby episode verified | 任意 | 任意 | 任意 | succeeded | playable |
| 自动重试有效 | failed | 任意 | 任意 | unknown | in_progress |
| 低分保护 | 任意 | protected | 任意 | unknown | protected |
| 明确失败且无恢复 | 任意 | failed | 任意 | unknown | action_required |

P0 通过前不得执行真实历史污染迁移，也不得宣称产品已达到方案 B。

## 10. P1.1：Symedia 服务组、STRM 与洗版摘要

目标：把“历史接口可读”与各实际服务能力分开，并增加明确 STRM 事实。

涉及文件：

- `services/nasemby-core/app/symedia_read_runtime.py`
- `services/nasemby-core/app/pipeline_fact_runtime.py`
- `services/nasemby-core/app/task_chain_runtime.py`
- `services/nasemby-core/tests/test_symedia_read_runtime.py`
- `services/nasemby-core/tests/test_task_chain_runtime.py`
- `src/types/symedia.ts`
- `src/types/taskChain.ts`
- `src/components/pages/ControlRoom.tsx`
- `src/components/pages/TasksCenter.tsx`
- `src/styles/control-room.css`

步骤：

1. 先取得并保存脱敏 Symedia 服务响应夹具；没有验证过的正式接口时保持 unknown，不猜路径或字段。
2. 服务摘要分别返回 transferHistory、archiveMonitor、cloudDriveListener、webhook、strmGenerator、archiveScheduler 和fileObserver。
3. 只有专用 STRM 服务结果或可验证文件事实生成 strm verified。
4. 控制室把“在线”改为具体能力状态；“全部服务证据可用”要求所有声明能力都有当前证据。
5. 洗版摘要分别统计成功替换、低分保护、取消覆盖、真实失败和最近目标。
6. 任务中心 Symedia/STRM 失败提供打开原工具或查看对应任务的明确动作。

验证：服务部分可用、字段缺失、时间过期、洗版保护/失败分类、STRM 候选不升级为 verified、脱敏。

## 11. P1.2：Torra 秒传文件详情与恢复计数

目标：消费 Torra 已提供的运行结果，不再把“本次没有详情”写成永久限制。

涉及文件：

- `services/nasemby-core/app/torra_read_runtime.py`
- `services/nasemby-core/app/secupload_result_runtime.py`
- `services/nasemby-core/app/secupload_issue_runtime.py`
- `services/nasemby-core/app/pipeline_source_fact_runtime.py`
- `services/nasemby-core/app/task_public_runtime.py`
- `services/nasemby-core/tests/test_torra_read_runtime.py`
- `services/nasemby-core/tests/test_secupload_issue_runtime.py`
- `src/types/taskChain.ts`
- `src/components/pages/TasksCenter.tsx`
- `src/styles/tasks.css`

步骤：

1. 读取 `recent_runs.result` 和 `failure_details`，兼容字段缺失、数组/对象差异和旧 Torra 版本。
2. 仅公开脱敏文件显示名、错误分类、单文件重试次数和当前批次关联；路径和内部 ID 不公开。
3. 有详情时生成 file scope cloud115 facts；没有详情时保留 system-category。
4. `evidenceLimitText` 改为本次运行条件文案。
5. 自动恢复中的明确失败数进入 inProgressCount；活动 run 完成后重新派生。
6. 手动重试仍受自动计划、幂等、锁和写闸门约束。

验证：有/无 failure_details、混合成功失败、重复文件、重试完成、活动 run、路径和凭据脱敏。

状态：✅ 2026-07-28 已完成实现与本地验收。结构化计数优先于 message，`result/failure_details` 对象与数组均兼容；重复文件去重，重试次数缺失保持 `null`。文件级 115 事实只有完整路径键与 qB 精确一致时绑定媒体链，其余保留在系统问题。公开响应不含路径、错误原文或内部 ID。用户界面的 `evidence_insufficient` 显示名改为“暂未确认”，高级诊断深浅主题和 390px 视口通过。全量 509 项 Python 回归、TypeScript/生产构建、Compose 与差异检查通过。

## 12. P1.3：六阶段详情、历史日历与调度语义收口

目标：让任务详情、日历、首页归档和调度文案完全消费新事实契约，同时保留可审计历史事件，不扩张为第二套统一账本。

设计依据：

- `docs/superpowers/specs/2026-07-28-pipeline-facts-calendar-final-mile-design.md`

独立提交顺序：

1. **P1.3a 历史时间与范围所有权**
   - 在现有 `resource_events/resource_artifacts` 上增加 `eventAt` 历史投影和规范 `episode_range` owner。
   - qB、Symedia、STRM 使用上游真实发生时间；Emby 使用 `firstConfirmedPlayableAt`。
   - 成功和失败历史不因 `freshUntil` 删除；当前状态仍按新鲜度判断。
   - 验收：过期成功仍在日历，过期失败保留历史但不继续红色，范围 artifact 只有一个 owner。

2. **P1.3b 六阶段详情与确认数**
   - 普通任务详情固定读取 `pipelineFacts` 六阶段。
   - 移除普通页面百分比，改为“已确认 N/6 个阶段”。
   - `stages/steps/progress` 仅保留兼容和高级诊断。
   - 验收：Symedia 失败任务显示六阶段；115 和 STRM 不被下游成功反推。

3. **P1.3c 今日归档事件查询**
   - 增加 `archivedDate` 和 `archiveSummary`。
   - 按 Asia/Shanghai、正式成功事件和稳定文件身份去重。
   - 返回 `archivedFiles/linkedFiles/linkedTasks/unlinkedFiles`，解析 chain alias 后统计任务。
   - 验收：等式成立，首页深链口径一致，不复用 playable。

4. **P1.3d 日历集级桥接**
   - 按 TMDB、媒体类型、季号、合法集号范围和唯一 artifact owner 投影集级历史。
   - 显示 qB 完成、Symedia 入库、STRM 生成和 Emby 首次确认可播放时间。
   - 验收：刷新和等待后历史不消失；冲突、跨季和模糊标题保持未关联。

5. **P1.3e 调度与 Torra 推送语义**
   - 候选规则、服务端调度、最近错误、最近运行和逾期分别建模。
   - 候选正常要求规则启用、调度运行、无错误且最近运行未逾期。
   - Torra 推送拆为 queued/submitted/linked，只有可靠远端 ID 可进入 linked。
   - 验收：当前 fnOS 文案直接表达“规则已启用但调度未启动”和真实推送阶段。

6. **P1.3f STRM 正式接口检查与止损**
   - 只检查 Symedia 正式、只读、独立 STRM 结果。
   - 成功必须具备身份、季集范围、状态、generatedAt、结果 ID 和当前目标归属。
   - 接口不存在或证据不足立即保持 `unknown + missing + STRM_INDEPENDENT_RESULT_MISSING`。
   - 不读取 `.strm` 文件名，不从归档、路径或 Emby 反推；不得阻塞 P1.3a-e。

主要涉及文件：

- `services/nasemby-core/app/pipeline_fact_runtime.py`
- `services/nasemby-core/app/pipeline_source_fact_runtime.py`
- `services/nasemby-core/app/resource_identity_runtime.py`
- `services/nasemby-core/app/resource_task_repository.py`
- `services/nasemby-core/app/episode_evidence_runtime.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/app/calendar_timeline_runtime.py`
- `services/nasemby-core/app/home_summary_runtime.py`
- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/Overview.tsx`
- `src/components/pages/CalendarPage.tsx`
- `src/components/pages/DiscoverPage.tsx`
- `src/types/taskChain.ts`
- `src/types/homeSummary.ts`
- `src/types/subscriptions.ts`

总验收：当前实例三个 Symedia 失败任务显示六阶段；115 暂未确认；STRM 明确说明未提供独立结果；今日归档深链解释四项实时计数；明确集级历史长期保留；普通页面不再读取旧四段链路或百分比。

## 13. P1.4：异常动作与历史迁移复查

目标：让每个明确失败都有安全下一步，并完成历史污染数据的受控复查。

涉及文件：

- `services/nasemby-core/app/pipeline_outcome_runtime.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/app/automation_action_runtime.py`
- `services/nasemby-core/app/discover_candidate_runtime.py`
- `services/nasemby-core/tests/test_pipeline_outcome_runtime.py`
- `services/nasemby-core/tests/test_automation_action_runtime.py`
- `services/nasemby-core/tests/test_discover_candidate_runtime.py`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/Overview.tsx`

步骤：

1. qB 失败打开 qB 或提供现有暂停/恢复动作。
2. Torra/115 失败打开 Torra；只有正式预览/确认 API 存在时才提供重试。
3. Symedia/STRM 失败打开 Symedia，并保留脱敏原因详情。
4. Emby 证据刷新继续使用现有安全预检、确认和状态复查。
5. 首页问题卡携带 chainId/targetKey/outcomeState，精确定位对应任务。
6. 在真实数据上只运行历史迁移预览，核对 manual/downstream-owned/candidate-eligible/migration-review 四类。
7. 用户单独批准后才执行候选迁移；执行后复查订阅、候选、Torra links、资源链和日历数量。

验收：每个主操作唯一、可解释；没有正式安全动作时只打开原工具，不伪造自动修复能力。

## 14. P2.1：发现、追更与日历信息密度

涉及文件：

- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/CalendarPage.tsx`
- `src/styles/discover.css`
- `src/styles/calendar.css`
- `src/types/subscriptions.ts`
- `src/app/urlState.ts`

步骤：

1. 发现卡只保留海报、标题、年份、评分、两行简介和加入追更；完整剧情进入详情抽屉。
2. 追更默认筛选只保留搜索、追更中、需要处理、已可播放和仅缺集。
3. 年份、更新时间、来源和对账状态进入更多筛选；选项只来自实际数据，不生成 1900 起的空年份。
4. 日历默认隐藏 unlinked/migration_review，显式筛选才显示。
5. 手机端筛选使用现有底部面板，桌面保持紧凑工具栏。
6. 所有筛选写入 URL，详情返回恢复筛选、滚动位置和页码。

验证：320/390/768/1440、最长中文标题、空数据、2000 条追更分页、返回恢复、无页面级横向溢出。

## 15. P2.2：活动运行摘要与低价值信息清理

涉及文件：

- `services/nasemby-core/app/activity_log.py`
- `services/nasemby-core/app/activity_api_runtime.py`
- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/app/torra_subscription_sync_runtime.py`
- `services/nasemby-core/tests/test_activity_api_runtime.py`
- `src/types/operations.ts`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/Overview.tsx`
- `src/styles/tasks.css`

步骤：

1. 每次候选刷新、Torra 同步、RSS 扫描生成稳定 runId 和一条业务摘要。
2. 摘要返回 total/succeeded/skipped/failed、时间范围和可展开失败列表。
3. important 视图先按 runId 聚合；没有 runId 的旧 background 记录继续使用现有折叠规则。
4. error 明细保留，不与不同原因合并；普通视图默认只显示摘要。
5. raw 视图保持原始顺序和审计内容。
6. 移除任务首屏空闲 `0 B/s`、重复服务数字和不会改变用户决策的技术字段。

验证：203 条扫描/21 条未匹配显示一条摘要；后台同步 892 次不平铺；原始记录仍可查询；limit 在聚合后应用。

## 16. 契约、文档与弃用登记

每个阶段同步更新相关文档，最终至少覆盖：

- `docs/API_CONTRACT.md`
- `docs/contracts/http-api-contract-v2.json`
- `docs/CORE_API_CAPABILITY_MATRIX.md`
- `docs/FRAMEWORK.md`
- `docs/PRODUCT_DESIGN.md`
- `docs/URL_STATE.md`
- `docs/UI_STANDARD.md`
- `docs/ROADMAP.md`
- `src/DESIGN.md`
- `services/nasemby-core/DESIGN.md`

必须登记：

1. pipelineFacts、pipelineOutcome、outcomeState、outcomeCounts 和 playableAt。
2. 六类事实的成功语义、scope、证据等级和新鲜度。
3. 旧 state/userState/chainState/fulfillmentState/inLibrary/progressText/completedAt 的替代字段和弃用状态。
4. 候选列表、候选加入追更、迁移预览、迁移执行和补偿预览契约。
5. activity run summary、旧 URL 映射和新 URL 参数。
6. 公共字段脱敏边界、写动作守卫和真实迁移审批要求。

## 17. 全量验证与发布关卡

### 17.1 自动验证

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
docker compose config --services
docker compose config --images
git diff --check
```

同时确认：

- `docs/contracts/http-api-contract-v2.json` 可解析；
- GitHub Actions workflow 语法保持有效；
- 新表在空库和 schema version 4 数据库上均可幂等初始化；
- 临时数据库升级、备份、迁移、回滚演练通过；
- 自动测试日志不写入真实活动记录。

### 17.2 浏览器验收

- 320、390、768、1440 视口；
- 深色、浅色主题；
- 首页、任务中心、追更、日历、控制室、发现、全局搜索；
- 键盘、焦点、筛选、URL 分享、浏览器返回、详情关闭和滚动恢复；
- 文本不重叠、按钮不溢出、卡片不因状态变化跳动；
- 网络响应和 DOM 不出现路径、Token、Cookie、Passkey 或原始外部 ID。

### 17.3 发布与实机

1. 所有代码和文档提交后推送 `main`。
2. 等待 GitHub Actions validate、双架构构建和镜像冒烟全部成功。
3. 确认 `latest` digest 等于本提交不可变镜像 digest。
4. fnOS pull/recreate 后确认 `/healthz.revision` 等于发布提交 SHA。
5. 先执行只读实机核对：Torra completed、Symedia 入库、STRM、Emby 集级、秒传恢复、追更和日历。
6. 候选迁移只运行 preview；真实执行仍需用户单独批准。
7. 不在发布流程中自动开启任何外部写开关。

## 18. 观察期与方案 B 退出关卡

P0–P2 发布后开启至少 7 个连续自然日影子观察。观察记录只保存聚合差异：

- legacy completed / new not playable；
- legacy 0/N / new evidence_insufficient；
- legacy Torra 运行中 / new target satisfied；
- legacy action_required / new in_progress；
- 完全一致。

退出条件：

1. 至少一条真实电影完成 Emby 可播放闭环。
2. 至少一条真实电视剧集完成集级 Emby 可播放闭环。
3. 连续 7 天没有无法解释的新旧状态差异。
4. 所有前端消费者和服务端统计不再读取旧字段。
5. 代码搜索确认没有业务流程写旧状态。
6. 历史污染迁移没有未处理的高风险冲突。
7. 回滚、备份和部署文档已验证。

满足后另开最终弃用提交：

1. 新契约版本删除旧字段和旧查询参数。
2. 删除 legacy projector、影子比较和旧前端类型。
3. 清理仅服务旧字段的测试和文档。
4. 再次运行全量回归、浏览器验收和多架构发布。

未满足退出条件时继续保留方案 A 的兼容层，不提前宣称达到方案 B。

## 19. 文件所有权建议

如果后续明确采用多代理并行，按以下边界分配；未明确要求多代理时按提交顺序串行执行：

- 任务事实代理：`pipeline_*`、`task_chain_*`、`resource_task_repository.py` 及对应测试。
- 订阅数据代理：`subscription_*`、`discover_candidate_runtime.py`、`discover_runtime.py` 及候选/迁移测试。
- 证据接入代理：Torra、qB、Symedia、Emby 读取层及对应测试。
- 前端代理：任务、首页、追更、日历、控制室、活动和样式。
- 主执行者：契约、跨模块集成、数据库迁移审查、浏览器验收、发布和实机核对。

同一文件同一时间只允许一个执行者修改；跨边界变更先由主执行者确认。
