# 家庭影音自动化维护手册

创建时间：2026-07-13  
维护对象：fnOS 上的 Torra、qBittorrent、115、CloudDrive2、Symedia、Emby、Refind 及相关 Hermes 自动化。

## 一句话架构

Torra 从 PT 站点发现并筛选资源，推送 qBittorrent 下载；秒传插件把文件送入 115 的 `00-待整理`；Symedia 识别、归档和洗版到 115 媒体库，再生成 STRM 并通知 Emby；Refind 根据 Emby 缺失和权重规则补资源，重新进入 Symedia 链路。

## 文档入口

- `MEDIA_CONTROL_CENTER.md`：面向整体使用和维护的媒体控制中心运行说明。
- `ARCHITECTURE.md`：端到端数据流、组件职责和边界。
- `TORRA.md`：订阅、识别、版本控制、权重、qB 推送和秒传。
- `SYMEDIA.md`：归档、命名、洗版、CloudDrive2、STRM 和 Emby 联动。
- `RUNBOOK.md`：日常巡检、故障定位、修改与验证方法。
- `CURRENT_STATE.md`：当前部署快照、已知问题与最近变更。
- `SOURCE_EVIDENCE.md`：源码位置、可信范围和已确认事实。

## 核心路径

| 对象 | 路径 |
|---|---|
| Torra 分析源码 | `/Users/zou/projects/torra-ctf` |
| Symedia 分析源码 | `/Users/zou/projects/symedia` |
| Hermes 工作区 | `/Users/zou/hermes-workspace` |
| fnOS 工具数据 | `/vol1/1000/tools` |
| qB/Torra 下载根目录 | `/vol02/1000-4-32d3f6a0` |
| Torra 容器数据库 | `/app/config/torra.db` |
| Symedia 宿主数据库 | `/vol1/1000/tools/symedia/config/symedia.db` |
| 115 待整理（容器视角） | `/CloudNAS/CloudDrive/115/00-待整理` |
| 115 媒体库（容器视角） | `/CloudNAS/CloudDrive/115/媒体库` |
| STRM 媒体库（容器视角） | `/CloudNAS/STRM/115/媒体库` |

## 维护目标

- 下载、秒传、归档、STRM 和 Emby 刷新均有可核验记录。
- 主秒传目录不长期积压；失败目录有上限、有重试、无无限循环。
- `/vol1` 与 `/vol02` 保持安全余量，避免“磁盘满 → 秒传失败 → 无限重试 → 115 风控”。
- 所有配置变更都能追溯到日期、原因和验证结果。
- 源码分析与运行事实分开记录，避免反编译误差污染维护判断。
