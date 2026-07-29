# 问题组统计与 qB 观察窗设计

## 目标

收口首页“作品数”误导、qB 短暂 `stalled` 闪烁和冷启动零值三个问题，同时保持任务身份、历史事件和旧接口消费者不变。

## 问题组统计

首页新增可选计数字段：

- `actionRequiredGroups`：全部“需要处理”资源形成的界面问题组数量。
- `actionRequiredIdentityUnconfirmedResources`：问题资源中缺少可靠媒体身份的资源数量。

旧 `actionRequiredWorks`、`actionRequiredResources`、`actionRequired` 和 `mediaActionRequired` 保持原语义与数值。普通首页改用 `actionRequiredGroups`，文案固定为：

```text
X 个问题组 · 涉及 Y 个资源 · 其中 Z 条身份未确认
```

问题组必须从全部 `healthState=action_required` 的任务资源计算，不依赖公开响应中最多八条 `issues`。

### 可靠身份

只有同时满足以下条件才使用媒体身份分组：

1. `identityState=linked`；
2. TMDB ID 是有效正整数；
3. 媒体类型明确为 `movie` 或 `tv`；
4. 电视剧具有唯一、有效的正整数季号。

电影按 `movie + tmdbId` 分组，电视剧按 `tv + tmdbId + seasonNumber` 分组。有 TMDB ID 但身份未关联或冲突时仍属于身份未确认资源。

### 展示分组

缺少可靠身份时，只为首页计数生成展示分组键：

- 使用明确媒体类型、机械规范化标题和唯一季号；
- 标题规范化只处理大小写、空格、全半角和常规标点；
- 禁止使用别名、翻译名映射、编辑距离或其他模糊相似度；
- 无标题、媒体类型不明确、季数缺失或 `identityState=conflict` 时逐资源计算；冲突状态不得用标题分组掩盖媒体类型或季数冲突。

展示分组键不得写回 `identityState`、TMDB ID、任务目标、链别名、资源所有权或持久事件。

## qB 15 分钟观察窗

观察窗固定为 900 秒，持续时间只按本次快照的 `observedAt - lastActivity` 计算，不读取任务创建时间，不维护跨轮询计时器。

判定优先级：

1. 原始状态包含 `missing` 或 `error`：立即进入 `failed` 和“需要处理”，即使速度大于零也不能覆盖。
2. 正在校验：进入 `active` 和“处理中”。
3. 已完成或做种：进入 `succeeded`。
4. 已暂停或排队：进入 `waiting`，不标红。
5. 下载速度大于零：进入 `active`，即使 qB 同时返回普通 `stalled` 也立即恢复为“处理中”。
6. 普通 `stalled` 或下载速度为零：进入观察窗。

观察窗结果：

- 无活动时间小于 900 秒：`waiting`，显示“短暂无下载活动”。
- 无活动时间大于或等于 900 秒：`failed`，提示检查 Tracker、网络和可用做种。
- `lastActivity` 缺失、无效或晚于 `observedAt`：`waiting`，显示“持续时间暂未确认”，不能标红。
- 当前事实过期：继续由现有新鲜度逻辑显示“当前状态暂未确认”。

观察期的 `active/waiting` 只存在于当前事实投影。由轮询状态生成的观察中或超时失败不得写入永久事件；只有既有永久事件白名单和稳定失败证据规则可以入账。

## qB 共享评估器与控制室

qB 当前状态判定抽为无副作用的共享模块，任务链和 qB 只读摘要不得分别实现阈值与优先级。模块固定提供两层函数：

- `assess_qb_task(task, observed_at)`：返回单任务的事实状态、聚合状态、原因码、公开原因、无活动秒数和建议动作。
- `summarize_qb_assessments(results, observed_at)`：生成 `/api/qbittorrent/summary.assessment`。

两个函数都必须接收明确的 `observedAt`，不得读取系统时间。相同任务与相同观察时间在首页、任务链和控制室必须得到相同结论。

### Assessment 契约

`assessment` 及其所有子字段均为可选增量：

```text
state: normal | observing | action_required | unknown
counts:
  processing
  waiting
  observing
  actionRequired
  unknown
reasonCode
reasonText
observedAt
```

聚合优先级固定为 `action_required -> unknown -> observing -> normal`。只要存在真实错误或达到 900 秒的无活动任务，服务就进入 `action_required`；没有真实错误但存在无法判断的任务时进入 `unknown`；只有短暂断流时进入 `observing`。

所有公开原因必须通过现有公开脱敏边界。`counts.stalled` 保持 qB 上游原始口径，不参与普通服务健康派生。

### 控制室投影

控制室普通状态只读取 `assessment`：

- `action_required`：显示真实下载异常或“下载持续无活动”，qB 服务计入一项“需检查”。
- `unknown`：显示“部分任务状态暂未确认”，保持中性，不计入“需检查”。
- `observing`：显示“短暂无下载活动 · 观察中”，保持中性，不计入“需检查”。
- `normal`：显示在线。

顶部“需检查”按服务计数；无论 qB 有多少条异常，qB 最多增加一项。原始 `counts.stalled` 只在 qB 高级诊断显示为“qB 原始 stalled”。旧后端未返回 `assessment` 时，普通控制室不得回退使用原始 stalled 生成警告。

观察中、等待和处理中不进入永久事件。达到 900 秒后的 stalled 仍是当前事实失败，不因控制室接入而改变既有历史白名单；只有最终结果按原有稳定证据规则进入历史。

## 首页未知态

首页统计卡片由请求状态决定是否展示数值：

- 首次加载、超时或请求失败：全部显示“未知”。
- 成功响应：按响应值显示，真实 `0` 必须显示为 `0`。
- 不通过空摘要中的零值推断服务事实。

请求失败不改写最后一次成功响应，但普通统计卡片在错误存在时不得继续展示旧数值为当前结果。

## API 兼容性

只在 `GET /api/v2/home/summary` 响应的 `counts` 中增加两个可选字段，并在 `GET /api/qbittorrent/summary` 增加可选 `assessment`；不改 URL、方法、状态码、请求参数和旧字段类型。前端在首页新字段缺失时回退到旧资源级计数，但使用中性“问题”文案，不重新解释旧字段为可靠作品数。控制室在 `assessment` 缺失时不从原始 stalled 反推异常。

## 验收

自动化必须覆盖：

- 同一可靠 TMDB 电视剧同季多集只形成一个问题组；不同季分别分组。
- 身份冲突即使存在 TMDB ID，也不能进入可靠身份分组。
- 无身份的同标题同季资源只做机械展示分组，不产生任何身份写入。
- 大小写、空格、全半角和常规标点差异可以归入同一展示组；别名和模糊相似标题不能合并。
- 无标题、媒体类型冲突或季数冲突逐资源计数。
- 问题组使用全部异常资源计算，结果不受首页八条问题响应上限影响。
- qB 无活动 `899` 秒为等待，`900` 秒为失败。
- qB `missing/error` 立即失败；无可靠活动时间为等待；正速度从普通 `stalled` 立即恢复；校验为处理中；暂停和排队为等待。
- qB 活动时间来自未来时为等待并显示持续时间暂未确认。
- 观察状态不生成永久历史事件。
- 单任务评估器和聚合器在相同 `observedAt` 下产生稳定结果，不读取系统时间。
- 混合任务同时包含观察中、未知和真实失败时，summary、任务链和控制室都按 `action_required` 处理；移除真实失败后按 `unknown`，再移除未知后按 `observing`。
- 控制室观察期内不增加顶部“需检查”；达到 900 秒或出现 missing/error 后，qB 服务只增加一项。
- qB 原始 stalled 数量只出现在高级诊断，普通卡片不再显示“有卡住任务”或“卡住任务”。
- 首页首次加载、超时和请求失败显示未知，成功响应中的零显示为零。

实机验收目标为：

```text
5 个问题组 · 涉及 15 个资源 · 其中 14 条身份未确认
```

具体数值随实时任务变化，但三项口径必须始终可解释且互不冒充。
