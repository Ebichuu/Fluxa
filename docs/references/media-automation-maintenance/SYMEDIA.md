# Symedia（SA）运行机制

## 定位

Symedia 是下游媒体整理器：从 CloudDrive2 暴露的 115 路径接收文件，完成媒体识别、目标路径计算、归档/洗版、历史记录、STRM/软链接生成和 Emby 联动。

## 启动与服务管理

`__main__.py` 初始化数据库和配置后启动 FastAPI；生命周期中由 `service_manager.start_all()` 启动应用服务和插件服务。

与家庭影音链路直接相关的常驻服务：

- `archive_watcher`：监控并提交归档任务；
- `archive_scheduler`：按 `transfer_list` 定时归档；
- `sync_scheduler`：按 `sync_list` 定时同步；
- `webhook_watcher` / `observer_watcher`：接收媒体目录变化；
- `transfer_observer_watcher`：监听归档过程；
- `cloud_drive_message_listener`：监听 CloudDrive2 文件事件；
- `secupload115_plugin`：SA 自带的 115 秒传能力；当前家庭链路的 PT 秒传主流程由 Torra 插件承担。

服务状态聚合支持 `running`、`degraded`、`failed` 等状态。容器在线不等于所有子服务健康，排障要同时看服务组状态和任务日志。

## 归档链路

当前 8 个分类的 `transfer_list` 均启用，源目录是 `/CloudNAS/CloudDrive/115/00-待整理/<分类>`，目标统一为 `/CloudNAS/CloudDrive/115/媒体库`；`transfer_type=cd2_move`、`symlink_sync_switch=true`。定时扫描开关关闭，主触发方式是 CloudDrive2 文件变更消息。

1. Torra 秒传在 115 `/00-待整理/<分类>` 创建文件。
2. `CloudDriveMessageListener` 收到 CloudDrive2 `create` 事件并形成 `FileSystemNotification`。
3. `ArchiveWatcher` 根据 `transfer_list` 找到对应分类，产生归档任务。
4. `ArchiveTaskManager` 使用队列串接 `Archive` 处理器。
5. `Archive.do_transfer()` 对候选执行历史跳过判断、运行时防重、媒体识别、路径计算和文件转移。
6. `apps/Transfer/metadata_recognition.py` 处理 TMDB、电影/剧集、季集和附加信息。
7. `apps/Transfer/naming_context.py` 与 `Naming/` 生成规范化目录和文件名。
8. `apps/Transfer/transfer_execution.py` 通过 CloudDrive2 执行 `cd2_move`。
9. 成功和失败分别写入 `TransferHistoryStore.add_success()` / `add_fail()`。
10. `cd2_move` 产生从待整理到媒体库的 `rename` 事件；归档监控把事件送给 Webhook 同步。
11. Webhook 在 `/CloudNAS/STRM/115/媒体库` 创建对应 STRM。

`ArchiveRuntimeGuard` 用来避免同一进程重复处理同一文件；历史记录还会防止已处理文件被重复归档。

## 权重洗版

SA 的权重模型保存在 SQLite `meta_weight` 表，核心配置是 JSON。运行记录可在 `TransferHistory.extra_info` 中保存 `meta_cover_detail`，包含源版本、目标版本及分项得分。

源码中可见的主要评分项：

- 分辨率、音频编码、视频编码；
- 动态范围/杜比视界；
- 流媒体来源；
- 自定义属性；
- 发行组；
- 文件扩展名；
- 文件大小；
- 强制覆盖/负向淘汰。

维护规则时要区分：识别不到、分数为零、命中排除、负向哨兵分。四种情况处理含义不同。

反编译 BCC 原生函数后已确认完整覆盖条件：

- 电影和剧集均为严格 `source_score > lowest_destination_score`；同分不会覆盖。
- 多个目标候选时先选择分数最低的目标进行比较；版本控制开启时只比较同版本候选。
- “总是覆盖”不是绕过比较，而是只给源文件加入 `99999 × weight` 的大分；目标评分前会强制把该项权重设为 0。
- 规则分数正好为 `-1` 时，源文件转换为 `-99999`，目标文件仍为 `-1`；其他负数没有这个特殊转换。
- 路径黑名单命中，或配置白名单但未命中时，只把对应评分项置 0。

权重保持正数非常重要：若给 `-1` 排除项配置负权重，极低哨兵会反向变成巨额正分。详细证据见 `/Users/zou/projects/symedia/reports/meta_weight_decision_rules.md`。

## STRM/软链接同步

`AutoSymlink` 根据 `sync_list` 生成 `SyncConfig`，创建 `StrmUtils` 与云盘状态检查器，然后由 `FileCreator` / `FileChecker` 执行。

主要步骤：

1. 校验媒体目录和排除词；
2. 检查云盘状态；
3. 非“常规同步”模式可先导出 115/123/Star 树；
4. 扫描源媒体文件；
5. 创建 STRM 或软链接，并复制需要的元数据；
6. 写入 `SyncHistory` 的 `symlink_count`、`strm_count` 和日期；
7. 通过消息/媒体通知触发后续 Emby 更新。

当前主配置：

```text
media_dir   = /CloudNAS/CloudDrive/115/媒体库
symlink_dir = /CloudNAS/STRM/115/媒体库
observer_enabled = true
observer_mode = Webhook
```

另有精品合集、电影原盘合集、美剧合集和动漫合集等独立同步映射。

## 单文件实证

2026-07-13 使用一个日漫文件串联到以下时间线：

```text
00:50:28  qB 原文件出现在 /torra/00-日漫
00:51:12  CloudDrive2 报告 /115/00-待整理/00-日漫 创建文件
00:51:15  Symedia 开始媒体归档
00:51:18  cd2_move 到 /115/媒体库/动漫/日番/... 成功
00:51:21  Webhook 成功创建对应 STRM
```

从 qB 文件落地到 STRM 创建约 53 秒；从 115 文件创建到 STRM 创建约 9 秒。该文件同时在 `TransferHistory` 中记录为成功。

## CloudDrive2

`CloudDriveClient` 是 gRPC 客户端封装，负责登录、目录查询、文件操作和挂载管理。`CloudDriveMessageListener` 把 CloudDrive2 的文件事件传给 Symedia 监控链路。

容器内 `/CloudNAS/CloudDrive/115/...` 是 CloudDrive2 映射路径，不等于 fnOS 的真实本地磁盘路径。任何宿主文件操作都必须先实机确认。

## 关键数据库表

| 表/模型 | 关键字段 | 用途 |
|---|---|---|
| `TransferHistory` | `src`、`dest`、`mode`、`rule_id`、`type`、`category`、`tmdbid`、`season`、`episode`、`status`、`errmsg`、`file_size`、`extra_info` | 归档成功/失败与洗版证据 |
| `SyncHistory` | `symlink_count`、`strm_count`、`date` | 每次同步产出 |
| `MetaWeightModel` | `id`、`config`、`created_at`、`updated_at` | 权重规则 |

## 源码可信边界

- 类名、函数签名、模型字段、导入关系和服务注册表可信度较高。
- 权重洗版的比较操作符、同分行为、`-1` 哨兵和强制覆盖分支已经通过 BCC ELF 注册表与原生指令还原。
- 其他仍标记为 `pass  # BCC native` 的函数体，具体分支继续以日志、数据库或运行实验验证。
- 旧 `docs/` 中的伪代码是分析摘要，不保证参数顺序和控制流完全正确。
