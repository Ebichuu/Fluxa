# 源码证据与可信范围

## 源码位置

### Torra

- 根目录：`/Users/zou/projects/torra-ctf`
- 加密源码：`source/encrypted/Torra`
- 静态提取：`source/decrypted/Torra`
- 配置与运行导出：`reports`、`references`

关键文件：

- `services/subscription/source.py.1shot.cdc.py`
- `services/subscription/rule_pipeline.py.1shot.cdc.py`
- `services/subscription/selection.py.1shot.cdc.py`
- `services/subscription/downloader.py.1shot.cdc.py`
- `services/release/facts_builder.py.1shot.cdc.py`
- `services/meta_weight/calculator.py.1shot.cdc.py`
- `services/version_control.py.1shot.cdc.py`
- `services/pt/downloaders/qbittorrent.py.1shot.cdc.py`
- `stores/subscription_store.py.1shot.cdc.py`
- `stores/subscription_runtime_store.py.1shot.cdc.py`
- `stores/secupload_sha1_cache_store.py.1shot.cdc.py`
- `archive/Torra/plugins/secupload_115/runtime.py.cdc.py`
- `archive/Torra/plugins/secupload_115/runtime.py.1shot.das`
- `reports/secupload_115_execution_order.md`

### Symedia

- 根目录：`/Users/zou/projects/symedia`
- 静态提取：`source/decrypted`
- 旧分析：`docs`

关键文件：

- `__main__.py`
- `service_manager.py`
- `apps/Archive/archive.py`
- `apps/Archive/ArchiveTaskManager.py`
- `apps/Archive/archive_stages.py`
- `apps/Transfer/metadata_recognition.py`
- `apps/Transfer/transfer_execution.py`
- `apps/AutoSymlink/autosymlink.py`
- `apps/AutoSymlink/FileCreator.py`
- `apps/CloudDrive2Client/client.py`
- `models/TransferHistory.py`
- `models/SyncHistory.py`
- `models/MetaWeight.py`
- `source/decrypted/utils/meta_weight.py.1shot.das`
- `source/decrypted/utils/meta_weight.py.1shot.bcc.linux-x64.elf`
- `source/decrypted/apps/Transfer/MetaCover.py.1shot.das`
- `source/decrypted/apps/Transfer/MetaCover.py.1shot.bcc.linux-x64.elf`
- `reports/meta_weight_decision_rules.md`

## 已由源码确认

- Torra 的候选处理分为来源、统一事实、订阅匹配、版本评估、权重评分、优先级选择、种子文件选择和下载器推送。
- Torra 权重/版本控制支持的主要属性集合。
- qBittorrent 适配器具有添加、确认 hash、读取文件、选择优先级、启停、删除与校验能力。
- Torra 使用订阅运行状态、推送快照和去重存储。
- Symedia 启动时注册应用服务组与插件服务组。
- Symedia 的归档和同步分别由 `ArchiveTaskManager` 与 `SyncTaskManager` 管理。
- `TransferHistory`、`SyncHistory` 和 `MetaWeightModel` 的字段结构。
- Symedia 通过 CloudDrive2 客户端、文件事件监听和 AutoSymlink 连接 115 与 STRM。
- Torra 秒传插件通过 `p115client.upload_file_init()` 秒传、`upload_file()` 原始上传；失败入口使用 `rename()`，成功清理使用 `unlink()`。
- Torra SHA1 查询顺序是进程内缓存、持久化缓存、重新计算。
- Symedia 电影和剧集都使用严格 `source_score > dest_score`，同分不覆盖。
- Symedia 的 `-1` 规则对源文件转换为 `-99999`，对目标文件保持 `-1`；强制覆盖只给源文件加分。

## 已由运行状态确认

- 当前端口、容器、数据库位置和实际目录映射。
- Torra 秒传分类配置、计划任务、运行结果和目录数量。
- 当前 Emby、Symedia、Torra、Refind 的仪表盘数据。
- `02-国产剧` 失败目录已修正并写入运行数据库。
- `file_event_mover` 通过 `hardlink` 将 qB 分类目录接到秒传分类目录。
- `02-国产剧` mover 已新增，`06-综艺` mover 已启用；两项均经运行数据库验证。
- Symedia 8 个 `transfer_list` 分类均启用，使用 `cd2_move` 从 `00-待整理` 归档到媒体库，并打开 `symlink_sync_switch`。
- 单文件日志已串联 CloudDrive2 `create`、Symedia 归档、`rename` 事件、STRM 创建和 `TransferHistory` 成功记录。

## 未完全确认

- 与本次两项无关的其他 BCC native 函数内部异常处理。
- `p115client` 库内部如何组装 115 官方请求；Torra 对它的调用参数和前后顺序已经确认。
- `file_event_mover` 内部 BCC native 的异常恢复细节；当前触发方式和硬链接行为已由运行配置确认。
- Symedia 每次文件事件到 Emby 刷新的精确时序，需用单文件追踪实验验证。

## 使用规则

当源码摘要与实机结果冲突时，以实机结果为准，并在本文追加冲突记录。涉及删除、移动或规则改写时，必须先获得可回滚快照。
