# PT 优先与 RSS 种子搜索设计

日期：2026-07-21

## 1. 目标流程

Fluxa 的获取主线固定为：

```text
发现内容 → 本地订阅台账 → Torra → qBittorrent → Torra 秒传 115 → Symedia → Emby
```

发现页或订阅详情中的“搜索资源”只查询 Fluxa 已采集的本地私人 RSS 种子箱，不访问 115、Telegram、HDHive、MoviePilot 或其他云盘搜索服务，也不触发下载、转存或外部写入。

## 2. 交互

点击发现结果或订阅详情中的资源搜索后，沿用当前详情弹窗，在弹窗内展示 RSS 搜索结果。搜索词使用当前媒体标题，可显示来源、发布时间、大小、季集信息和可下载标记；无结果时保留清晰的空状态。

查询通过现有 `GET /api/v2/rss-items` 完成，使用本地 SQLite FTS/筛选能力。详情弹窗不调用旧的 `GET /api/discover/resources/search` 云盘兼容接口。

## 3. 与 PT 主线的边界

- Torra 是唯一 PT 获取入口；“允许向 Torra 创建订阅”关闭时只保存本地订阅。
- RSS 搜索是证据和候选查看，不改变订阅状态，也不代替 Torra 推送。
- 115、Telegram、HDHive 和 MoviePilot 继续作为延期或独立受控能力，不属于本次搜索路径。

## 4. 测试

- 资源搜索使用本地 RSS 查询接口并正确映射结果。
- 无 RSS 结果时显示空状态。
- 搜索过程不调用云盘/外部资源接口。
- Torra 推送开关关闭时不排队后台推送。
- 保持现有 TypeScript 检查和 Python 回归测试通过。
