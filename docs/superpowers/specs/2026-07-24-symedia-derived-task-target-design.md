# Symedia 派生任务目标与无订阅串链设计

## 背景

当前任务目标只从 Fluxa 本地追更和 Torra 远端订阅生成。没有订阅锚点的 qB 任务与 Symedia 入库记录即使属于同一作品，也会分别生成下载孤立链和入库孤立链。

典型结果是：

- qB 链显示下载完成，但 115 与入库证据未知；
- Symedia 链显示已进入下游并成功入库，但没有上游下载；
- 两条链无法共享 Symedia 的明确 TMDB 身份，也无法利用该身份查询 Emby 作品级证据；
- 首页继续汇总大量“任务身份尚未完成关联”。

本阶段允许 Symedia 的明确媒体身份在没有订阅时建立任务目标，再让 qB 通过保守标题和季号裁决归属。它不改变 Fluxa、Torra、qB、115、Symedia 或 Emby 的事实源职责。

## 目标

1. 没有 Fluxa/Torra 订阅时，具有明确 TMDB、电视剧类型和季号的 Symedia 记录可以建立稳定媒体目标。
2. qB 任务只在保守中文标题、电视剧类型、季号和候选唯一时归属该目标。
3. 同一目标的 qB Hash、Symedia 记录和 Emby 作品级证据合并为一条顶层任务链。
4. 多候选、缺季号、缺可靠标题或身份矛盾时保持冲突或未关联，不扩大模糊匹配。
5. 保持逐文件秒传和 Emby 单集证据的真实能力边界。

## 不在本阶段处理

- 不自动处理 RSS 历史身份积压；
- 不修复《金特务：本色回归》等资源来源不可用或无结果问题；
- 不根据 qB 完成时间或 Symedia 缺失推断单文件秒传状态；
- 不把 Emby 作品级 TMDB 命中描述成具体单集已索引；
- 不创建、删除或修改 Torra 订阅，不暂停或删除 qB 任务；
- 不为电影启用缺年份的标题回退。电影继续使用现有 TMDB 或标题加年份规则。

## 事实源与身份

- qB：下载任务、Hash、文件名、季集和进度事实；
- Symedia：媒体 TMDB、季集、源文件、归档结果和正常保护事实；
- Emby：作品是否已被媒体库收录的事实；
- Fluxa：证据所有权裁决和任务链聚合结果。

稳定身份继续使用：

- `mediaKey = tv:tmdb:{tmdbId}`；
- `targetKey = tv:tmdb:{tmdbId}:season:{seasonNumber}`；
- `chainId` 由 `mediaKey + targetKey` 生成；
- qB Hash 和 Symedia 记录 ID 只作为 `artifactKey/sourceIds`，不参与顶层链身份。

## Symedia 派生目标

### 建立条件

一条 Symedia 记录只有同时满足以下条件才可以建立派生目标：

1. `tmdbid` 是非空、有效的正整数；
2. 媒体类型明确为电视剧；
3. 季号大于 0；
4. `title` 可以产生非空规范标题键。

不满足条件的记录继续使用现有孤立证据逻辑。路径、文件名或错误文本不能替代缺失的 TMDB 身份。

### 合并规则

- 相同 TMDB、相同季号的多条 Symedia 记录合并到同一个派生目标；
- 如果 Fluxa/Torra 已经存在相同 `targetKey`，派生证据直接进入已有目标，不创建第二个目标；
- 派生目标只存在于本次任务快照和任务台账，不创建 Fluxa 追更意图，也不创建 Torra 镜像；
- Symedia 自身证据使用 `matchMethod=symedia_tmdb_anchor`、`confidence=strong`。

## qB 候选裁决

qB 证据先收集全部候选，再统一裁决，不在遍历过程中抢占目标。

候选必须同时满足：

1. qB 标题能够解析出电视剧季号；
2. qB 季号与派生目标季号一致；
3. qB 方括号/中文括号中文名或标题开头连续中文名，与 Symedia `title` 的规范标题键完全一致；
4. 匹配后只有一个不同 `targetKey`。

裁决结果：

- 唯一候选：`matchMethod=symedia_title_season_unique`、`confidence=fallback`；
- 多个不同 TMDB 目标满足：`confidence=conflict`，记录全部 `conflictCandidates`，不归属；
- 没有可靠候选：`confidence=unlinked`；
- 一条 qB/Symedia 证据最多只有一个 `ownerTargetKey`。

不使用标题包含、编辑距离、拼音、路径相似度或英文名模糊匹配。

## 无订阅任务链

每个有 qB 或 Symedia 证据的派生目标生成一条统一任务链：

- `subscriptionId`、`torraId` 保持为空；
- `sourceIds.qbHashes` 合并全部相关 qB Hash；
- `sourceIds.symediaIds` 合并全部相关 Symedia 记录；
- `origin/origins` 表示下载和入库证据来源，不伪装成追更订阅；
- 任务来源阶段显示“未发现 Fluxa/Torra 追更订阅；当前按 qB 与 Symedia 媒体证据跟踪”；
- 下载阶段读取真实 qB 状态；
- 115 阶段有 Symedia 源文件时只说明“下游已收到源文件，具体上传方式未确认”；
- 入库阶段读取真实 Symedia 成功、保护或失败结果；
- Emby 使用派生目标 TMDB 查询作品级收录状态。

无订阅不等于身份未识别。只要派生媒体身份和 qB 归属裁决成立，任务返回 `identityState=linked`。是否存在追更订阅作为来源信息单独展示，不覆盖执行健康状态。

## Emby 证据边界

- Emby TMDB 作品索引命中时返回 `embyIndexed=true`、`embyEvidenceScope=title`；
- 页面文案为“Emby 已收录该作品”；
- 只有未来接入明确季集索引接口后，才允许返回 `embyEvidenceScope=episode`；
- Symedia 已入库但 Emby 作品索引未命中时，保持“已入库，尚无 Emby 收录证据”，不能改成红色故障。

## 秒传证据边界

本阶段不会因为 qB 与 Symedia 已合并，就反向声称某个文件使用 115 秒传成功。现有 Torra 接口只有批次级结果：

- Symedia 源文件只证明文件进入下游；
- 单文件等待、失败次数、重试和原始上传状态继续不可见；
- 任务详情继续明确显示“具体上传方式未确认”或“暂不支持逐文件确认”。

## 接口兼容

现有任务 URL 和必填字段保持不变。新增行为只影响聚合结果和可选证据字段：

- 顶层不再同时返回同一目标的 qB 孤立链和 Symedia 孤立链；
- `sourceIds` 同时包含 qB 与 Symedia 身份；
- `evidenceOwnership` 增加新的 `matchMethod`；
- v2 摘要、分页、ETag 和详情读取继续使用现有契约；
- 没有满足派生条件的数据时，响应与当前行为一致。

## 测试与验收

### 自动化回归

1. 无订阅、Symedia 有明确 TMDB/季号、qB 有方括号中文名和同季号时，只生成一条任务链；
2. 统一链同时包含 qB Hash、Symedia ID、下载证据和入库证据；
3. Emby 存在相同 TMDB 时只生成作品级收录证据；
4. 相同标题和季号对应多个不同 TMDB 时，qB 保持冲突；
5. qB 缺季号、Symedia 缺 TMDB、缺季号或缺标题时不建立错误归属；
6. 已有 Fluxa/Torra 同 `targetKey` 时不生成重复派生链；
7. 电影缺年份仍不参与标题回退；
8. 没有逐文件秒传证据时，统一链仍不显示虚假的单文件秒传状态；
9. 旧任务列表、摘要、详情和条件请求继续通过。

### 实机只读验收

- 《灿如繁星》《雀骨》《野狗骨头》如果满足明确 Symedia TMDB、同季号和唯一中文标题候选，应各自从两条链收敛为一条；
- 相应任务不再计入“身份未关联”，但没有 Emby 命中时仍显示缺少最终收录证据；
- 首页未关联系统提示数量下降，不产生新的红色异常；
- 不执行 RSS 回填、下载、秒传、归档、删除、订阅导入或其他外部写操作。

## 发布边界

本阶段可以独立发布和回滚。发布流程继续由 GitHub Actions 构建不可变镜像、执行容器冒烟，再将验证通过的 digest 提升为 `latest`。
