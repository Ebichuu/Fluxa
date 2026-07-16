# Torra 运行机制

## 定位

Torra 是上游资源决策器：把 PT/RSS 候选转换成可比较的资源事实，按订阅目标筛选季集，再套版本控制和权重规则，最后推送到 qBittorrent。下载完成后的 115 秒传由 Torra 插件平台管理。

## 订阅主链路

源码确认的模块分层：

1. `services/subscription/source.py`：从站点、RSS 和 RSS Cloud 取得候选，处理标题别名、年份、动漫判断、季集冲突和站点失败缓存。
2. `services/release/facts_builder.py`：把候选字典统一收敛为 `release_facts`。
3. `services/subscription/rule_pipeline.py`：
   - `SubscriptionMatcher`：订阅身份匹配；
   - `VersionEvaluator`：版本规则命中；
   - `MetaWeightScorer`：权重评分；
   - `PrioritySelector`：选择最高优先级版本桶；
   - `ResultSorter`：最终排序。
4. `services/subscription/selection.py`：解析季集、整季包和种子文件列表，排除本地已存在集数，构建下载文件选择与推送快照。
5. `services/subscription/downloader.py`：安全推送到下载器并回写快照、去重和错误。
6. `stores/subscription_store.py` 与 `subscription_runtime_store.py`：保存订阅定义、运行状态、已下载 URL/目标和去重键。

## 统一资源事实

`ReleaseFactsBuilder` 会收集并规范化：

- 标题、别名、年份、季集、是否完结；
- 分辨率、视频编码、音频编码、色深、帧率、动态范围、杜比视界；
- 资源类型、发布类型、流媒体来源、发行组、文件扩展名；
- 体积、做种人数、免费状态、发布时间；
- “官组、中字、国语、粤语、DIY、官字组、完结、禁转”等语义标签。

事实字段带来源概念：识别结果、结构化标题或候选原始数据。维护规则时不能假定每个站点都能提供全部字段。

## 版本控制与权重

版本控制支持的字段包括：

`title`、`releaseGroup`、`videoFormat`、`audioCodec`、`videoCodec`、`color_depth`、`frame_rate`、`dolby_vision`、`dynamic_range`、`enhancement`、`resource_type`、`release_type`、`streaming_service`、`file_extension`、`free`、`file_size_range`、`seeders`、`publish_minutes`、`resource_labels`。

处理顺序是“先进入版本桶，再在入围候选中按权重排序”。规则维护要点：

- 文件名正则统一使用 `(?i)`，避免 PT 标题大小写差异。
- 本地库洗版会忽略 `free`、`seeders`、`publish_minutes` 等只属于源站候选的字段。
- 负向淘汰使用极低哨兵分值；排除条件与普通低分不是同一含义。
- `releaseGroup` 只是资源事实的一部分，不能单靠组名区分日漫与国漫。

## qBittorrent 推送

`QbittorrentDownloader` 负责：

- 登录与连接恢复；
- 添加 torrent/magnet；
- 解析刚添加的 torrent hash；
- 多次重试读取种子文件列表；
- 按季集选择文件优先级；
- 启停、强制开始、校验、限速和删除。

当前部署下载器：

- 名称：`qb`
- 地址：`http://192.168.50.50:8080`
- 下载目录：`/vol02/1000-4-32d3f6a0/torra`

## 秒传插件

### 下载目录进入秒传目录

实际运行配置确认，中间由独立插件 `file_event_mover` 接力：

```text
qB 下载完成：/qbdownload/torra/<分类>/<种子目录>/<文件>
        ↓ file_event_mover，move_method=hardlink
秒传入口：/qbdownload/秒传/<分类>/<种子目录>/<文件>
```

它不是移动原文件，而是建立硬链接。因此：

- qB 原文件继续留在 `/torra/<分类>` 做种；
- 秒传插件处理 `/秒传/<分类>` 中的另一个目录项；
- 秒传成功后 `delete_by_success=true` 删除硬链接入口，qB 原文件仍存在；
- 后续删种由独立的 `torrent_remover` 和 Hermes 安全清理任务控制。

`file_event_mover` 当前配置为事件监听，`observer_enabled=true`、`scan_on_start=false`。它只处理配置允许的媒体和字幕扩展名。

运行数据库确认的关键表：

| 表 | 作用 |
|---|---|
| `plugin_config_items` | 每个媒体分类的源目录、失败目录、115 目标目录和策略 |
| `plugin_task_schedules` | `retry_pending` 的 cron、启用状态和最后执行时间 |
| `plugin_task_runs` | 每轮任务的状态、消息、进度和结果 |
| `secupload_sha1_cache` | 文件 SHA1 缓存；TTL 从源码可见为 86400 秒 |
| `rss_cloud_upload_fingerprints` | RSS Cloud 上传去重指纹，每站上限 5000 |

当前分类使用的目录模式：

```text
源：/qbdownload/秒传/<分类>
失败：/qbdownload/秒传失败/<分类>
目标：/00-待整理/<分类>
```

当前策略包含：

- `fallback_upload_after_failures = 3`
- `scan_on_start = true`
- `delete_by_success = true`
- `retry_pending` 每 8 小时运行一次

### 当前分类入口差异

2026-07-13 实时配置：

- 8 个分类 mover 均已启用：`00-日漫`、`01-国漫`、`02-国产剧`、`03-日韩剧`、`04-欧美剧`、`05-港台剧`、`06-综艺`、`10-电影`。
- `02-国产剧` 于 03:22 新增并启用，路径为 `/qbdownload/torra/02-国产剧` → `/qbdownload/秒传/02-国产剧`。
- `06-综艺` 于 03:20 启用，路径为 `/qbdownload/torra/06-综艺` → `/qbdownload/秒传/06-综艺`。
- 两项均为 `hardlink`，`observer_enabled=true`。

当前所有分类的“qB 分类目录 → 秒传目录 → 115 待整理 → Symedia 归档”接力配置已经对齐。

### 秒传目录清理边界

空目录清理不能把 `/qbdownload/秒传` 作为扫描根。`empty_folder_cleaner` 自底向上处理扫描根的子目录；总目录作为根时，空分类目录也是待删除对象，分类入口消失后对应秒传 watcher 无法启动。

2026-07-13 起采用固定结构：

```text
清理规则根：/qbdownload/秒传/<分类>
允许删除：  /qbdownload/秒传/<分类>/<空资源目录>
永久保留：  /qbdownload/秒传/<分类>
```

8 个分类分别配置清理规则，计划每 12 小时错峰执行。原 `link空目录` 总目录规则已停用并保留作回滚证据。qB 下载、主秒传和秒传失败目录的 8 个分类根必须在 Torra 启动前存在，才能保证 mover 和 `secupload_115` watcher 完整接管。

### 秒传内部执行顺序

此前 `source/decrypted/Torra/plugins/secupload_115/` 的导出文件确实错配成了 `site_cookie_login`，但 `archive/Torra/plugins/secupload_115/` 保存了正确的独立静态提取结果。结合 DAS 常量表，完整顺序已经还原：

1. 视频先查内存 SHA1 缓存，再查持久化缓存，最后才读取文件计算 SHA1。
2. 调用 `p115client.P115Client.upload_file_init()` 尝试秒传。
3. 秒传失败后，用 `rename()` 把秒传侧目录项移到失败目录，并创建 `attempts=1` 的待处理记录。
4. 达到 `fallback_upload_after_failures` 阈值时，在同一轮立即调用 `P115Client.upload_file()` 原始上传；未达到则等待下一次 `retry_pending`。
5. 秒传或原始上传确认成功后，才执行 `delete_by_success` 对应的 `unlink()`。
6. 重试成功后删除待处理记录；失败则保留文件和状态。

这里的失败目录“移动”不会伤到 qB 原文件：进入秒传目录前已经由 `file_event_mover` 建立硬链接，后续 `rename/unlink` 只作用于秒传侧目录项。

详细证据见 `/Users/zou/projects/torra-ctf/reports/secupload_115_execution_order.md`。

## 订阅性能特征

当前订阅批处理是串行长队列。历史运行中 185 条订阅一轮约 31 小时，单个慢站点会拖慢整轮。判断“种子发布后为何晚命中”时，要先看订阅排队位置和站点耗时，不能直接归因于 RSS 失效。

2026-07-13 已关闭全局多名称搜索，实时数据库值为 `settings.search_multiple_name = 0`。订阅聚合搜索不再为中文名、英文名和别名分别追加站点搜索，有助于缩短串行批处理并减少重复请求。

豆瓣榜单插件会按历史记录避免重复创建。如果历史记录引用的 `subscription_id` 已被删除，旧实现仍可能按“插件以前添加过”跳过。2026-07-13 已清理当前榜单命中的 37 条失效引用并重跑：17 条恢复创建，20 条被 Emby 完整入库检查安全拦截。后续判断榜单未自动订阅时，需要同时核对插件历史记录和实际 `subscriptions` 表，不能只看历史状态。

## 豆瓣榜单订阅分类路径

豆瓣榜单插件的全局 `save_path` 是 qB 下载总目录，`resolved_category` 只记录识别出的媒体分类，插件本身不会把两者拼成分类子目录；qB 下载器同时关闭了 `auto_category`，因此这类订阅过去会直接写入 `/torra` 根目录，绕过 8 个分类 `file_event_mover`。

2026-07-13 已在 Torra 数据库增加持久化分类保护，只作用于 `douban_rank_subscriber_records.status = 'subscribed'` 且下载路径仍为总目录、旧容器路径或空值的订阅。普通手工订阅和已经指定自定义目录的订阅不受影响。映射如下：

| 豆瓣解析分类 | qB 下载分类 |
|---|---|
| 日番 | `00-日漫` |
| 国番、国产动画 | `01-国漫` |
| 国产剧 | `02-国产剧` |
| 日韩剧、南亚剧 | `03-日韩剧` |
| 欧美剧、欧美动画 | `04-欧美剧` |
| 港台剧 | `05-港台剧` |
| 综艺 | `06-综艺` |
| 全部电影 | `10-电影` |

数据库共有四个保护触发器：两个在豆瓣榜单历史新增或更新后修正对应订阅，另两个在订阅自身被新增或写回总目录时再次校正。后者用于防止运行中的旧订阅对象把错误路径重新写回。触发器名称：

```text
douban_rank_subscription_path_after_insert
douban_rank_subscription_path_after_update
douban_subscription_path_guard_after_insert
douban_subscription_path_guard_after_update
```

修改既有订阅后必须等待正在运行的秒传任务结束，再重载 Torra，使订阅调度器丢弃旧的内存对象。qB 中已完成的任务不能直接移动文件，应通过 qB API 修改保存位置；这样种子继续做种，同时新位置的 `file_event_mover` 会把媒体接入秒传目录。
