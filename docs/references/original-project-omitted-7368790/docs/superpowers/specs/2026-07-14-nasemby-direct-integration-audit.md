# NasEmby 直接集成偏差审计

状态：审计完成，已确认采用源码合并；禁止 iframe 嵌入
日期：2026-07-14
源码基线：`D:\Projects\NasEmby_friend_clean\NasEmby_friend_clean_20260630_171606`

## 1. 审计结论

NasEmby 不是只能参考的旧项目，而是已经能够独立运行的订阅、发现和资源网关应用。它同时包含 Flask API、Python 业务逻辑、订阅调度器、数据文件和完整前端交互。媒体控制中心当前又实现了一套 TypeScript 订阅存储、自动订阅、发现来源、日历、详情和 Torra 推送，形成了两个可能同时写数据和执行任务的业务源。

后续应停止扩展中控内的重复实现。NasEmby 作为唯一订阅与发现业务源码和数据语义，直接合并进媒体控制中心；用户只看到中控的一套 React 页面、顶部导航和操作入口，不使用 iframe、外部跳转或独立 NasEmby 页面。

## 2. NasEmby 可直接复用的运行边界

NasEmby 已提供独立 Docker 服务：

- Python 3.13，入口 `python -m app.main`。
- 默认端口 `12388`。
- 持久目录：`data/`、`db/`、`upload/`。
- 运行配置写入 `data/user.env`。
- 订阅配置：`db/discover_subscriptions.json`。
- 订阅条目：`db/discover_subscription_items.json`。
- 订阅详情缓存：`db/discover_subscription_detail_cache.json`。
- HDHive 安装绑定、Telegram 会话和相关缓存继续由 NasEmby 自己管理。

NasEmby 启动后自行运行：

- HDHive 签到调度。
- 发现页缓存预热。
- 自动订阅定时任务。
- 订阅搜索轮询。
- 频道模式轮询。

因此，接入后中控的 `AutoSubscribeRunner` 不能继续同时运行。

## 3. API 对照

| 能力 | NasEmby 原 API | 当前中控 | 结论 |
|---|---|---|---|
| TMDB 发现 | `GET /api/discover/tmdb` | `GET /api/discover/browse` | 改为代理原 API |
| 豆瓣 | `GET /api/discover/douban` | `browse?source=douban` | 停止 Node 重写 |
| 平台热更 | `GET /api/discover/platform-hot` | `browse?source=tencent/...` | 停止 Node 重写 |
| 全球日播 | `GET /api/discover/daily-airing` | `browse?source=daily` | 停止 Node 重写 |
| 综合搜索 | `GET /api/discover/search` | `GET /api/discover/search` | 使用 NasEmby 响应契约 |
| 资源搜索 | `GET /api/discover/resources/search` | 尚未实现 | 直接复用 NasEmby |
| 资源预览 | `POST /api/discover/resources/preview` | 尚未实现 | 直接复用 NasEmby |
| 发现缓存 | `cache/status`、`cache/preload` | 尚未实现 | 保留 NasEmby 调度 |
| 订阅配置 | `GET/POST /api/subscriptions/config` | 同名但独立实现 | NasEmby 唯一写入 |
| 订阅列表 | `GET /api/subscriptions/items` | 同名但独立 JSON | 任务链改读 NasEmby |
| 订阅详情 | `GET /api/subscriptions/detail` | TypeScript 简化版 | 直接复用完整详情 |
| 订阅日历 | `GET /api/subscriptions/calendar` | TypeScript 简化版 | 使用 NasEmby 日历 |
| 自动执行 | `POST /api/subscriptions/run` | `AutoSubscribeRunner` | 停用中控调度器 |
| 日播同步 | `POST /api/subscriptions/daily-airing/sync` | 尚未实现 | 直接复用 NasEmby |
| 保存/删除/屏蔽 | `/save`、`/delete`、`/block`、`/unblock`、`/clear` | 存在重复实现 | NasEmby 唯一写入 |
| 活动日志 | `/api/activity/logs|clear|event` | 中控有独立日志 | 两类日志分工，订阅日志来自 NasEmby |
| Torra 推送 | `POST /api/torra/subscribe` | `POST /api/subscriptions/push` | 使用 NasEmby 原行为 |

## 4. Torra 行为差异

NasEmby 原逻辑：

1. 读取 Torra 全部订阅。
2. 优先用 TMDB ID、媒体类型和季号查重；缺少 TMDB 时使用标题、类型和年份。
3. 已存在且 `skip_existing=true` 时跳过。
4. 手动推送默认 `skip_existing=false`：合并已有订阅字段，重新保存，再触发 `/subscriptions/run/{id}`。
5. 新订阅保存成功后同样触发搜索。
6. 保存、跳过、搜索成功和失败均写活动日志。

当前中控已做过一处安全偏离：已有订阅时不重新保存，只触发已有订阅搜索。按最新决定，此偏离不再作为新业务规则继续扩展；最终行为以 NasEmby 源码为准，若原逻辑存在 bug，应在 NasEmby 中修复。

## 5. 发现与订阅界面对照

NasEmby 原页面已经包含：

- 全球日播、TMDB、豆瓣、腾讯、优酷、爱奇艺、芒果来源页签。
- 搜索、类型和多组筛选、分页、海报卡片和订阅状态。
- 资源搜索与资源预览面板。
- 电影订阅、电视剧订阅、订阅日历、被屏蔽订阅四个页签。
- 状态、更新时间、年份和关键词筛选。
- 订阅详情、演职员、季集、入库路径和资源搜索。
- 推送 MoviePilot、Torra、Symedia，屏蔽、删除和复制标题。

当前中控发现页已实现统一 Mineradio 工作页视觉、TMDB/豆瓣/平台/日播浏览、JustWatch 扩展和简化订阅侧栏，但缺少 NasEmby 的资源搜索、资源预览、完整订阅筛选、缓存管理以及部分详情和操作。继续逐项补写会重复 NasEmby 前端逻辑。

## 6. 中控可继续保留的能力

以下能力不属于 NasEmby 重复实现，可继续保留：

- 影院大厅及 Mineradio 视觉。
- 顶部导航和媒体队列。
- Emby、Torra、qB、Symedia 控制室摘要。
- qB 暂停和恢复。
- Emby 证据驱动刷新。
- 统一任务链，但订阅主干数据改为读取 NasEmby。
- JustWatch 海外流媒体必须保留。该功能应从中控当前的 TMDB watch-provider 实现迁入 NasEmby 的发现运行时、API 和原发现页，成为 NasEmby 的正式来源；加入订阅时继续写入 NasEmby 唯一台账。

## 7. 必须停止或迁移的中控模块

切换完成后应停止作为业务源：

- `server/services/subscriptionStore.ts`
- `server/services/subscriptionConfigStore.ts`
- `server/services/autoSubscribeRunner.ts`
- `server/services/discoverSourceService.ts` 中与 NasEmby 重复的来源实现
- `server/services/doubanSource.ts`
- `server/services/subscriptionPush.ts`
- `server/routes/subscriptionRoutes.ts` 中的本地写入逻辑
- `server/routes/discoverRoutes.ts` 中的重复发现逻辑

这些文件暂不删除。先完成代理、数据备份、只读对照和任务链切换，再按引用关系逐步退出。

## 8. 安全闸门

- 当前阶段不连接真实写接口做验收。
- NasEmby 的 `ENV_TORRA_AUTO_SUBSCRIBE=0` 保持关闭。
- 中控的 `TORRA_PUSH_ENABLED=false` 保持关闭，直到旧入口退出。
- 自动云盘兜底继续关闭。
- 先使用 NasEmby 数据目录副本或模拟响应做接入测试。
- 不把 NasEmby、Torra、Telegram、115、HDHive、Symedia 凭据返回前端或写入仓库。

## 9. 已确认的合并方式

- 不使用 iframe，不保留用户可见的独立 NasEmby 页面，也不把发现或订阅入口跳转到 NasEmby。
- NasEmby 的 Python 后端源码、数据文件、调度器和 API 语义作为中控内部模块纳入同一项目与 Docker Compose。
- 中控 React 页面承接发现、订阅、日历、资源搜索和配置内容；实现时逐项依据 NasEmby 的 `templates/index.html`、`static/app.js` 和 API 响应，不重新设计业务规则。
- 当前 TypeScript 重复实现按“先切读、再切写、最后停调度”的顺序退出，不能一次性删除。
- 影院大厅、媒体队列和顶部导航保持现状；发现和日历入口仍属于媒体控制中心原导航。

附加确定项：海外流媒体 / JustWatch 来源必须保留。它是合并后 NasEmby Core 的明确功能补丁，不继续留作平行的 Node 发现服务；现有 US 区平台和已核对 provider ID 作为迁移基线。
