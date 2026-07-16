# 家庭影音自动化架构

## 端到端主链路

```mermaid
flowchart LR
    A["PT 站点 / RSS"] --> B["Torra 候选收集"]
    B --> C["标题与媒体识别"]
    C --> D["版本控制过滤"]
    D --> E["权重评分与排序"]
    E --> F["qBittorrent 分类目录下载"]
    F --> G["file_event_mover 建立硬链接"]
    G --> H["Torra 秒传目录"]
    H --> I["secupload_115 秒传/回退上传"]
    I --> J["115 /00-待整理"]
    J --> K["CloudDrive2 文件变更消息"]
    K --> L["Symedia ArchiveWatcher"]
    L --> M["cd2_move 归档/洗版"]
    M --> N["115 /媒体库"]
    N --> O["Webhook 创建 STRM"]
    O --> P["Emby 媒体库"]
    P --> Q["Refind 缺失检测"]
    Q --> R["115 转存补充"]
    R --> J
```

## 控制链与数据链

### 控制链

- Torra 订阅任务决定搜索哪些站点、哪些季集、采用哪套版本控制和权重规则。
- qBittorrent 负责真实下载、文件选择、做种和删除。
- Torra 插件平台负责秒传任务的配置、计划、执行记录和重试。
- Symedia 的 `transfer_list` 控制归档，`sync_list` 控制软链接/STRM 同步。
- Emby 接收媒体库刷新；Refind依据缺失项和权重规则生成补充计划。
- Hermes cron 负责状态汇报、retry 守护、秒传成功后的安全删种和每日复盘。

### 数据链

1. PT 候选进入 Torra。
2. Torra 生成统一资源事实 `release_facts`，再做规则匹配。
3. 命中候选推送到 qBittorrent 的 `/qbdownload/torra/<分类>`，并记录订阅快照与去重状态。
4. `file_event_mover` 监听分类目录，对允许的媒体/字幕文件建立硬链接到 `/qbdownload/秒传/<分类>`；原 qB 文件继续保留做种。
5. `secupload_115` 监听硬链接目录，秒传或回退上传到 115 `/00-待整理/<分类>`；成功后删除的是硬链接入口，不是 qB 原文件。
6. CloudDrive2 产生 `create` 文件变更消息，Symedia 的 `CloudDriveMessageListener` 转发给 `ArchiveWatcher`。
7. Symedia 识别 TMDB/季集/类别，计算目标路径，以 `cd2_move` 从待整理区归档或洗版到 `/媒体库`。
8. `TransferHistory` 写入成功或失败结果。
9. 归档产生的 `rename` 事件进入 Webhook 同步，生成 `/CloudNAS/STRM/115/媒体库/.../*.strm`。
10. Emby 刷新并提供播放；Refind检查缺失后把资源重新转存到待整理区。

## 组件职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Torra | PT 搜索、订阅、候选识别、版本过滤、权重排序、推送下载、秒传 | 最终媒体库命名与 STRM 生成 |
| qBittorrent | 下载、文件优先级、做种、删除源文件 | 115 入库和媒体识别 |
| Torra 秒传 | 源文件 SHA1、115 秒传/回退上传、失败状态、重试 | Symedia 最终归档是否成功 |
| CloudDrive2 | 115 挂载、文件操作通道、消息监听 | 媒体规则判断 |
| Symedia | 识别、归档、路径命名、权重洗版、转移历史、STRM/软链接同步 | PT 种子搜索和 qB 下载队列 |
| Emby | 媒体索引和播放 | 下载与云盘归档 |
| Refind | Emby 缺失检测、补充资源计划 | Torra 的 PT 订阅队列 |
| Hermes | 监控、汇报、守护和经确认的清理 | 替代业务系统核心状态机 |

## 故障传播路径

最危险的连锁故障已经发生过一次：

```text
权重删除目录未清理
→ 磁盘满
→ 秒传写入/处理失败
→ 无重试上限或无回退上传
→ 同一文件反复请求 115
→ 账号级风控
```

维护时优先监控：磁盘、主秒传积压、失败目录、任务重复次数、115 错误码、SHA1 缓存命中率。
