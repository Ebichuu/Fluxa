# 私人 PT RSS 种子库设计

状态：设计已确认，待书面复核后进入实施计划

日期：2026-07-18

## 1. 背景

用户计划向媒体控制中心提供 5～10 个 PT 站点的私人 RSS 地址。这些地址包含个人 Passkey，并可能在 RSS `enclosure` 或下载链接中继续携带 Passkey。

Torra 当前已经能够按站点 RSS 模式运行：一个批次先预热站点 RSS，再让多条订阅共享候选。但是 Torra 源码明确把原始 RSS 候选保存在单次 batch child 的内存缓存中，批次结束后直接回收，不保存长期磁盘快照。因此它适合实时订阅处理，不适合作为可搜索的一周种子历史库。

用户明确要求：

- 不修改 Torra 源码。
- 媒体控制中心直接接收私人 RSS。
- RSS 地址和种子下载地址允许明文保存在 SQLite。
- 本地种子库用于查看站点更新、搜索历史、匹配订阅和触发追更洗版。

## 2. 目标

1. 统一轮询 5～10 个私人 PT RSS，避免每条订阅分别访问站点。
2. 将最近一周的 RSS 条目保存为可搜索的本地种子索引。
3. 支持按标题、站点、时间、媒体类型、季集和版本特征查询。
4. 记录各站新增内容和获取状态，形成轻量 PT 更新台账。
5. 用新 RSS 条目实时唤醒追更洗版，减少固定主动搜索。
6. Torra 继续负责最终版本评分、在线查重、下载和下游主线。
7. 不修改 Torra，不复制 Torra 的完整质量评分器。

## 3. 非目标

- 不爬取 PT 站网页或建立通用 PT 搜索引擎。
- 不保存整个站点的全部历史种子。
- 不长期保存 `.torrent` 文件。
- 不在本地种子库直接决定哪个版本必须下载。
- 不在第一版绕过 Torra 直接向 qB 推送 RSS 种子。
- 不把 RSS 地址、Passkey 或下载 URL 返回给浏览器。
- 不承诺 RSS 能替代站点主动搜索；主动搜索继续作为有限兜底。

## 4. 业务边界

```text
媒体控制中心
  ├─ 直接轮询私人 RSS
  ├─ 保存最近 7 天轻量种子元数据
  ├─ 本地搜索、去重和季集匹配
  └─ 发现追更候选后唤醒 Torra

Torra
  ├─ 保持现有源码和配置
  ├─ 执行追更洗版分析
  ├─ 使用真实版本控制和元数据权重
  ├─ 在线查重并选择下载器
  └─ 进入 qB → 115 → Symedia → Emby 主线
```

媒体控制中心只把 RSS 命中当作“值得让 Torra 再检查一次”的事件。版本是否更好仍以 Torra 返回结果为准。

## 5. 总体架构

```text
私人 RSS 地址
  → PrivateRssCollector
      → 条件请求 / 限频 / 失败退避
      → RSS / Atom 标准化
  → RssSeedRepository
      → SQLite 明文 RSS 地址和下载地址
      → 条目去重、保留期和抓取审计
      → FTS5 本地全文索引
  → RssMatchCoordinator
      → 匹配媒体控制中心订阅、季和集
      → 关联 24/48 小时追更洗版窗口
      → 生成一次性唤醒事件
  → TorraQualityCoordinator
      → Torra 追更洗版分析与候选下载
```

## 6. SQLite 数据设计

RSS 种子库与订阅台账共用 `db/media_control_center.sqlite3`，通过独立表和 schema version 管理。

### 6.1 `rss_sources`

- `source_id` 主键。
- `name`，用户显示名称。
- `site_host`，脱敏显示和请求约束使用。
- `feed_url`，私人 RSS 完整地址，按用户选择明文保存。
- `enabled`。
- `poll_interval_minutes`，只允许 `1 / 3 / 5`，默认 `5`。
- `retention_days`，第一版只允许 `3 / 7 / 14`，默认 `7`。
- `etag`。
- `last_modified`。
- `last_success_at`。
- `last_error_code`。
- `last_error_message`，必须脱敏。
- `failure_count`。
- `backoff_until`。
- `next_poll_at`。
- `created_at`、`updated_at`、`version`。

### 6.2 `rss_items`

- `item_id` 主键。
- `source_id` 外键。
- `fingerprint`，站点内唯一索引。
- `guid_hash`，对 RSS GUID 或稳定链接做哈希。
- `title`。
- `normalized_title`。
- `published_at`。
- `category`。
- `size_bytes`。
- `detail_url`，允许明文保存，但 API 不返回完整敏感查询参数。
- `download_url`，允许明文保存，浏览器永不读取。
- `media_type`。
- `season_number`。
- `episode_numbers_json`。
- `release_group`。
- `resolution`、`video_codec`、`dynamic_range` 等轻量版本摘要。
- `first_seen_at`、`last_seen_at`、`expires_at`。

唯一指纹优先使用站点 ID + GUID；缺少 GUID 时使用站点 ID、标题、发布时间、大小和稳定链接的规范化哈希。

### 6.3 `rss_item_search`

SQLite FTS5 虚拟表，索引：

- 标题和规范化标题。
- 制作组。
- 分类和站点名称。
- 分辨率、编码、HDR 等可搜索摘要。

结构化筛选继续读取 `rss_items`，FTS5 只负责文本检索。

### 6.4 `rss_fetch_runs`

- `run_id`。
- `source_id`。
- `status`。
- `http_status`。
- `not_modified`。
- `received_count`、`inserted_count`、`updated_count`。
- `duration_ms`。
- `error_code`、脱敏 `error_message`。
- `started_at`、`completed_at`。

只保留最近 30 天或每站最近 1000 条抓取记录，以先到限制为准。

### 6.5 `rss_subscription_matches`

- `match_id`。
- `item_id`。
- `subscription_key`。
- `unit_key`，关联具体电影或剧集观察单元。
- `match_status`：`candidate / ignored / triggered / confirmed / expired`。
- `match_reason_json`，只记录身份、季集和标题依据，不记录本地质量结论。
- `trigger_action_id`。
- `created_at`、`updated_at`。

同一 RSS 条目与同一观察单元只允许建立一条匹配，避免重复唤醒 Torra。

## 7. 私人 RSS 与明文存储规则

本方案按照用户明确选择，不增加独立加密密钥：

- `feed_url`、`detail_url` 和 `download_url` 在 SQLite 中明文保存。
- `db/media_control_center.sqlite3`、WAL、备份和迁移副本都属于敏感文件。
- fnOS 持久目录必须只允许容器运行用户和管理员读取。
- 数据库备份不得上传到公开云盘或公开代码仓库。
- 前端读取来源时只返回 `source_id`、名称、域名、周期和“已配置”，不返回完整 URL。
- 编辑页面的空 URL 表示保持原值；更换 URL 必须重新完整输入。
- 日志、异常、活动记录和抓取审计必须删除查询参数或替换为 `***`。
- HTTP 客户端错误不得包含完整请求 URL。
- 默认导出和诊断包不包含 RSS 地址及下载地址。

如果数据库或备份被复制，Passkey 会泄露；这是明文方案明确接受的剩余风险。

## 8. 轮询规则

- 生产使用独立 `MCC_PRIVATE_RSS_ENABLED` 环境闸门，默认关闭。该闸门只控制后台抓取和“测试 RSS”的真实网络访问；来源配置 CRUD 只写本地 SQLite，可在收集器关闭时提前完成。
- 每个站点独立设置 1、3 或 5 分钟，默认 5 分钟。
- 同一来源不能并发抓取。
- 全局最多同时抓取 2 个来源。
- 每次实际时间加入最多 15 秒随机错峰，避免所有站点同秒请求。
- 优先发送 `If-None-Match` 和 `If-Modified-Since`，正确处理 `304`。
- `429` 按 `Retry-After` 退避；连续失败使用指数退避，最大 60 分钟。
- 单请求连接超时 5 秒、总超时 20 秒。
- 响应体默认限制 2 MiB，超过后拒绝解析并记录脱敏错误。
- 最多跟随 3 次重定向，每次重定向重新执行地址安全检查。
- 站点明确给出更严格周期时，媒体控制中心使用更慢的周期。

## 9. 地址与请求安全

虽然 RSS 地址由管理员配置，服务端仍必须防止 SSRF：

- 默认只允许 `https`；需要 `http` 时由高级设置单独开启。
- 禁止 `file:`、`ftp:`、`data:` 等非 HTTP 协议。
- DNS 解析后拒绝环回、链路本地、组播和未显式允许的内网地址。
- 端口默认只允许 80、443；非标准端口需要明确保存确认。
- 重定向目标必须重新解析和校验。
- Content-Type 不可信，解析器按体积和 XML 结构双重限制。
- RSS 描述中的 HTML 不直接渲染到页面。

## 10. RSS 标准化与解析

第一版支持 RSS 2.0 和 Atom，并从真实的 5～10 个站点样本建立测试夹具，不猜测站点字段。

标准字段包括：

- GUID、标题、发布时间。
- 分类、大小、详情链接和下载链接。
- description 中可验证的补充字段。

标题解析优先完成：

- 电影或电视剧身份线索。
- 季号、单集和连续集范围。
- 1080P、2160P、编码、HDR、杜比视界和制作组摘要。

这些摘要用于本地搜索和粗匹配，不替代 Torra 的 `MetaWeightCalculator` 和版本控制规则。

## 11. 保留与清理

- 默认保存 7 天；每个来源可选 3、7 或 14 天。
- `expires_at` 在插入时按来源策略计算。
- 每小时分批删除到期条目，每批不超过 1000 条。
- 删除条目时同步删除 FTS 和未使用匹配记录。
- 不在每次清理后执行 `VACUUM`；只在人工维护或低频计划中执行。
- 第一版设置全局 200000 条软上限，接近上限时优先删除已到期和最旧条目。

## 12. 本地种子库页面

顶部导航允许增加“种子库”。页面包括：

### 12.1 更新流

- 最近 1 小时、24 小时和 7 天新增数量。
- 按站点查看最新条目。
- 显示标题、发布时间、大小、季集和版本摘要。
- 不显示完整下载 URL 或 Passkey。

### 12.2 本地搜索

- 关键词全文搜索。
- 站点、时间、媒体类型和分类筛选。
- 季号、集号、分辨率、HDR 和制作组筛选。
- 默认分页，每页 50 条，最大 100 条。

### 12.3 RSS 来源管理

- 新增、编辑、启用、暂停和删除来源。
- 设置轮询周期和保留天数。
- “测试 RSS”只获取并解析，不写入种子库，返回脱敏摘要。
- 保存后不回显完整地址；更换地址需要重新输入。
- 删除来源需要二次确认，并级联删除该来源的本地条目、FTS 索引和未执行匹配；不会访问 PT 站或删除站点端数据。

## 13. 与追更洗版的协作

追更洗版从固定频繁主动搜索改为 RSS 优先：

```text
本集进入 Emby
  → 建立 24/48 小时窗口
  → RSS 种子库每次新增条目时本地匹配
  → 命中订阅身份和集数
      → 创建一次性 rss_subscription_match
      → 唤醒 Torra 追更洗版分析
      → Torra 确认是否更好并决定下载
  → RSS 一直没有可靠命中
      → 12/24 小时或 12/24/48 小时主动搜索兜底
  → 窗口到期关闭当前集
```

规则：

- 24 小时窗口主动兜底：12、24 小时。
- 48 小时窗口主动兜底：12、24、48 小时。
- RSS 新条目可在任何时间即时唤醒，但同一条目/单元只触发一次。
- 唤醒前仍检查 Torra、qB 活动、冷却、每日上限和幂等状态。
- 本地匹配失败或不可靠时不触发。
- 迁移已有 RSS 历史不反向创建追更任务。
- 窗口到期后新 RSS 只保留在种子库，不再自动唤醒该集。

## 14. Torra 边界

- 不修改 Torra 源码或数据库。
- 不向 Torra 注入本地 RSS 索引。
- 第一版只调用已核对的追更洗版分析、候选下载和 job 查询接口。
- 本地 RSS 命中不会直接向 qB 添加任务。
- 以后只有核对到 Torra 已有“指定种子 URL 安全提交”接口，才单独设计直达能力。

## 15. 计划 API

以下接口是本设计的目标契约。来源、种子、匹配读取和统一动作读取已经在第一版实现；匹配后触发 Torra 分析仍待下一阶段：

- `GET /api/v2/rss-sources`
- `POST /api/v2/rss-sources`
- `GET /api/v2/rss-sources/:id`
- `PATCH /api/v2/rss-sources/:id`
- `DELETE /api/v2/rss-sources/:id`
- `POST /api/v2/rss-sources/:id/tests`
- `GET /api/v2/rss-items`
- `GET /api/v2/rss-items/:id`
- `GET /api/v2/rss-matches`
- `POST /api/v2/rss-matches/:id/torra-rewash-analyses`

来源创建返回 `201 Created` 和 `Location`。测试和 Torra 分析属于有外部副作用的异步动作，返回 `202 Accepted`。列表必须分页。RSS 来源响应不含完整 `feed_url`；种子条目响应不含 `download_url`。

补充契约：

- `POST /rss-sources` 的请求包含完整 `feedUrl`，响应只返回 `feedConfigured=true` 和脱敏域名。
- `PATCH /rss-sources/:id` 未提供 `feedUrl` 表示保持原值；空字符串无效并返回 `422`。
- 相同来源指纹重复创建返回 `409`。
- `DELETE /rss-sources/:id` 成功返回 `204 No Content`。
- RSS 来源、种子和匹配列表都使用 `limit/offset` 分页，并返回总数或下一页游标。
- 两个异步 POST 返回动作 ID 和 `Location: /api/v2/automation-actions/:actionId`，不能用 `200` 表示外部任务已经完成。
- 非法 RSS、非法周期和保留期返回 `422`；限频返回 `429`；上游失败返回 `502`；收集闸门关闭时，测试和后台抓取返回或保持 `503/停用`，本地来源配置 CRUD 不因此访问外部网络。

## 16. 错误处理

- XML 无效、响应过大和字段缺失只影响当前来源。
- 一个站点失败不阻塞其他站点。
- 认证失效显示“RSS 地址可能已失效”，不回显地址。
- 连续失败进入退避，不在短时间内持续重试。
- SQLite 锁冲突返回可重试状态，不丢弃已拉取批次。
- 重复 GUID 或指纹只更新 `last_seen_at`，不新增重复条目。
- 解析不确定的季集仍可进入种子库，但不得自动触发追更洗版。

## 17. 测试方案

### 17.1 RSS 获取

- 真实站点脱敏 XML 夹具。
- RSS 2.0、Atom、CDATA、中文编码和缺失字段。
- ETag、Last-Modified、304、重定向、429 和超时。
- 响应大小限制、恶意 XML 和 HTML 清理。
- 地址和错误日志不泄露 Passkey。

### 17.2 去重、搜索与清理

- GUID 去重与无 GUID 指纹。
- 同站重复、跨站同名和发布时间更新。
- FTS 中文、英文、制作组和版本关键词。
- 3/7/14 天到期清理和 200000 条软上限。
- WAL 重启恢复和多线程读取。

### 17.3 追更协作

- RSS 命中正确订阅、季和集。
- 同一条目只触发一次。
- 不可靠匹配不触发。
- 24/48 小时到期后不触发。
- Torra/qB 活动、冷却和每日上限阻止唤醒。
- RSS 无命中时按 12/24 或 12/24/48 兜底。

### 17.4 安全与回归

- 所有读 API 不返回完整 RSS 或下载 URL。
- 日志、错误、活动记录、诊断包和默认导出不含 Passkey。
- SSRF、重定向和响应体限制。
- 现有订阅、影院大厅和 PT 主线不受影响。
- 测试不连接真实私人 RSS 或外部写接口。

## 18. 实施顺序

1. 在 SQLite schema 中加入 RSS 来源、条目、FTS、抓取记录和匹配表。
2. 增加来源仓储、明文敏感字段脱敏输出和日志清洗。
3. 增加 RSS/Atom 解析器并用真实脱敏样本建立夹具。
4. 增加限频、条件请求、错峰、退避和 SSRF 防护的收集器。
5. 增加去重、FTS 搜索、保留期和清理任务。
6. 增加来源管理和本地种子库只读 API。
7. 增加种子库页面和来源设置。
8. 增加订阅/季集本地匹配和一次性唤醒事件。
9. 将追更洗版主动兜底调整为 12/24 或 12/24/48。
10. 完成契约、安全、Docker 和重启测试后构建候选镜像。

## 19. 验收标准

- 5～10 个私人 RSS 可以独立设置周期并稳定轮询。
- RSS 地址和下载地址按用户选择明文保存，但不会通过 API 和日志泄露。
- 最近 7 天种子可在本地快速搜索、筛选和分页。
- 同一 RSS 条目不重复入库。
- 到期条目自动清理，数据库不会无限增长。
- 新 RSS 条目能可靠匹配当前追更集并唤醒一次 Torra。
- Torra 继续负责最终质量评分和下载，源码不修改。
- RSS 无命中时只有有限主动兜底，不再固定频繁搜索。
- 所有新调度和外部动作默认关闭，实机前只运行模拟测试。
