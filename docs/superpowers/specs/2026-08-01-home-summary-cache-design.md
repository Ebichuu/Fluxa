# Fluxa 首页后台摘要缓存与部分确认统计设计

日期：2026-08-01

状态：设计已确认，待实施计划

## 1. 目标

本波次只解决首页 P0：冷启动超时、模块失败互相拖累，以及“下载完成未入库”和“追更缺集”全有或全无的问题。

完成后必须满足：

1. `GET /api/v2/home/summary` 只读取 SQLite 缓存并组装公开响应，不刷新缓存、不访问外部服务、不触发后台任务。
2. 容器重启后首次首页请求在 500ms 内返回；缓存完全为空时也返回结构完整的 `200` 部分响应。
3. 各模块独立缓存、独立更新时间和失败状态；一个模块失败不影响其他模块的可靠结果。
4. 已确认数量与尚未确认对象同时展示，二者不重叠且可以严格对账。
5. 旧响应字段不删除、不改名、不改型；新增字段均为可选增量。

## 2. 实施边界

本波次包含：

- 首页后台摘要刷新器与 SQLite 模块缓存。
- 首页纯读缓存接口与结构完整的空缓存响应。
- “下载完成未入库”和“追更缺集”的部分确认统计。
- 三处文案收口：候选来源更新与 Torra 订阅搜索分开命名、跳过与错误分开计数、重复同步改为累计后台同步。

本波次不包含：

- Symedia 归档监控、CloudDrive、Webhook、STRM、归档调度或文件观察接入。
- 身份台账重构、未关联日历清理或 34 部电影的实际归档操作。
- Torra 订阅搜索策略推断、RSS 优先批量调整或任何 Torra 写操作。
- 海报来源或占位视觉重构。
- 统一事件台账重建。

## 3. 当前问题

当前 `HomeSummaryService.snapshot()` 在一个 HTTP 请求内依次执行：

- 任务链完整快照；
- qB 摘要读取；
- Symedia 当日归档投影；
- RSS 台账与资源中心统计；
- 追更工作台和对账；
- 首页问题组、诊断和关注项派生。

任一慢调用都会延长整个请求。第一次请求还会同时承担各下游缓存预热，实机约 20 秒后超时；刷新后因下游缓存已经建立而恢复到约 1.3 秒。

现有两个关注项也采用全有或全无逻辑：

- 任一 qB 完成任务缺少当前 Symedia 事实，整个“下载完成未入库”返回 `null`。
- 任一追更缺少 `missingEpisodes` 数组，整个“追更缺集”返回 `null`。

这两种做法没有伪造数据，但丢失了已经确认的有效部分。

## 4. 总体架构

首页拆成两个互不混用的路径：

```text
后台刷新器
  -> 事务外读取任务链 / qB / 归档 / 秒传 / 追更 / RSS
  -> 按模块派生安全摘要
  -> 每个模块使用独立短事务原子写入 SQLite

GET /api/v2/home/summary
  -> 一次 SQLite 只读查询
  -> 按当前 Asia/Shanghai 日期选择模块行
  -> 组装兼容响应
  -> 立即返回
```

GET 路径禁止：

- 调用任务链 `full_snapshot()`；
- 调用 Torra、qB、Symedia、Emby、RSS 或追更工作台；
- 领取刷新租约；
- 写入最后访问时间；
- 因缓存过期而顺便触发刷新。

缓存过期只改变公开确认状态，刷新始终由后台刷新器负责。

## 5. 模块划分

首页至少使用以下独立模块键：

| 模块 | 主要内容 | 缓存范围 |
|---|---|---|
| `task_pipeline` | 任务计数、问题组、诊断、媒体结果 | `global` |
| `qb_activity` | qB 活跃任务与连接证据 | `global` |
| `archive_today` | 今日归档文件、关联任务和未关联文件 | `date:YYYY-MM-DD` |
| `secupload` | 秒传失败、恢复计划和系统问题摘要 | `global` |
| `subscription_progress` | 明确缺集和未提供进度的追更数量 | `global` |
| `rss_resource_center` | 今日新资源、待识别、未关联和可洗版统计 | `date:YYYY-MM-DD` |
| `service_health` | 首页所需的核心服务可确认状态 | `global` |

同一次外部读取可以为多个模块提供输入，例如任务链快照可以派生 `task_pipeline`、`archive_today` 和 `service_health`；但落库时仍必须写成独立模块行。一个模块派生失败不得阻止已成功模块落库。

## 6. SQLite 数据模型

新增模块缓存表：

```text
home_summary_module_cache
  module_key TEXT
  scope_key TEXT
  payload_json TEXT
  observed_at TEXT
  fresh_until TEXT
  confirmation TEXT
  last_success_at TEXT
  last_attempt_at TEXT
  last_error_code TEXT
  last_error_text TEXT
  version INTEGER
  updated_at TEXT
  PRIMARY KEY (module_key, scope_key)
```

`confirmation` 只使用：

- `confirmed`：本轮成功且证据完整；
- `partial`：本轮成功但只覆盖部分对象，或刷新失败后继续展示上一份可靠值；
- `unknown`：从未成功，当前没有可靠值。

刷新失败时：

- 保留原 `payload_json`、`observed_at` 和 `last_success_at`；
- 更新 `last_attempt_at`、`last_error_code` 和脱敏后的 `last_error_text`；
- 将公开确认状态投影为 `partial`；
- 不写入路径、URL、Cookie、Passkey、Authorization、外部原始 ID 或上游错误原文。

从未成功的模块允许存在只有失败元数据、没有有效 payload 的行，GET 将其投影为 `unknown`。

新增刷新租约表：

```text
home_summary_refresh_state
  id INTEGER PRIMARY KEY CHECK (id = 1)
  running INTEGER
  lease_token TEXT
  lease_until TEXT
  started_at TEXT
  finished_at TEXT
  last_error_code TEXT
  version INTEGER
  updated_at TEXT
```

模块缓存和租约均使用现有 SQLite 运行时。迁移只新增表，不修改任务事实、订阅台账或资源事件。

## 7. 后台刷新与事务

后台刷新器在应用后台运行时注册，固定单实例线程执行。首次后台刷新在服务启动后尽快进行，后续按固定节奏刷新；页面轮询和 GET 不控制其生命周期。

每轮流程：

1. 先用进程内非阻塞锁阻止同一进程重复进入。
2. 再用 SQLite 短事务原子领取租约。
3. 关闭事务。
4. 在事务外读取所有外部或重计算数据。
5. 每个模块独立派生结果。
6. 每个成功或失败模块使用独立短事务更新自己的缓存行。
7. 最后用短事务释放租约并记录汇总结果。

任何数据库事务内禁止访问网络、调用任务全快照或等待其他锁。

进程崩溃后租约到期才允许下一轮重新领取。容器多线程、重复定时调用或内部手动刷新同时到达时，只允许一份刷新执行。首页刷新按钮仍然只是重新 GET 当前缓存，不触发重汇总。

## 8. GET 纯读响应

`GET /api/v2/home/summary` 使用一次 SQLite 只读连接读取：

- 全部 `global` 模块；
- 当前 Asia/Shanghai 日期对应的 `date:YYYY-MM-DD` 模块。

响应继续保留现有：

- `ok`、`generatedAt`、`healthState`、`headline`、`detail`；
- `counts`、`statisticsMeta`、`resourceCenter`、`archiveSummary`；
- `focusItems`、`problemGroups`、`issues`、`diagnostics` 和 `systemIssues`。

顶层 `generatedAt` 表示本次 GET 从缓存组装公开响应的时间，不再代表所有模块同时刷新。数据发生时间、新鲜度和失败状态只能读取对应模块的 `observedAt/freshUntil/confirmation`。

可选新增：

```text
modules[moduleKey]
  observedAt
  freshUntil
  confirmation
  lastSuccessAt
  lastAttemptAt
  errorCode
  errorText
```

缓存完全为空时返回：

- HTTP `200`；
- `ok=true`；
- `healthState=evidence_insufficient`；
- 数量字段使用现有可空约定，无法确认的值为 `null`；
- 所有既有对象和数组字段仍存在；
- `modules` 中各模块为 `unknown`。

只有 SQLite 本身不可读或响应无法组装时才返回现有 `502 HOME_SUMMARY_READ_FAILED`。

## 9. 日期隔离

日期型模块使用 Asia/Shanghai 自然日生成范围键：

```text
date:2026-08-01
```

GET 在北京时间午夜后只读取新日期行，禁止回退昨天的 `archive_today` 或 `rss_resource_center` 数值。若新日期尚未生成缓存，对应值立即显示未知；昨天的历史行保留用于审计和后续清理，不冒充今天。

后台读取当日数据时，上下界统一使用：

```text
[北京时间当天 00:00:00, 次日 00:00:00)
```

持久时间仍使用 UTC ISO 8601。

## 10. 部分确认统计

### 10.1 下载完成未入库

统计集合只包含具有当前、可靠 qB `succeeded` 事实的规范任务目标。

互斥分类：

- `confirmedNotArchived`：Symedia 当前事实为 `waiting`、`active` 或 `failed`；
- `confirmedArchivedOrProtected`：Symedia 当前事实为 `succeeded` 或 `protected`；
- `unconfirmed`：没有上述任一当前、可靠 Symedia 事实。

公开规则：

- 原 `value = len(confirmedNotArchived)`；
- 新 `unconfirmedCount = len(unconfirmed)`；
- 两者不得包含同一任务；
- `confirmation=partial` 当且仅当 `unconfirmedCount > 0` 或模块刷新失败后使用旧值；
- 存在明确 `failed` 时状态为 `action_required`；
- 只有等待或活动记录时状态为 `processing`；
- `value=0` 且 `unconfirmedCount>0` 时状态为 `unknown`；
- 两者均为零且依赖证据可读时状态为 `normal`。

示例：

```text
已确认未入库 2 个 · 另有 14 个暂未确认
```

### 10.2 追更缺集

对当前追更条目做互斥分类：

- `confirmedProgress`：明确提供数组型 `missingEpisodes`；
- `unconfirmedProgress`：缺少该字段、字段类型无效或该条目读取失败。

公开规则：

- 原 `value = sum(len(missingEpisodes))`，只统计 `confirmedProgress`；
- 新 `unconfirmedCount = len(unconfirmedProgress)`，单位为“条追更”；
- `value>0` 时状态为 `action_required`；
- `value=0` 且 `unconfirmedCount=0` 时状态为 `normal`；
- `value=0` 且 `unconfirmedCount>0` 时状态为 `unknown`；
- 有已确认缺集且仍有未确认条目时 `confirmation=partial`，不能把未知条目当作零缺集。

示例：

```text
已确认缺失 6 集 · 131 条追更尚未提供进度
```

### 10.3 关注项兼容字段

`focusItems[]` 保留原字段，并可选新增：

```text
confirmation: confirmed | partial | unknown
unconfirmedCount: number
unconfirmedUnit: string
observedAt: string
freshUntil: string
errorReason: string
```

旧前端继续读取 `value/state/detail`；新前端使用新增字段显示“已确认 + 暂未确认”。

## 11. 模块失败与最后可靠值

模块刷新失败但存在历史成功结果时：

- `value` 继续返回上次确认数量；
- `confirmation=partial`；
- `state=unknown`，除非历史值中有仍然有效的明确失败事实需要保持 `action_required`；
- `detail` 必须说明“上次确认值 · 当前状态暂未确认”；
- `observedAt` 仍是上次成功证据时间；
- `lastAttemptAt` 和 `errorReason` 说明当前刷新失败。

从未成功过时：

- `value=null`；
- `confirmation=unknown`；
- `state=unknown`；
- 不使用默认零值。

一个模块失败不得把其他模块的 `value`、确认状态或更新时间清空。

## 12. 三处文案收口

### 12.1 调度器名称

普通页面统一区分：

- `候选来源更新`：只更新 Fluxa 本地候选池；
- `Torra 订阅搜索`：Torra 或旧订阅总调度中的 PT 搜索能力。

不再使用容易混淆的“后台订阅定时扫描”。

### 12.2 跳过与错误

候选刷新和活动摘要分别返回、显示：

- `skippedCount`：规则排除、无身份、重复等正常跳过；
- `errorCount`：来源读取或处理真实失败。

旧合并字段继续兼容，但新文案禁止显示“跳过/错误 N 条”。

### 12.3 累计后台同步

活动记录中的重复折叠文案改为：

```text
累计后台同步 1731 次
```

该累计是信息压缩，不进入异常数量，也不使用“重复”暗示故障。

## 13. 性能与并发验收

必须覆盖：

1. 容器重启后、后台首轮尚未完成时，首页首次 GET 在 500ms 内返回结构完整的部分响应。
2. 首页 GET 对 Torra、qB、Symedia、RSS、Emby、任务全快照和追更工作台的调用数均为零。
3. 各模块已有缓存时，首页 GET 只执行 SQLite 读取和纯内存组装。
4. 任一模块超时，其他模块继续返回自己的可靠值。
5. 同时触发多个刷新请求时，只执行一轮外部读取。
6. SQLite 租约领取、模块写入或进程退出中断后，上一份完整模块缓存仍可读取。
7. 后台刷新期间 GET 不阻塞。
8. 日期跨过 Asia/Shanghai 午夜后，昨天的今日统计不进入新日期响应。

这里的原子边界是单个模块行：模块更新时间允许不同，但任一模块写入中断都必须回滚该行，不能暴露半写 JSON；其他已经成功提交的模块保持各自完整的新值，尚未提交的模块继续保留上一份完整值。

## 14. 统计验收

必须覆盖：

1. 已确认未入库 2 个、未确认 14 个时，`value=2`、`unconfirmedCount=14`，两组目标没有交集。
2. 已确认缺失 6 集、131 条追更未提供进度时，`value=6`、`unconfirmedCount=131`。
3. 已确认值为零且没有未确认对象时，显示真实 `0`，不能显示未知。
4. 已确认值为零但存在未确认对象时，显示“已确认 0 + 暂未确认”，不能伪称全部正常。
5. 部分模块失败后继续显示上次可靠值，并标记当前暂未确认。
6. 从未成功的模块显示 `null/unknown`，不能使用空摘要中的零。
7. 旧消费者只读取 `value/state/detail` 时行为兼容。

## 15. 安全与回滚

- 后台缓存只保存首页所需的公开摘要，不保存原始任务、文件名、路径、hash、URL、Cookie、Passkey 或远端内部 ID。
- 错误只保存稳定错误码和公开脱敏原因。
- 新表写入失败不修改既有任务、订阅、RSS 或资源事件。
- 回滚旧版本时新表可保留且被忽略；不要求回写旧运行时字段。
- 本波次不执行任何 Torra、qB、Symedia、Emby、RSS 或追更写动作。
