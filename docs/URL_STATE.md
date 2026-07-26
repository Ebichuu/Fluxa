# 页面地址与可分享参数

Fluxa 管理工作台把关键筛选和定位上下文写入浏览器地址，复制地址即可分享当前视图。所有地址都需要登录后访问。本文与 `src/app/navigation.ts` 及各页面的 URL 状态读写保持一致。

## 页面路径

| 页面 | 路径 |
| --- | --- |
| 首页 | `/` |
| 影院大厅 | `/hall` |
| 发现 | `/discover` |
| 追更 | `/following` |
| 追更设置 | `/following/settings` |
| 任务中心 | `/tasks` |
| 日历 | `/calendar` |
| 作品总览 | `/media/movie/:tmdbId`、`/media/tv/:tmdbId` |
| RSS 种子库 | `/rss-library` |
| 控制室 | `/control` |
| 设置 | `/settings` |

旧地址 `/overview`、`/subscriptions`、`/subscription-settings`、`/tasks-center`、`/control-room` 继续可用，会映射到对应新页面。

## 任务中心与追更（`/tasks`、`/following`）

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| `userState` | `action_required` / `in_progress` / `completed` / `no_action` | 日常四态筛选 |
| `completedDate` | `YYYY-MM-DD` | 按完成日期查看 |
| `chainId` | 公开任务链 ID | 直接展开单条任务详情 |
| `targetKey` | 目标标识 | 配合 `chainId` 定位 |
| `subscriptionId` | 订阅 ID | 定位单条追更 |
| `mediaType` | `movie` / `tv` | 媒体类型 |
| `tmdbId`、`title`、`seasonNumber` | — | 作品身份定位 |
| `advanced` | `1` | 展开高级视图 |
| `identityState` | `unidentified` / `linked` / `conflict`，可重复 | 身份状态筛选 |
| `systemIssue` | `secupload_failures` | 直接打开系统问题面板（仅任务中心） |

示例：`/tasks?userState=action_required&mediaType=movie` 打开"需要处理的电影任务"；`/tasks?systemIssue=secupload_failures` 直达秒传系统问题面板。

## 日历（`/calendar`）

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| `year`、`month` | 数字 | 月份定位 |
| `view` | `month` / `week` | 视图 |
| `type` | `movie` / `tv` | 媒体类型 |
| `status` | `upcoming` / `acquiring` / `library` / `protected` / `missing` / `unknown` | 状态筛选 |
| `q` | 关键词 | 作品搜索 |
| `date` | `YYYY-MM-DD` | 选中日期 |
| `detail` | `1` | 打开当日详情 |

示例：`/calendar?year=2026&month=7&status=missing` 查看 7 月逾期未获取。

## 发现（`/discover`）

`q`（关键词）、`source`、`type`、`trend`、`sort`、`language`、`year`、`genre`、`provider`（仅流媒体来源）、`page`。省略即为默认值。

## RSS 种子库（`/rss-library`）

`q`（关键词）、`sourceId`（来源）、`identityStatus`（`identified` / `conflict` / `unidentified`）、`window`（`1h` / `24h` / `7d` / `all`，默认 `24h`）、`offset`（分页偏移）。

## 作品总览（`/media/...`）

路径本身即完整上下文，例如 `/media/tv/202`；无查询参数。顶栏"搜索媒体"的结果、任务中心和追更卡片都会跳转到该页。
