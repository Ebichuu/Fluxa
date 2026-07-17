# 当前实现来源与文件映射

本文只记录 v2 当前运行代码，不保留技术迁移流水账。

## 1. 业务来源

### NasEmby

来源参考：`D:\Projects\NasEmby_friend_clean\NasEmby_friend_clean_20260630_171606`

当前纳入：

- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/app/services.py`
- `services/nasemby-core/app/config.py`
- `services/nasemby-core/app/legacy/`
- `services/nasemby-core/app/hdhive/`
- `services/nasemby-core/app/telegram_runtime.py`

订阅、发现、日历、资源规则和调度以这些 Python 业务函数为准。媒体控制中心不维护第二套订阅规则。

### Mineradio

来源参考：`D:\Mineradio\resources\app\public`

仓库副本：`vendor/mineradio-public/`

当前保留 Three.js、GSAP、封面粒子、视觉预设、3D shelf 和交互；音乐播放、登录、歌词、播客、评论、收藏和 Electron 桌面业务由桥接层拦截或不使用。

### 维护资料

原维护资料整理到：

`docs/references/media-automation-maintenance/`

这些文件只作架构与外部服务参考，不参与运行，也不作为配置导入源。

## 2. 后端能力映射

| 能力 | 当前文件 |
| --- | --- |
| Flask 装配与调度 | `app/main.py` |
| 整站认证 | `app/access_auth.py`、`app/auth_runtime.py` |
| 请求 ID 与错误 | `app/http_runtime.py` |
| React 静态托管 | `app/frontend_runtime.py` |
| Mineradio 桥接 | `app/mineradio_runtime.py`、`app/mineradio_fragments/` |
| 浏览器字段白名单 | `app/contract_mapping.py` |
| 发现兼容层 | `app/discover_compat_runtime.py` |
| 订阅兼容层 | `app/subscription_compat_runtime.py` |
| SQLite 与订阅迁移 | `app/sqlite_runtime.py`、`app/subscription_repository.py`、`app/subscription_migration.py` |
| 私人 RSS 种子库 | `app/private_rss_repository.py`、`app/private_rss_parser.py`、`app/private_rss_collector.py`、`app/private_rss_api_runtime.py` |
| Emby 读取与图片 | `app/emby_runtime.py`、`app/media_read_runtime.py` |
| Emby 证据刷新 | `app/emby_refresh_runtime.py` |
| qB 摘要 | `app/qbittorrent_runtime.py` |
| qB 暂停/恢复 | `app/qbittorrent_action_runtime.py` |
| Torra 摘要、查重和推送 | `app/torra_read_runtime.py` |
| Symedia 摘要 | `app/symedia_read_runtime.py` |
| 四步任务链 | `app/task_chain_runtime.py` |

表中 `app/` 均指 `services/nasemby-core/app/`。

## 3. 前端能力映射

| 页面 | 当前文件 |
| --- | --- |
| 应用和路由 | `src/app/App.tsx` |
| 顶部导航 | `src/components/layout/AppTopNav.tsx` |
| 影院大厅 | `src/components/media-hall/MediaHall.tsx` |
| Mineradio iframe | `src/components/media-hall/MineradioEmbed.tsx` |
| 媒体队列 | `src/components/media-hall/MediaQueuePanel.tsx` |
| 总览 | `src/components/pages/Overview.tsx` |
| 控制室 | `src/components/pages/ControlRoom.tsx` |
| 任务中心 | `src/components/pages/TasksCenter.tsx` |
| 日历 | `src/components/pages/CalendarPage.tsx` |
| 内容发现与我的订阅 | `src/components/pages/DiscoverPage.tsx` |
| 订阅设置 | `src/components/pages/SubscriptionSettingsPage.tsx` |
| 种子库 | `src/components/pages/RssSeedLibraryPage.tsx` |
| 系统设置 | `src/components/pages/SettingsPage.tsx` |
| HTTP 客户端 | `src/services/api.ts` |

## 4. 动态依赖说明

`legacy/`、`hdhive/` 和 `telegram_runtime.py` 不是公开旧页面。当前 NasEmby 发现、资源搜索、网盘获取、可选 provider 或通知函数仍可能动态调用它们，因此保留源码与依赖。

原核心管理路由和调用关系已经恢复，默认由 `MCC_PRESERVED_CORE_API_ENABLED=false` 隔离；原静态管理页不作为第二套生产页面注册，其源码作为迁移参考保留。当前 React 页面和统一 Python 安全边界已经提供配置状态、候选预览和受控单条转存接口；剩余自动执行器和实机验证见 `docs/CLOUD_ACQUISITION_PLAN.md`，逐接口映射见 `docs/CORE_API_CAPABILITY_MATRIX.md`。

## 5. 数据归属

- 订阅唯一台账：`db/media_control_center.sqlite3`。
- 旧 `discover_subscription_items.json`、`discover_subscriptions.json` 只作为一次性迁移和回滚输入。
- 私人 RSS 来源、最近种子、FTS5 索引和抓取记录与订阅台账共用同一 SQLite 文件。
- React 展示只读取 Python 白名单 DTO。
- 任务链读取同一订阅台账并关联 Torra、qB、Symedia 和 Emby 证据。
- 不导入外部 NasEmby 台账，不创建 Node 台账，不双写。

## 6. 部署来源

- 根 `Dockerfile`：Node 构建 React，Python 3.13 运行。
- 根 `docker-compose.yml`：唯一服务、8787、三个持久目录和默认写保护。
- `.env.example`：唯一环境变量模板。

`services/nasemby-core` 下不再保留第二份 Dockerfile 或环境模板。
