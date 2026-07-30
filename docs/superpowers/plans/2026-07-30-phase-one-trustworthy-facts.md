# 第一阶段：数字可信实施计划

对应上位规格：

- `docs/superpowers/specs/2026-07-30-trustworthy-media-control-center-roadmap-design.md`
- `docs/superpowers/specs/2026-07-30-rss-subscription-quality-rewash-design.md`

实施边界：只修改 Fluxa；不重建事件台账；不改变 Torra、qB、115、Symedia、STRM、Emby 的事实归属；现有接口只做可选增量；始终排除 `services/nasemby-core/mcc_data.db`。

当前进度：P0.1 已提交（`9a2a018`）；P0.2 已提交（`461d1ef`）；P0.3 已提交（`76e14d6`）；P0.4 已提交（`d6533ab`）。数字可信 P0 波次已完成，当前衔接 RSS 总计划阶段 A。

## P0.1 日历统一去重与来源合并

状态：已完成并提交（`9a2a018`）。

### 后端

- 在 `calendar_timeline_runtime.py` 增加全局日历归并步骤。
- 可靠去重键固定为：`日期 + 媒体类型 + TMDB ID + 季号 + 集号`。
- 电影使用季号和集号 `0`；电视剧必须有明确季号与正集号。
- 缺少 TMDB、媒体类型、日期或电视剧明确集号时不自动合并。
- 本地追更、Torra-only 和重复任务日历条目统一进入同一归并器，不再只在追加 Torra 条目时做一次性排除。
- 归并后保留一个主条目，同时增加可选来源摘要：`sourceLabels`、`sourceKeys`、`sourceOrigins`、`sourceCount`。
- 主条目优先级固定为：人工本地追更、其他本地追更、Torra 只读追更；同级按信息完整度和稳定键决定。
- 布尔事实采用保守合并：`torraLinked/followScopeExplicit/includePastEpisodes/inLibrary` 任一可靠来源为真即为真；`migrationReview` 只有全部来源都要求复核时才为真。
- 时间字段中，订阅时间采用最早明确值；展示标题、海报和导航键优先取主条目；路径与来源数组去重。
- 汇总、搜索索引、详情数量和缓存版本全部基于归并后的条目。

### 前端

- 日历详情在存在多个来源时显示“来源：A、B”，单来源保持现有文案。
- React key 使用规范日期/身份/季集键，避免重复来源造成重复 key。

### 验收

- 同一天同 TMDB 同季同集的两个本地条目只返回一条。
- 本地条目与 Torra-only 同集只返回一条，`sourceCount=2` 且来源完整。
- 缺 TMDB、跨季、不同集和范围不明记录不合并。
- `entries / linkedEntries / unlinkedEntries / totalEntries / statusCounts / days.total / searchIndex` 全部按归并结果计算。
- 来源顺序变化不改变主条目和版本摘要。

## P0.2 首页与任务中心共享问题组

状态：已完成并提交（`461d1ef`）。

### 后端

- 新建无副作用的问题组派生器，输入全部公开任务链，输出问题组及资源成员。
- 可靠组键：`媒体类型 + TMDB ID + 季号 + 阶段 + 原因码`。
- 无可靠身份时只做机械展示分组；标题、媒体类型或季号冲突时逐资源成组。
- 首页 `actionRequiredGroups`、问题列表和任务中心问题组必须消费同一结果。
- 新增可选 `problemGroups` 与 `problemGroupSummary`；旧计数字段保持原值和语义。

### 前端

- 任务中心“需要处理”默认按问题组展示。
- 展开后显示涉及的集号范围、文件/任务数量和独立资源入口。
- 技术资源仍只在展开区和高级诊断展示。

### 验收

- 同作品同季同原因的 10 个分集显示为 1 个问题组、10 个资源。
- 首页组数、资源数与任务中心完全对账。
- 首页前 8 条问题上限不影响完整问题组统计。

## P0.3 统计范围元数据

状态：已完成并通过全量验收，进入独立提交。

### 后端

- 为首页、任务、追更和日历增加可选统计元数据，不改旧数值字段。
- 每项元数据至少包含：`scope`、`unit`、`observedAt`、`confirmation`。
- `confirmation` 固定为 `confirmed | partial | unknown`。
- 任务中心已可播放范围固定为“当前唯一任务链”；追更页已可播放范围固定为“当前追更台账”。
- 请求失败和首次加载继续显示未知，真实零值仍显示零。

### 前端

- 统计卡片或摘要显示范围标签；必要时提供简短说明。
- 不要求不同范围的数字相等。

### 验收

- “任务中心已可播放 18”和“追更已可播放 0”同时出现时，各自明确说明范围。
- 过期或缺失证据显示未知/部分确认，不伪造零。

## P0.4 媒体最终结果与残留问题分离

状态：已完成并提交（`d6533ab`）。

### 后端

- 从现有六阶段事实派生独立 `mediaResult`，回答已获取、已入库、已生成 STRM、已可播放等最终媒体结果。
- 继续使用 `pipelineOutcome` 表达兼容主结果，不改变旧消费者。
- 新增可选 `residualIssues`，只表示不会推翻已确认下游结果的遗留下载、重复任务或清理问题。
- 下游已确认时，上游晚到或残留失败不能把 `mediaResult` 降级；真实残留问题仍保留为需要处理。

### 前端

- 卡片主结论优先显示媒体结果，例如“已入库”。
- 残留问题作为次级提示，例如“另有 1 个残留下载需处理”。
- 只有影响目标最终结果的失败才显示整条媒体任务异常。

### 验收

- Symedia 已归档且 qB 残留 stalled：主结论为“已入库”，次级问题为残留下载。
- Emby 已精确可播放时，任何上游残留问题都不能取消可播放统计。
- 没有下游成功事实时，qB/Symedia 真实失败仍正常进入需要处理。

## 后续衔接：RSS 阶段 A 订阅绑定与规则影子评分

状态：已完成实现，进入全量验证与独立提交。

- 自动 RSS 匹配改为本地影子评分，不再触发 Torra 整订阅搜索。
- 只读 `/api/v1/meta_weight/rules`，保存规则哈希与只读快照，不在 Fluxa 编辑第二套规则。
- 候选必须绑定可靠 Torra 订阅、TMDB 媒体身份和明确季集；缺少条件时为“暂未确认”。
- 一个 RSS 指纹对应一个 `artifactKey`；范围包可投影多个单集，但只评分一次，所有权冲突整体阻断。
- 观察单元创建和基线确认都会按首次下载真实时间回扫候选，重复回扫保持幂等。
- 种子库展示候选分数与基线状态，明确“只读评估，不会自动下载”；人工 Torra 整订阅分析保留为兜底。
- 阶段 B 继续负责可靠当前版本基线、跨批次冠军和真实升级决策，本阶段不自动下发。

## 提交与发布顺序

1. `fix(calendar): merge duplicate episode sources`
2. `feat(tasks): expose shared problem groups`
3. `feat(metrics): describe statistic scopes`
4. `fix(tasks): separate media results from residual issues`
5. 文档与机器契约同步提交（可随对应阶段合并）。

每个阶段都必须通过相关单测、后端全量回归、前端生产构建和 `git diff --check`。第一阶段全部完成后统一推送 `main`，等待源码测试、双架构镜像、amd64/arm64 冒烟和 `latest` 提升成功。
