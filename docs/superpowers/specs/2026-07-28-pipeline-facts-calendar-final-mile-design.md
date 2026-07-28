# Fluxa 六阶段事实、历史日历与调度语义收口设计

日期：2026-07-28
状态：已确认，待实施
范围：任务详情、历史事件、集级证据所有权、今日归档、追更调度文案、STRM 独立证据和日历投影

## 1. 决策

本轮继续采用方案 A：以 `pipelineFacts` 为唯一普通页面事实源，在现有资源事件台账上补历史事件投影和集级桥接。若 Symedia 没有正式、只读、独立的 STRM 结果接口，则自动按方案 B 收口为未知，不阻塞其余工作。

不采用以下方案：

- 不从 `.strm` 文件名、归档成功、文件路径或 Emby 已收录反推 STRM 成功。
- 不继续让普通页面读取旧 `stages/steps`。
- 不重建第二套统一事件账本。
- 不用标题模糊匹配生成集级事实或归档所有权。

## 2. 本波次完成定义

1. 任务详情固定展示 `torra/qb/cloud115/symedia/strm/emby` 六阶段，只读取 `pipelineFacts`。
2. 普通页面不再显示旧进度百分比，改为“已确认 N/6 个阶段”。
3. qB 完成、Symedia 归档、STRM 生成和 Emby 首次集级命中形成永久历史事件，不因 `freshUntil` 过期从日历消失。
4. 集级证据拥有唯一规范范围 owner；日历只投影范围，不复制 artifact 所有权。
5. 首页“今日归档”进入 `archivedDate`，返回并解释 `archivedFiles/linkedFiles/linkedTasks/unlinkedFiles`。
6. 候选更新、服务端调度和 Torra 推送使用各自真实状态，不再合并为“自动获取”。
7. STRM 只有满足正式独立证据契约时才成功；否则固定显示“Symedia 未提供独立结果”。

## 3. 时间模型

### 3.1 发生时间与观察时间

历史事件必须同时区分：

- `eventAt`：上游声明的真实发生时间。
- `observedAt`：Fluxa 读取、接收或确认该证据的时间。
- `freshUntil`：仅用于判断当前状态是否仍然有效，不决定历史事件是否存在。

`eventAt` 按以下优先级生成：

| 阶段 | 首选发生时间 | 回退 |
| --- | --- | --- |
| qB | `completedAt` / `completion_on` | `observedAt` |
| Symedia | 正式结果时间 | `observedAt` |
| STRM | `generatedAt` | `observedAt` |
| Emby | Fluxa 首次明确集级命中的检查时间 | 不回推媒体实际可播放时间 |

Emby 的公开字段和文案固定为：

- `firstConfirmedPlayableAt`
- “首次确认可播放”

不得宣称该时间是媒体实际首次可播放时间。

### 3.2 历史事件与当前状态

以下成功事件是不可因新鲜度过期删除的历史事实：

- qB `succeeded`
- Symedia `succeeded`
- STRM `succeeded`
- Emby 集级 `succeeded` 的首次明确命中

当前在线、活动、等待、失败和“当前是否仍在 Emby”继续使用 `freshUntil`。

失败过期后：

- 不再自动进入当前红色 `action_required`。
- 历史事件继续保留并显示“曾于 xx 失败 · 当前状态暂未确认”。
- 只有同 artifact、同阶段后续出现明确 `succeeded` 或正式 `recovered` 事件，才标记已恢复。
- 单纯时间流逝、服务重启或下游成功不能标记恢复。

### 3.3 台账复用

复用现有 `resource_events` 和 `resource_artifacts`：

- 为历史事件增加兼容的 `eventAt` 投影；旧事件缺少该字段时回退 `observed_at`。
- 当前快照仍保存在 `resource_chains`，历史时间线从 `resource_events` 读取。
- 不新增第二套事件表，也不从旧 `stages/steps` 重建历史。

## 4. 六阶段任务详情

### 4.1 固定顺序

普通任务详情按以下固定顺序展示：

```text
Torra -> qB -> 115 -> Symedia -> STRM -> Emby
```

主链只消费 `pipelineFacts`。`stages/steps` 只允许出现在高级诊断的“旧兼容投影”区域，不得参与普通状态、当前步骤、进度或操作建议。

旧服务没有返回 `pipelineFacts` 时显示“六阶段事实尚未返回”，禁止回退旧四段链路。

### 4.2 确认数

普通卡片和详情移除百分比，改为：

```text
已确认 N/6 个阶段
```

“已确认”表示阶段存在唯一、非冲突、非 missing 的明确事实。以下状态都计入已确认：

- `waiting`
- `active`
- `succeeded`
- `failed`
- `protected`
- `not_applicable`

`unknown + missing` 不计入。确认数不表示线性完成度，也不能影响整体 `pipelineOutcome`。

旧 `progress` 字段仅作为兼容响应保留，普通页面不得读取。

### 4.3 115 与 STRM 文案

没有可唯一归属的秒传文件级证据时固定显示：

```text
115 · 暂未确认 · Torra 未提供可绑定当前目标的文件结果
```

不得因 Symedia 成功把 115 投影为完成。

没有正式独立 STRM 结果时固定显示：

```text
STRM · 暂未确认 · Symedia 未提供独立结果
```

## 5. 集级证据唯一所有权

### 5.1 规范范围 owner

单集 owner 保持：

```text
tv:tmdb:123:season:1:episode:2
```

范围 artifact 使用规范范围 owner：

```text
tv:tmdb:123:season:1:episodes:2-3
```

并在结构化记录中保存：

```json
{
  "ownerScope": "episode_range",
  "seasonNumber": 1,
  "episodeStart": 2,
  "episodeEnd": 3
}
```

E02-E03 不得把 owner 设成单集 E02 或 E03。日历可以从一个范围 owner 投影两个条目，但底层仍只有一个 artifact owner。

### 5.2 关联条件

集级桥接必须同时满足：

1. TMDB 身份精确一致。
2. 媒体类型一致。
3. 季号明确且一致。
4. 集号位于合法范围。
5. artifact 在 `resource_artifacts` 中只有一个规范 owner。
6. 永久链别名解析后仍指向同一规范任务。

以下情况保持“证据未关联”：

- 同一 artifact 被两个目标声明。
- 范围跨季。
- `episodeStart > episodeEnd`。
- absolute 与 season/episode 编号无法可靠转换。
- 所有权冲突或仅标题相似。

## 6. 日历历史投影

日历先按 TMDB、媒体类型、季号匹配规范任务，再按单集或规范范围 owner 投影对应 episode。

每个日历条目分开返回：

- qB 完成历史时间。
- Symedia 入库历史时间。
- STRM 生成历史时间（若有正式证据）。
- Emby `firstConfirmedPlayableAt`。
- 当前状态及其新鲜度。

历史事件即使过期仍显示；当前状态过期时只把当前结论改为“暂未确认”。刷新或等待不得清除已经确认的历史时间。

季包只有明确内部文件范围时才能投影集级记录；仅有季包标题不能生成全季集级证据。

## 7. STRM 调研止损点

本波次只检查 Symedia 是否存在正式、只读、独立的 STRM 结果接口。只有单条结果同时具备以下字段才生成 `strm succeeded`：

1. 明确媒体身份。
2. 明确季集或合法范围。
3. 明确 STRM 生成成功状态。
4. `generatedAt`。
5. 可追溯且稳定的结果 ID。
6. 结果归属当前规范目标，不依赖标题模糊匹配。

接口不存在、不可只读访问或任一证据缺失时立即止损：

- `state=unknown`
- `evidence=missing`
- `reasonCode=STRM_INDEPENDENT_RESULT_MISSING`
- `reasonText=Symedia 未提供独立结果`

禁止读取 `.strm` 文件名，禁止从归档、路径或 Emby 反推。STRM 调研不得阻塞任务详情、调度文案、今日归档和日历集级桥接。

## 8. 今日归档

### 8.1 成功事件筛选与去重

日期统一按 `Asia/Shanghai` 切分。只统计明确 Symedia `succeeded/archived` 历史事件，排除：

- 低分保护。
- 取消覆盖。
- 真实失败。
- 状态未知。
- 同一文件的重复扫描。

去重键优先级：

1. Symedia 正式结果 ID。
2. 内部规范文件身份的不可逆哈希。
3. 两者都不存在时不进入明确归档计数。

### 8.2 `archivedDate` 响应

`GET /api/v2/tasks/chains?archivedDate=YYYY-MM-DD` 增加可选归档摘要：

```json
{
  "archiveSummary": {
    "date": "2026-07-28",
    "timezone": "Asia/Shanghai",
    "archivedFiles": 35,
    "linkedFiles": 30,
    "linkedTasks": 18,
    "unlinkedFiles": 5
  }
}
```

必须满足：

```text
archivedFiles = linkedFiles + unlinkedFiles
```

- `linkedFiles` 是拥有唯一规范 owner 的去重文件数。
- `linkedTasks` 先解析永久 chain alias，再按 canonical chain ID 去重。
- `unlinkedFiles` 是成功归档但没有唯一规范 owner 的文件数。
- 不得假定当前 35 个文件对应 18 个已可播放作品，所有数字必须实时重算。

首页“今日归档”跳转到 `archivedDate`，任务中心同时解释文件数、关联任务数和未关联数，不复用 `playable/completedDate`。

## 9. 候选调度与 Torra 推送文案

### 9.1 候选自动更新

候选能力分别保存：规则启用、服务端调度运行、最近错误、最近成功时间和计划时间。

“候选自动更新正常”必须同时满足：

1. 候选规则启用。
2. 服务端调度正在运行。
3. 没有最近错误。
4. 最近一次应执行的计划没有逾期。

每日计划使用 `Asia/Shanghai`：

- 计算最近一个应执行时间。
- 在应执行时间后的 2 小时宽限内，允许等待本次运行完成。
- 超过宽限仍没有本次成功记录即为逾期。
- 服务首次启动且尚无运行记录时显示“调度已启动 · 等待首次运行”，不能显示正常。

普通文案固定为：

- `候选规则已启用 · 服务端调度未启动`
- `候选自动更新正常 · 最近运行 xx`
- `候选规则未启用`
- `候选调度异常 · 最近运行失败`
- `候选调度逾期 · 最近成功 xx`

### 9.2 Torra 推送三态

追更保存与 Torra 推送分开表达：

| 状态 | 判据 | 文案 |
| --- | --- | --- |
| queued | 本地追更已保存，但推送尚未被 Torra API 接受 | 追更已保存 · 等待推送 Torra |
| submitted | Torra API 已接受请求，但尚未从可靠只读对账中取得远端 ID | 已提交 Torra · 等待确认 |
| linked | 只读对账读取到可靠远端 ID，且身份/季范围一致 | 追更已保存 · 已在 Torra |

推送关闭时显示：

```text
追更已保存 · Torra 自动推送已关闭
```

不得仅根据 HTTP 2xx 或本地任务存在显示“已在 Torra”。

## 10. API 与兼容

本轮只增加可选字段和查询参数：

- `eventAt`
- `firstConfirmedPlayableAt`
- `confirmedStageCount`
- 范围 owner 字段
- `archivedDate`
- `archiveSummary`
- 候选调度细分状态
- Torra 推送三态

旧 `progress/stages/steps/completedDate` 保持兼容，不改变类型和现有状态码。新普通页面不再读取 `progress/stages/steps`；高级诊断可继续显示旧兼容投影。

`archivedDate` 非法日期返回 `400` 和稳定错误码；归档数据源暂不可用返回明确 `502`，不能包装为 `200 + 0`。

## 11. 错误处理与安全边界

- 上游时间非法时保留原始事件为未确认，不使用当前时间伪装真实发生时间。
- 公开响应不返回路径、Token、Cookie、Passkey、原始外部 ID 或未脱敏错误正文。
- 规范文件哈希只用于内部去重，不公开输入和原始摘要。
- 所有权冲突不得自动改绑；沿用资源台账的条件迁移、别名和冲突记录。
- 本波次只读检查 Symedia STRM 能力，不新增外部写动作，也不自动开启调度或 Torra 推送。

## 12. 测试与验收

### 12.1 红线测试

1. 成功事件 `freshUntil` 过期后，日历历史时间仍存在。
2. 失败过期后不再是当前红色，但保留“曾于 xx 失败”。
3. 没有后续成功/恢复事件时不得标记已恢复。
4. E02-E03 只有一个范围 owner，日历投影两集。
5. 跨季、范围冲突和多 owner 均保持未关联。
6. 归档去重排除保护、取消、失败和重复扫描。
7. `archivedFiles = linkedFiles + unlinkedFiles`。
8. `linkedTasks` 在 alias 解析后去重。
9. 普通页面源码不读取 `stages/steps/progress`。
10. Symedia 成功不能把 115 或 STRM 变成成功。
11. 候选自动更新正常必须同时满足四项条件。
12. Torra API 接受但未对账只能是 submitted，不能是 linked。

### 12.2 当前实例验收

- 三个 Symedia 失败任务展开后固定显示六阶段。
- 没有秒传文件级证据时，115 显示“暂未确认”。
- STRM 显示“Symedia 未提供独立结果”。
- 首页点击实时“今日归档”进入 `archivedDate`，页面解释四项归档计数。
- 日历显示明确集级入库和首次确认可播放历史，刷新和等待后不消失。
- 任务卡显示“已确认 N/6 个阶段”，不显示百分比。
- 普通页面不读取旧 `stages/steps`；旧字段只在高级诊断出现。
- 候选和 Torra 推送文案与当前 fnOS 真实状态一致。

### 12.3 自动验证

- Python 全量回归和新增定向测试。
- v2 机器契约、旧客户端兼容和非法参数测试。
- TypeScript 类型检查与生产构建。
- Compose 解析、差异检查、安全与质量关卡。
- 桌面/390px、深浅主题、任务展开、首页深链、日历刷新和 URL 恢复浏览器验收。

## 13. 非目标

- 不保证一次消除全部历史未关联日历记录；只能关联满足强证据和唯一所有权的记录。
- 不把作品级 Emby 命中升级为电视剧集级可播放。
- 不推测媒体实际首次可播放时间。
- 不重做下载、归档、STRM 或 Emby 引擎。
- 不执行历史候选迁移或任何外部写操作。
