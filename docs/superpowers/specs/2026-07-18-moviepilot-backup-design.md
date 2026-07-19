# MoviePilot 人工备用入口设计

状态：阶段 7 已按确认设计实现并通过隔离测试

日期：2026-07-18

## 1. 目标与边界

为 Torra 主线提供一个明确人工选择的 MoviePilot 备用入口。入口只允许用户在 Torra 追更观察窗口已经结束、Torra 与 qB 状态可核对且没有相关活动任务时使用。

本阶段实现：

- `POST /api/v2/subscriptions/:id/moviepilot-previews`
- `POST /api/v2/subscriptions/:id/moviepilot-pushes`
- 独立的 `MCC_MOVIEPILOT_BACKUP_ENABLED` 闸门，默认关闭。
- 已有 MoviePilot 订阅的人工重搜。
- 没有已有订阅时的人工创建并重搜。
- SQLite 幂等、审计和安全结果摘要。

本阶段不实现：

- MoviePilot 自动调度器或自动兜底。
- 浏览器保存 MoviePilot 凭据、完整响应、订阅 ID 或 URL。
- 新的 MoviePilot HTTP client；继续复用 NasEmby 既有函数。
- Torra 不可达时的自动切换。
- React 页面接入。

## 2. 现有能力复用

新增 `moviepilot_backup_runtime.py` 作为安全编排层，复用 `services.py` 中的既有配置、查重、创建和重搜逻辑。为隔离私有函数，`services.py` 增加少量只读/重搜门面；不复制请求 URL、认证头或 MoviePilot payload 构造逻辑。

运行时通过依赖注入接收：

- MoviePilot 预览、已有订阅重搜和新订阅创建函数。
- SQLite 质量观察仓储。
- 唯一订阅台账读取器。
- Torra 与 qB 只读客户端。
- 环境变量读取器和时钟。

运行时不向浏览器传递内部门面返回的原始字段。

## 3. 资格预检

预览和推送都执行同一组服务端预检；推送会在执行前重新执行，不能信任旧预览。

1. `MCC_MOVIEPILOT_BACKUP_ENABLED` 未开启时立即返回 `503`，不调用 MoviePilot、Torra 或 qB。
2. 从 SQLite 订阅台账按路径 ID 重建条目；不存在返回 `404`。
3. 标题、媒体类型、稳定 TMDB ID 和电视剧季信息必须可解析，否则返回 `422`。
4. 订阅必须存在观察单元，且所有相关单元都处于 `observation_expired`；观察中、等待基准、搜索中、暂停或阻塞状态均不允许备用推送。
5. Torra 必须已配置且可读；Torra 订阅映射缺失或处于运行/变更状态在预览中形成安全阻塞摘要，推送返回 `409`；连接失败返回 `502`。Torra 不可达不会触发 MoviePilot 自动切换。
6. qB 必须连接成功；匹配该订阅标题的活动下载在预览中形成安全阻塞摘要，推送返回 `409`；qB 读取失败返回 `502`。
7. MoviePilot 查重结果只在服务端内存中使用；已有订阅选择重搜路径，没有已有订阅选择创建路径。

预览将业务阻塞表达为安全摘要；上游不可用、认证失败或未配置仍使用错误状态，不伪装成可执行。

## 4. HTTP 契约

### 4.1 预览

`POST /api/v2/subscriptions/:id/moviepilot-previews` 使用 POST，因为它会执行受闸门保护的外部读取和查重。请求正文为空对象；未知字段返回 `422`。

成功返回 `200`：

```json
{
  "subscriptionId": "tv:202",
  "ready": true,
  "mode": "search-existing",
  "title": "测试剧",
  "mediaType": "tv",
  "tmdbId": "202",
  "seasons": [1],
  "blockers": []
}
```

响应不包含 MoviePilot URL、Token、订阅 ID、原始查重行或完整上游响应。`ready=false` 的业务阻塞仍返回 `200` 摘要；闸门、资源不存在、请求语义和上游错误分别返回 `503/404/422/502`。

### 4.2 推送

`POST /api/v2/subscriptions/:id/moviepilot-pushes` 请求字段仅允许：

- `confirm: true`。
- `idempotencyKey`，长度 12–128 个字符。

服务端重新执行资格预检和查重：

- `search-existing`：调用已有订阅重搜。
- `create-and-search`：调用现有创建逻辑，随后触发搜索。

MoviePilot 没有稳定的可轮询 job 契约，因此推送同步返回 `200`，不伪造 `202`。成功响应只保留 `ok`、`mode`、`alreadyExists`、`searchTriggered`、安全消息和本地动作 ID。失败写入终态动作并返回 `502`；同一幂等键重放返回已保存的安全摘要，不重复调用外部服务。

## 5. SQLite 动作

推送使用 `provider_actions`：

- `provider=moviepilot`。
- `action_type=backup-push`。
- `request_summary` 只保存来源、订阅 key、媒体类型和季摘要，不保存 Token、URL、MoviePilot 订阅 ID 或原始 payload。
- `response_summary` 只保存白名单布尔值、模式、消息和错误码。
- 外部响应没有 job ID 时保持本地同步终态；不会创建后台调度任务。

幂等键冲突返回 `409 MOVIEPILOT_IDEMPOTENCY_CONFLICT`；同一动作已在执行返回 `409 MOVIEPILOT_IN_PROGRESS`；冷却返回 `409 MOVIEPILOT_COOLDOWN`。

## 6. 认证与安全

- 两条新路由均为 v2 会话保护路由，危险 POST 要求同源或允许 Origin。
- 闸门关闭时不进行任何 MoviePilot/Torra/qB 请求。
- 浏览器不能提交 MoviePilot 订阅 ID、Torra ID、Token、URL 或原始候选映射。
- 旧 `/api/moviepilot/*` 路由继续保留在 URL map 中，但默认由核心接口隔离守卫返回 `503`。
- 外部异常只返回固定中文错误和 `request_id`；日志不写 Token、认证头、完整 URL 或原始响应。

## 7. 测试方案

新增 `test_moviepilot_backup_runtime.py`，使用临时 SQLite、假 Torra/qB、假 MoviePilot 门面和固定时钟，覆盖：

- 闸门关闭时预览和推送零外部调用。
- 订阅、TMDB、季信息缺失的 `404/422`。
- Torra 未配置、映射缺失、运行中和 qB 活动阻断。
- 已有订阅重搜和新订阅创建两条路径。
- 预览摘要脱敏，推送不返回原始响应、Token、URL 或 MoviePilot 订阅 ID。
- 幂等重放、冷却、并发冲突和上游异常映射。
- 会话认证和错误 Origin。
- 35 条 v2 机器契约、环境样例、Compose 和部署文档同步。

代码阶段不连接真实 MoviePilot、Torra、qB、Emby、115、Symedia 或 RSS。

## 8. 验收标准

- 默认 `MCC_MOVIEPILOT_BACKUP_ENABLED=false`，新接口不产生外部请求。
- 只有观察单元全部结束且 Torra/qB 预检通过时，才允许人工备用流程。
- 已有订阅只触发重搜；新订阅只通过既有创建函数创建后重搜。
- 同一幂等键不重复写入或触发搜索。
- 浏览器和日志不泄露凭据、URL、原始响应或外部订阅 ID。
- 现有 Python 全量回归、前端 typecheck/build、npm audit、Compose、变更、质量和安全关卡继续通过。
