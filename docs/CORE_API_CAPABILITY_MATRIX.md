# NasEmby 核心接口能力矩阵

状态：源码已恢复；PT 主线使用细分接口，Telegram 网盘能力延期且旧高风险入口继续关闭

日期：2026-07-17

## 目的

这份文档用于回答四个固定问题：接口做什么、调用哪段 Python、会影响哪个外部软件、当前能否安全调用。以后不再根据路由名称猜测用途，也不允许在没有替代证据时删除接口。

源码位置：

- 路由与调用关系：`services/nasemby-core/app/main.py`
- 115、provider 和设备能力：`services/nasemby-core/app/services.py`
- Telegram：`services/nasemby-core/app/telegram_runtime.py`
- HDHive / pansou：`services/nasemby-core/app/hdhive_auth.py`、`services/nasemby-core/app/hdhive/`
- 订阅、发现、日历和资源规则：`services/nasemby-core/app/discover_runtime.py`
- 当前 React 兼容层：`discover_compat_runtime.py`、`subscription_compat_runtime.py`

## 状态定义

- **当前**：已由 React 或当前统一后端使用。
- **保留关闭**：源码和路由已恢复，默认由 `MCC_PRESERVED_CORE_API_ENABLED=false` 阻止真实调用。
- **待接入**：需要新的统一页面、细分写入开关、幂等和审计后才能开放。
- **兼容**：功能已被当前接口覆盖，原实现继续保留作为行为参考和回归基线。

生产环境不得为了“试一下”整体开启 `MCC_PRESERVED_CORE_API_ENABLED`。当前只允许在模拟测试中开启；未来应把每组能力迁移到独立细分开关。

## 系统、配置与审计

| 方法与路径 | Python 调用 | 用途 | 副作用 | 状态与后续 |
| --- | --- | --- | --- | --- |
| `GET /api/status` | `api_status → project_status` | 返回项目和功能状态 | 无 | 当前 |
| `GET /api/health` | `api_health → read_config` | 汇总 Emby、qB、Torra、Symedia 和订阅配置状态 | 无，不返回凭据 | 当前 |
| `GET /api/dashboard/system` | `api_dashboard_system → dashboard_system_metrics` | 读取 NAS CPU、内存、磁盘等摘要 | 无 | 原入口保留关闭；当前由 `GET /api/v2/system/metrics` 白名单映射并缓存 |
| `GET /api/config` | `api_get_config → read_config` | 读取 NasEmby 原配置 | 读取可能含敏感配置 | 保留关闭；需拆成脱敏分组接口 |
| `POST /api/config` | `api_save_config → write_config` | 保存 NasEmby 原配置分组 | 写配置文件，可能改变后台行为 | 保留关闭；待拆分开关和字段白名单 |
| `GET /api/activity/logs` | `api_activity_logs → read_activities` | 读取原操作日志 | 无 | 当前契约已有同路径，原实现作为兼容基线 |
| `POST /api/activity/clear` | `api_activity_clear → clear_activities` | 清空操作日志 | 删除日志 | 保留关闭；需要二次确认 |
| `POST /api/activity/event` | `api_activity_event → write_activity` | 记录前端操作事件 | 写日志 | 保留关闭；待限定事件类型和大小 |

## HDHive / pansou

延期保留映射：

- `GET /api/v2/integrations`：脱敏状态。
- `GET /api/v2/integrations/hdhive/authorization`：生成授权地址。
- `PATCH /api/v2/integrations/hdhive/config`：白名单配置。
- `POST /api/v2/integrations/hdhive/check-ins`：一小时冷却签到。
- `/api/v2/acquisition/cloud/candidates` 与 `/transfers`：候选和单条转存。当前 React 不调用，环境闸门保持关闭。

| 方法与路径 | Python 调用 | 用途 | 副作用 | 状态与后续 |
| --- | --- | --- | --- | --- |
| `GET /api/hdhive/authorize` | `api_hdhive_authorize → hdhive_auth_url` | 生成 HDHive 授权地址并跳转 | 进入外部授权流程 | 保留关闭；待统一授权页 |
| `GET /api/hdhive/status` | `api_hdhive_status → hdhive_status` | 检查模块、授权和账号状态 | 可能读取本地授权信息 | 保留关闭；待控制室只读状态 |
| `GET /api/hdhive/identity` | `api_hdhive_identity → hdhive_identity` | 读取当前 HDHive 身份 | 无外部写入 | 保留关闭；待脱敏 |
| `POST /api/hdhive/config` | `api_hdhive_config → update_hdhive_config` | 更新 HDHive 配置 | 写配置 | 保留关闭；待字段白名单 |
| `POST /api/hdhive/account` | `api_hdhive_account → update_hdhive_account` | 更新 HDHive 展示账号 | 写配置或账号元数据 | 保留关闭 |
| `POST /api/hdhive/checkin` | `api_hdhive_checkin → hdhive_checkin_now` | 立即执行签到 | 调用外部 HDHive | 保留关闭；需要冷却和审计 |
| `POST /api/yingchao/search` | `api_yingchao_search → search_yingchao_resources` | 通过 HDHive / pansou 搜索网盘候选 | 外部读取 | 保留关闭；计划映射到网盘候选预览 |
| `POST /api/yingchao/transfer` | `api_yingchao_transfer → transfer_yingchao_item` | 转存选中的网盘候选 | 外部写入 115 | 保留关闭；需查重、确认、幂等和冷却 |

## Telegram

延期保留映射：`/api/v2/integrations/telegram/*` 覆盖登录码、登录、退出和频道读取/保存。当前 React 不调用；未来启用仍需要总开关与 Telegram 细分开关同时开启。

| 方法与路径 | Python 调用 | 用途 | 副作用 | 状态与后续 |
| --- | --- | --- | --- | --- |
| `GET /api/telegram/status` | `api_telegram_status → telegram_status` | 检查 Telegram 登录与会话状态 | 读取会话 | 保留关闭；待系统连接状态 |
| `POST /api/telegram/send-code` | `api_telegram_send_code → telegram_send_login_code` | 向手机号发送登录验证码 | 外部发送验证码 | 保留关闭；需要频率限制 |
| `POST /api/telegram/sign-in` | `api_telegram_sign_in → telegram_sign_in` | 使用验证码或二次密码登录 | 建立并保存 Telegram 会话 | 保留关闭；需敏感字段保护 |
| `POST /api/telegram/logout` | `api_telegram_logout → telegram_logout` | 注销 Telegram 会话 | 删除或失效会话 | 保留关闭；需要二次确认 |
| `GET /api/telegram/channels` | `api_telegram_channels → telegram_list_channels` | 读取资源频道列表 | 无 | 保留关闭；待订阅来源设置 |
| `GET /api/telegram/channel-icons/:filename` | `api_telegram_channel_icon → send_from_directory` | 返回已缓存频道图标 | 本地文件读取 | 保留关闭；需路径约束测试 |
| `POST /api/telegram/channels` | `api_telegram_save_channels → telegram_save_channels` | 保存启用的资源频道 | 写配置 | 保留关闭 |
| `DELETE /api/telegram/channels/:index` | `api_telegram_delete_channel → telegram_delete_channel` | 删除频道配置 | 写配置 | 保留关闭 |
| `POST /api/telegram/channels/reorder` | `api_telegram_reorder_channels → telegram_reorder_channels` | 调整频道优先级 | 写配置 | 保留关闭 |

## 115

延期保留映射：`POST /api/v2/integrations/cloud115/probes`、候选预览和单条转存。当前 React 不调用；浏览器响应仍不包含 Cookie、完整分享链接或提取密码。原监控、清理和助力继续保留关闭。

| 方法与路径 | Python 调用 | 用途 | 副作用 | 状态与后续 |
| --- | --- | --- | --- | --- |
| `POST /api/115/check` | `api_check_115 → check_115_account` | 验证 115 账号或 Cookie 是否可用 | 外部只读请求 | 保留关闭；模拟测试已覆盖开关 |
| `POST /api/115/extract` | `api_extract_115 → extract_115_links` | 从输入文本提取 115 分享链接 | 无外部写入 | 保留关闭；计划作为候选解析能力 |
| `POST /api/115/transfer` | `api_transfer_115 → transfer_115_share` | 把指定分享链接转存到 115 | 外部写入 115 | 保留关闭；必须查重、确认、幂等和审计 |
| `POST /api/115/monitor/run` | `api_run_monitor → run_115_monitor_once` | 手动执行一次 Telegram / 115 监控 | 可能搜索并转存 | 保留关闭；拆分 dry-run 与执行 |
| `POST /api/115/cleanup/run` | `api_run_cleanup → run_115_cleanup` | 执行原 115 清理规则 | 删除或移动网盘文件 | 保留关闭；高风险，必须预览和二次确认 |
| `POST /api/115/boost` | `api_run_boost → run_115_invite_boost` | 运行原 115 助力流程 | 外部账号动作 | 保留关闭；是否进入统一页面待评估 |

## PT、provider 与入库

| 方法与路径 | Python 调用 | 用途 | 副作用 | 状态与后续 |
| --- | --- | --- | --- | --- |
| `GET /api/moviepilot/status` | `api_moviepilot_status → moviepilot_status` | 检查 MoviePilot 连接 | 外部只读 | 保留关闭；兼容能力 |
| `POST /api/moviepilot/subscribe` | `api_moviepilot_subscribe → moviepilot_subscribe` | 向 MoviePilot 创建订阅 | 外部写入 | 保留关闭；PT 主链备用兼容 |
| `GET /api/torra/status` | `api_torra_status → torra_status` | 检查 Torra 原连接状态 | 外部只读 | 保留关闭；当前由 `GET /api/torra/summary` 提供控制室摘要 |
| `POST /api/torra/subscribe` | `api_torra_subscribe → torra_subscribe` | 向 Torra 创建或更新订阅并搜索 | 外部写入 | 原入口保留关闭；当前由 v2 Torra 预览/推送路由调用统一安全逻辑 |
| `GET /api/symedia/status` | `api_symedia_status → symedia_status` | 检查 Symedia 原连接状态 | 外部只读 | 保留关闭；当前由 `GET /api/symedia/summary` 提供摘要 |
| `POST /api/symedia/subscribe` | `api_symedia_subscribe → symedia_subscribe` | 向 Symedia 推送原订阅动作 | 外部写入 | 保留关闭；当前不向 Symedia 推送订阅，Symedia 只负责 115 后整理入库 |

## Emby 原接口

| 方法与路径 | Python 调用 | 用途 | 副作用 | 状态与后续 |
| --- | --- | --- | --- | --- |
| `POST /api/emby/libraries` | `api_emby_libraries → fetch_emby_libraries` | 使用提交的连接信息读取 Emby 媒体库 | 外部只读，但请求中含凭据 | 保留关闭；当前统一接口从服务端环境读取凭据 |
| `GET /api/emby/library-image/:itemId` | `api_emby_library_image → fetch_emby_library_image` | 读取原媒体库图片 | 外部只读 | 保留关闭；当前使用 `/api/media/image/:itemId/:imageType` |

## 已由当前兼容层承接的原接口

下列原函数仍保留在 `main.py`，但 `create_app()` 先注册当前兼容层，因此浏览器实际命中新的统一实现。它们是回归基线，不是第二套台账。

| 原接口组 | 原实现 | 当前实现 | 结论 |
| --- | --- | --- | --- |
| `/api/discover/search` | `api_discover_search` | `register_discover_compat` | 当前使用统一字段映射 |
| TMDB、海外流媒体、豆瓣、平台热榜、全球日播 | `api_discover_*` | `/api/discover/browse`、`/api/discover/trending` 和内部 source 路由 | 原数据源逻辑仍在 `discover_runtime.py` |
| `/api/discover/resources/search` | `api_discover_resources_search` | `register_discover_compat` | 当前只读资源搜索 |
| `/api/subscriptions/items` | `api_subscriptions_items` | `register_subscription_compat` | 同一 NasEmby 台账 |
| 配置、详情、日历、运行、保存、删除、屏蔽、清空 | `api_subscriptions_*` | `register_subscription_compat` | 原业务函数保留，React 字段由兼容层转换 |
| `/api/status`、`/api/health` | `api_status`、`api_health` | 当前仍由 `core_routes` 提供 | 已启用 |

## 链路验证顺序

代码阶段只执行模拟验证，不连接实机：

1. 路由存在性：确认所有原接口仍在 Flask URL map。
2. 默认隔离：确认保留接口返回 `503 PRESERVED_CORE_API_DISABLED`，而不是 404。
3. 模拟启用：测试中开启 `MCC_PRESERVED_CORE_API_ENABLED=true`，替换外部函数为 mock。
4. 输入输出契约：记录每条接口的必填字段、成功响应、错误响应和脱敏要求。
5. 副作用闸门：Torra v2 推送使用确认、幂等、冷却和独立开关；115、Telegram、HDHive 和网盘搜索/转存继续保留独立开关但当前无 React 入口。
6. 新旧映射：v2 接口与保留接口调用同一业务函数，模拟测试已经覆盖关键行为。
7. 以上代码验证已完成；下一步才是用户批准的 fnOS 实机单条链路测试。

## 删除禁令

未在本矩阵中标记“已被功能等价接口替代，并通过回归测试”的接口、函数、模块或原调用参考，不得删除。可以默认关闭、移动到隔离模块或不注册旧页面，但必须保留可读源码和测试入口。
