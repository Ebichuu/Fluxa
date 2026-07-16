# Python 统一后端安全审计

日期：2026-07-14
范围：NasEmby 来源代码、Python 统一入口、公开兼容层、单容器部署与阶段 8 收口

2026-07-17 更新：Python 已成为唯一公开后端，旧运行后端已删除。下面保留初始扫描证据；涉及迁移期双服务的描述以文末“最终公开边界”为准。

## 当前自动扫描结果

2026-07-17 恢复核心接口并修正动态 SQL 与固定 115 请求后，生产 Python 源码扫描结果为：0 个 Critical、0 个 High、2 个 Medium、1 个 Low，安全关卡通过。

若把 `docs/references/nasemby-original-ui/` 的不运行参考快照也纳入扫描，会额外报告 64 个 `innerHTML` High。这些文件由 `.dockerignore` 排除，Flask 不注册对应静态路由；它们只用于核对原页面的接口调用关系。

## 复核结论

### SQLite 动态查询（2 个 Critical）

`discover_runtime.py` 的两条查询所用列名来自本地 SQLite `pragma table_info(organize_history_records)`；标题和 TMDB ID 等值继续通过 `?` 参数绑定。当前已增加 `quote_sqlite_identifier()`，对动态标识符使用 SQLite 双引号转义并拒绝 NUL，同时取消 `execute(f"...")`。包含双引号列名的临时数据库回归测试已通过，Critical 清零。

### 固定 115 请求（4 个 High）

`legacy/tgto115.py` 和 `legacy/zhuli115.py` 只允许源码固定的 `my.115.com`、`webapi.115.com` 和 `act.115.com` 地址，不接受调用方提供主机。现已把地址改为明确常量，查询参数使用 `urlencode()`，所有相关请求增加超时；High 清零。该能力在实机前仍保持关闭。

### 原 NasEmby 静态页面 `innerHTML`（64 个 High）

原 `app/static/app.js` 大量使用模板字符串和 `innerHTML`。部分动态值经过 `escapeHtml`，但自动扫描无法证明完整数据流安全。

统一入口不会把该页面嵌入或暴露给用户；媒体控制中心使用 React 渲染。原静态文件只作为页面内容迁移依据保留。统一端口放行冻结的公开兼容 API；原核心管理路由保留在 URL map 中但默认返回 `503 PRESERVED_CORE_API_DISABLED`，原静态页面路径仍不注册。

若未来改变决定并直接暴露原页面，必须重新进行逐点 XSS 审计，不能沿用本结论。

### 随机数与哈希（Medium/Low）

- `random.randint` 用于安排下次提醒时间，不用于密钥、Token 或权限判断。
- MD5 用于文件内容校验，不用于密码存储或签名。
- SHA1 用于生成可重复的短订阅 ID，不承担安全认证。

这些属于非安全用途，当前接受。

## 已修正问题

- MoviePilot 自动订阅默认值从开启改为关闭。
- Telegram 订阅通知默认值从开启改为关闭。
- Compose 同时显式关闭 Torra、Symedia、115、Telegram、HDHive 和 PT 上传自动动作。
- 恢复的 37 条核心接口默认返回 `503 PRESERVED_CORE_API_DISABLED`，模拟测试才允许整体开启。
- SQLite 动态列名完成安全引用，固定 115 请求完成常量化和参数编码。

## 2026-07-15 阶段 5 复核

- 公开订阅列表、详情和日历不直接透传原始 JSON，Python `contract_mapping.py` 使用字段白名单；未知字段、原始上游对象和内部错误正文不会进入浏览器。入库路径是用户明确要求展示的业务字段，保留在详情和分集白名单中。
- 订阅公开读取固定使用 `remove_completed=False` 和 `persist_progress=False`。列表进度计算不会删除已完成项或回写订阅台账；Python 契约测试已覆盖该副作用边界。
- 订阅 POST/PATCH 默认由 `NASEMBY_CORE_WRITE_ENABLED=false` 返回 403；不存在旧台账或旁路写入。
- 原业务函数异常时，订阅、日历和任务链返回脱敏错误，不回退第二套数据。
- 迁移审计命令只输出计数、媒体身份和差异摘要，不输出服务地址、凭据或 Core 原始对象，不写文件、不合并数据。
- 自动扫描结果仍为基线的 2 Critical、68 High、2 Medium、1 Low；全部命中原 SQLite 动态标识符、未公开静态页面、固定 115 地址或非安全用途哈希/随机数。本阶段新增 TypeScript 服务端扫描通过，仅有两个既有 `console.log` Low；React 页面扫描 0 告警。本阶段没有新增 Critical/High。

## 2026-07-16 统一 Flask 应用壳复核

- 全目录规则扫描仍为 2 个 Critical、68 个 High、2 个 Medium、1 个 Low，命中位置与初始基线一致；新增 `app/http_runtime.py` 和本次改动的 `app/main.py` 没有新增扫描命中。
- 客户端提供的 `X-Request-ID` 只接受 1 至 64 位字母、数字、点、下划线和短横线，拒绝控制字符和路径字符，避免响应头与日志注入。
- 未捕获异常的响应只包含固定错误码、通用中文消息和请求 ID；日志只记录请求 ID、方法、无查询字符串的路径和异常类型，不记录异常消息、请求正文、Token 或 URL 参数。
- `/api/health` 只返回服务 ID、名称、类型和 `configured` 布尔值；测试使用伪凭据逐项确认地址、账号、Token 和 API Key 不进入响应。
- 原 Blueprint 路由仍保留在源码中，供业务函数、接口契约和历史调用关系对照。统一入口默认只启用 `/api/status`、`/api/health` 以及当前兼容层；其余原核心入口返回明确的 503 保留状态。

## 2026-07-16 Python 整站认证复核

- 密钥比较对提交值和配置值分别计算 SHA-256，再使用 `hmac.compare_digest`；Cookie HMAC 校验同样使用恒定时间比较。
- Cookie 继续使用 `v1.<expires_ms>.<nonce>.<signature>`、固定密钥派生上下文和 base64url 编码；密钥轮换会使全部旧会话失效。
- 两侧都要求签名是规范 base64url：解码后重新编码必须与原字符串完全一致，拒绝只修改未使用填充位但解码字节相同的等价编码。
- `next` 拒绝外站、双斜杠、反斜杠、控制字符、明文和百分号编码的 `/auth/*`；未登录页面跳转会先 URL 编码完整站内路径。
- 危险方法的非同源 Origin 在业务路由前返回 403；允许来源精确匹配，不支持通配。生产使用 `ProxyFix` 且只信任一层反向代理，因此源站端口必须继续由防火墙限制。
- Python 显式拒绝包含 `*`、路径、凭据或非法端口的 Origin；只接受同源或精确白名单。
- 登录页无外部资源，使用 nonce 内联样式、`default-src 'none'`、`form-action 'self'` 和 `frame-ancestors 'none'`；Jinja 默认转义错误与 next 值。
- React 静态托管只接受服务端配置目录，文件路径通过 `safe_join` 与 `send_from_directory` 解析；`/api`、`/auth` 和 `/mineradio` 不参与 SPA 回退。
- 本阶段新增模块的定向危险模式检查无 eval、exec、shell、反序列化、动态 SQL、任意 URL 请求或前端 HTML 注入。全目录扫描的既有基线风险数量不变。

## 2026-07-16 Python Mineradio 桥接复核

- Mineradio 资源根目录只来自 `MINERADIO_PUBLIC_DIR`、项目内置目录或固定 Windows 开发回退，不接受请求参数指定目录。
- `/mineradio/<path>` 在 `safe_join` 和文件存在性检查后使用 `send_from_directory`，目录索引和越界路径不提供；`/mineradio/embed` 仍受整站会话保护。
- 注入片段不是用户输入，也不拼接媒体数据；它以迁移完成时的 SHA-256 快照冻结，继续通过 `postMessage` 接收 React 的结构化数据。真实媒体字段由既有桥接函数处理，没有扩大 HTML 注入面。
- 嵌入页使用 `no-store`，原始资产使用同源、`nosniff`、`SAMEORIGIN` 与零秒缓存。浏览器桌面和移动回归无控制台错误。
- 新增 Python 运行文件不包含外部请求、凭据、动态执行、子进程或写动作；未调用 Torra、qB、115、Symedia、Telegram、HDHive 或 Emby。

## 2026-07-16 Python Emby 只读适配器复核

- Emby 凭据只从服务端环境或原 Core 配置读取。API Key 只进入到 Emby 的服务端查询；网络异常和 JSON 错误被替换为固定消息，不向浏览器或应用错误日志回显完整 URL。
- 密码登录请求只发送到配置的 Emby 基址，Token 仅保存在进程内存；401/403 最多清缓存并重登一次，不无限重试。
- 外部图片 URL 仅允许无用户名密码的 HTTP(S)，拒绝 localhost、私网/回环/链路本地/保留 IP，并在请求前校验全部 DNS 结果；不跟随重定向，避免跳转绕过目标校验。
- 图片响应必须通过 JPEG、PNG、GIF、WebP 或 AVIF 魔数检查。非图片正文不按上游 Content-Type 直接回传，Emby 图片支持 `strict=1` 返回 204。
- 定向扫描对 `emby_runtime.py`、`media_read_runtime.py` 和 `fallback_media.py` 为 0 条新命中；全目录仍是既有 `2 Critical / 68 High / 2 Medium / 1 Low` 基线。本次迁移仅使用模拟响应，未再次连接真实 Emby，也未调用刷新写接口。

## 2026-07-16 Python qBittorrent 只读摘要复核

- qB 基址、用户名和密码只来自服务端环境。密码只进入 `/api/v2/auth/login` 表单，SID Cookie 只在当前摘要的三个 GET 请求中复用，不持久化、不写日志、不返回浏览器。
- 网络异常统一为 `qBittorrent 请求失败` 或 `qBittorrent 登录请求失败`，不会回显可能包含地址、查询或凭据的底层异常。
- 阶段 5 只读迁移当时仅注册摘要 GET；阶段 6 新增的暂停/恢复由独立动作服务承载，不把删除、文件变更或万能操作接口带入 Python。
- 定向安全与质量扫描对 `qbittorrent_runtime.py` 均为 0 条新命中；全目录安全数字保持既有基线。本次仅使用固定模拟响应，未连接局域网 qBittorrent。

## 2026-07-16 Python Torra 只读摘要复核

- Torra 地址和凭据只来自服务端环境；Bearer Token 只进入到已配置基址的请求头，不写文件、不进入摘要或错误正文。
- 固定 Token 遇到 401/403 直接报告失效，不猜测刷新；仅账号密码模式允许清理进程 Token 并重登一次，避免无限认证循环。
- Python 客户端只实现 GET 订阅列表、摘要和本地查重，不包含保存订阅、运行搜索或其他写请求。
- 网络异常替换为固定 `Torra 请求失败` / `Torra 登录请求失败`；质量扫描 0 新问题。本次仅使用模拟响应，未连接现网 Torra。

## 2026-07-16 Python Symedia 只读摘要复核

- Symedia Token 只保存在配置或进程内存并进入 Bearer 请求头；账号模式在 401/403 后最多重登一次，网络错误不回显底层 URL 或凭据。
- 新客户端只有登录和转存历史 GET，不实现转存写动作；最多读取 20 页，避免异常上游导致无界循环。
- 本次仅使用固定分页样本，未连接真实 Symedia。阶段 5 全部新增运行文件定向扫描 0 新安全和质量问题，全目录仍保持既有安全基线。

## 2026-07-17 阶段 6 任务链与可回滚动作复核

- 任务链只读取 NasEmby `db/` 唯一订阅台账和四个外部只读客户端；订阅读取显式关闭完成项删除和进度回写。外部适配器失败只降级对应证据，订阅台账读取失败返回固定 502，不回显内部路径。
- qB 动作只接受 1 至 20 个 40 位十六进制 hash，去重后整批复查存在性；不存在任一目标时不提交写请求。暂停/恢复后重新读取状态，无法确认返回 202，不自动重试。活动日志只保存 hash 前 8 位，不保存 Cookie、密码、完整 hash 或文件路径。
- Emby 刷新不接受前端证据，POST 内部重新读取 Symedia 与 Emby 时间；从证据复查开始使用非阻塞执行锁，同一证据只处理一次，10 分钟冷却写入已忽略的 `data/emby-refresh-state.json`。成功 202 只表示 Emby 接受扫描请求，不表示扫描完成。
- Symedia 无时区时间按北京时间解析，输出时间统一转 UTC，避免本地开发环境把北京时间误标成 UTC；“今日”统计仍按北京时间判断。
- 阶段 6 自动测试全部使用固定样本、临时状态文件和模拟 qB/Emby 客户端，没有连接真实 qB、Emby、Symedia，也没有调用 Torra、115、Telegram 或 HDHive 写动作。React、影院大厅、顶部导航、媒体队列和原 Mineradio 资源未修改。
- 阶段 6 变更范围安全扫描新增命中为 0；Core 全目录仍是既有 `2 Critical / 68 High / 2 Medium / 1 Low` 基线。质量关卡为 0 错误、35 条复杂度/长度警告和 18 条行长提示，关卡通过；这些提示作为后续拆分候选记录，不在契约迁移阶段混入行为重构。

## 交付闸门

- 8787 只允许受信局域网或 HTTPS 反向代理访问，公网不得直接暴露源站端口。
- 阶段 9 的真实订阅闭环开始前，保持订阅写入、订阅调度和 Torra 推送关闭。

## 最终公开边界（2026-07-17）

- 一个 Python/Gunicorn 容器、一个 8787 端口，最终镜像不含 Node。
- 47 条冻结路由均由 Python 承载；42 条受保护路由逐条验证 401，所有受保护写路由逐条验证错误 Origin 为 403。
- 115、Telegram、HDHive、缓存预热和 provider 核心入口逐条验证为默认 503；模拟测试可显式开启，Flask 原 `/static/*` 路由仍关闭。
- 订阅保存、分类和改季只在临时台账测试；Torra 推送只使用模拟客户端。
- Docker 隔离冒烟验证订阅读取 200、写入 403、重启持久化和无 Node 运行时，没有注入真实外部凭据。
- SQLite 动态标识符风险已修复并有异常列名回归测试。
- 真实外部写动作必须由用户单独确认测试窗口。
