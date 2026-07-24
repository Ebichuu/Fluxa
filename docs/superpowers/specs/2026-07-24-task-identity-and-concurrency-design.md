# 任务身份串链与并发下载摘要设计

## 目标

降低 qB 任务大量未关联造成的“证据不足”，并让任务中心列表直接显示同一目标的并发下载数量。

## 身份裁决规则

候选必须先全部收集，再统一裁决：

1. 可靠文件/Hash 与 Torra 文件证据精确一致时，使用 `artifact_exact`；
2. 有 TMDB ID 时使用 TMDB 精确匹配，并校验媒体类型和季号；
3. 无 TMDB 的电视剧使用保守标题、媒体类型和 qB 季号匹配；Torra 季号为 0/未知时，只在候选唯一的情况下视为全季目标；
4. qB 标题开头方括号/中文括号内的中文名、标题开头连续中文名都作为独立候选；不启用普通模糊包含匹配；
5. 多个候选继续返回 `conflict`，无可靠候选继续返回 `unlinked`。

所有决策保留 `ownerTargetKey`、`matchMethod`、`confidence`、`conflictCandidates` 和 `observedAt`。

## 任务列表字段

v2 列表摘要新增并保持向后兼容：

- `activeDownloadTasks`：该目标当前活跃 qB 任务数；
- `completedDownloadTasks`：该目标已完成 qB 任务数；
- `concurrentDownloadCount`：用于列表提示的并发数量，等于活跃 qB 任务数。

前端优先使用 `concurrentDownloadCount`，缺失时回退到 `activeDownloadTasks`。

## 明确不做

- 不把 qB 完成且缺少 Torra 秒传文件证据推断为“正在秒传”或“已进入 115”；
- 不执行 Torra、qB、115、Symedia、Emby 写操作；
- 不改变远端订阅，不自动回填 RSS 全库。

## 验收

- Torra 季号未知但标题和类型唯一的电视剧 qB 任务可关联到全季目标；
- 中英混合、方括号中文标题仍按候选裁决，不跨作品串链；
- 多目标候选仍保持冲突，不抢占其他目标；
- 任务列表摘要包含并发字段，现有客户端继续兼容；
- 后端回归与前端构建通过。
