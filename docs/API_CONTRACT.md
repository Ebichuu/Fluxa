# 媒体控制中心 HTTP v1 契约

机器清单：`docs/contracts/http-api-contract-v1.json`  
路由数量：47  
运行实现：Python / Flask

## 1. 版本规则

当前浏览器路径保持 `/api/*`，机器清单将其定义为 v1 兼容契约。破坏性改动必须新增 `/api/v2/*` 或提供兼容期，不能直接改变现有字段、类型、状态码或认证边界。

v1 保留少量历史 HTTP 语义：部分删除和动作使用 POST、创建订阅返回 200、错误包络存在少量差异。当前不为追求形式统一而破坏 React 调用。

## 2. 认证边界

公开启动路由只有：

- `GET /healthz`
- `GET /auth/login`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /api/auth/session`

其余 42 条路由在启用访问密钥时必须通过会话认证。未登录 API 返回：

```json
{
  "error": "需要登录",
  "code": "AUTH_REQUIRED"
}
```

危险方法要求 Origin 与当前站点相同，或精确出现在 `MCC_ALLOWED_ORIGINS` 中。

## 3. 路由分组

| 分组 | 能力 |
| --- | --- |
| 启动与认证 | 健康、登录、退出、会话状态 |
| 媒体 | Emby 首页、概览、图片和刷新证据 |
| qBittorrent | 摘要、暂停、恢复 |
| Torra / Symedia | 服务摘要与任务证据 |
| 任务链 | 四步聚合状态 |
| 订阅 | 列表、详情、保存、分类、改季、配置、日历和安全推送 |
| 发现 | 浏览、趋势、搜索和资源搜索 |
| 活动 | 脱敏活动日志 |
| 内部诊断 | 同进程 NasEmby 只读诊断 |
| 影院大厅 | Mineradio 嵌入页 |

完整方法、路径、认证、读写属性、成功状态和响应类型以机器清单为准。

## 4. 请求约束

| 路由 | 主要请求字段 |
| --- | --- |
| `POST /auth/login` | 表单 `access_key`、可选 `next`，正文不超过 2 KiB |
| `GET /api/media/home` | 可选 `libraryId` |
| `POST /api/media/emby/refresh` | 无正文，必须有较新 Symedia 证据 |
| `POST /api/qbittorrent/actions/:action` | `hashes`、`taskId`、`title`，最多 20 个 hash |
| `GET /api/subscriptions/items` | 可选 `include_progress=1` |
| `POST /api/subscriptions/save` | 标题、TMDB ID、媒体类型和可选元数据 |
| `PATCH /api/subscriptions/:id/category` | 八分类 key 或 `null` |
| `GET /api/subscriptions/detail` | 必填 `id`，可选 `season` |
| `GET /api/subscriptions/calendar` | `year`、`month`、`type` |
| `GET /api/discover/browse` | 来源、类型、排序、语言、年份、风格、provider 和分页 |
| `GET /api/discover/search` | `query`、可选 `page` |
| `GET /api/discover/resources/search` | 标题，可选类型、年份、TMDB ID 和来源 |

## 5. 响应与字段边界

公开订阅、详情、日历、发现和资源响应通过 `contract_mapping.py` 白名单映射。浏览器不会收到原始上游包络、未知内部字段、Cookie、Token 或异常正文。

内部诊断路由仍受会话保护，只用于核对同一 Python 进程中的 NasEmby 数据，不表示存在第二个服务。

集合边界：

- 发现和资源搜索使用分页或固定上限。
- 活动日志最多返回 1000 条。
- 订阅列表沿用 v1 全量契约，未来增加分页必须通过兼容版本完成。

## 6. 状态码

- `200`：普通读取或已完成动作。
- `202`：Emby 已接受刷新，或 qB 动作已接受但尚未完全确认。
- `303`：登录和退出跳转。
- `400`：输入格式错误。
- `401`：未登录。
- `403`：Origin 或写闸门拒绝。
- `404`：资源不存在，或未注册的旧静态页面路径。
- `409`：状态冲突、并发锁或冷却。
- `429`：登录限流。
- `502`：上游失败。
- `503`：依赖未配置、离线，或已保留的核心兼容接口尚未安全接入。

未捕获异常返回脱敏的 `500 / INTERNAL_ERROR` 与请求 ID。

## 7. 已保留但默认关闭的核心入口

原 115、Telegram、HDHive、provider、缓存预热和 NasEmby 配置接口仍保留在源码与 Flask URL map 中，但不属于当前 47 条 React v1 契约。默认调用返回 `503 PRESERVED_CORE_API_DISABLED`；只允许在模拟测试中通过 `MCC_PRESERVED_CORE_API_ENABLED=true` 开启。

NasEmby 原静态页面源码作为迁移参考保留，但不注册为第二套生产页面，因此 `/static/app.js` 仍返回 404。逐接口用途和副作用见 `docs/CORE_API_CAPABILITY_MATRIX.md`。

保留接口的守卫顺序固定为：未登录先返回 401；已登录但错误 Origin 的危险方法返回 403；通过认证和 Origin 后，在总开关关闭时返回 503。总开关只用于模拟兼容测试，不能代替后续每组动作的细分写闸门。

`POST /api/subscriptions/import-nasemby` 仅为冻结路径兼容，生产不导入外部台账，调用返回明确的 404 禁用结果。

## 8. 自动验证

- 47 条方法与路径逐条存在。
- 42 条受保护路由未登录时逐条返回 401。
- 所有受保护写路由拒绝错误 Origin。
- React API 引用必须命中 `client=true` 契约。
- 所有 GET 只能读取，不改变订阅、下载器或外部服务状态。
- 保留核心接口默认返回 503，旧静态页仍保持 404。

## 9. 待新增的网盘契约

当前 47 条 v1 契约不代表网盘订阅已经完成。115、Telegram、HDHive / pansou、Symedia 的统一配置状态、候选预览、单条转存和兜底状态接口仍需单独设计并加入机器契约。

旧 NasEmby 核心路由实现继续保留，但不会在生产整体开启。新接口必须经过会话认证、Origin、防泄漏、细分写闸门、幂等和服务端复查，并与原业务函数建立明确映射。详细范围见 `docs/CLOUD_ACQUISITION_PLAN.md`。
