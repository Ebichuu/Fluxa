# NasEmby Core 补丁记录

源码基线：`D:\Projects\NasEmby_friend_clean\NasEmby_friend_clean_20260630_171606`

本目录记录合并后相对基线的行为修改。不能在媒体控制中心 TypeScript 侧另写一套规则绕开 NasEmby bug。

## 2026-07-17：统一 Python 公开入口与单容器部署

- 问题证据：迁移阶段仍有旧运行后端、内部 Core 网络门面和两套字段契约；直接切 Compose 会导致 React 订阅/发现字段不兼容，并可能把原 NasEmby 高风险管理路由暴露到统一端口。
- 修改：新增 `contract_mapping.py`、`discover_compat_runtime.py`、`subscription_compat_runtime.py`；47 条冻结路由由 Python 承载，公开数据白名单映射。订阅写入、订阅调度和 Torra 推送默认关闭，legacy 管理入口返回 404。根镜像改为 Node 构建 + Python 运行，Compose 收口为一个服务。
- 行为兼容性：React 路径和字段保持 v1；订阅只写 NasEmby 原台账。分类与改季直接更新唯一条目，不触发 provider 后处理。Mineradio 原资源和桥接片段不变，改用 SHA-256 快照回归。
- 验证：75 项 Python 测试、TypeScript 检查、Vite 构建、Docker 镜像、认证、只读 API、写闸门、legacy 404、无 Node 运行时和重启持久化通过；没有执行真实外部写动作。
- 回滚：恢复阶段 6 镜像或迁移前归档，先停止统一 Python 容器；订阅数据不随代码回滚，也不能同时启动两套调度器。

每个补丁必须记录：

- 日期与标题。
- 原始文件和函数。
- 问题证据。
- 修改内容与理由。
- 行为兼容性。
- 自动测试或手工验证。
- 回滚方法。

## 2026-07-14 - 初始源码纳入

- 从基线项目复制 `app/`、`requirements.txt`、`Dockerfile` 和 `.env.example`。
- 未复制 `data/`、`db/`、`upload/` 或任何真实配置与会话。
- 尚未修改 NasEmby 业务行为。

## 2026-07-14 - 外部自动动作默认关闭

- 原始文件：`app/config.py`、`.env.example`。
- 问题：原基线默认开启 MoviePilot 自动订阅和 Telegram 订阅通知，不符合媒体控制中心“PT/Torra 优先、实机前所有外部写动作关闭”的安全边界。
- 修改：`ENV_MOVIEPILOT_AUTO_SUBSCRIBE` 和 `ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED` 默认值改为 `0`。
- 兼容性：用户后续仍可显式开启；未配置环境不再尝试自动推送或通知。
- 验证：`tests/test_source_contract.py` 检查全部外部自动动作默认关闭。
- 回滚：恢复两项默认值为 `1`，不涉及数据迁移。

## 2026-07-15 - 增加 JustWatch 海外流媒体来源

- 原始文件：`app/discover_runtime.py`、`app/main.py`。
- 问题：NasEmby 基线没有用户现有中控中的海外流媒体发现来源，直接切换会丢失 Netflix 等平台榜单。
- 修改：迁入八个已核对的 US 区 TMDB watch-provider ID，新增 `fetch_streaming()`、`/api/discover/streaming` 和独立 `streaming` 缓存分类；复用原 TMDB discover 和标准化逻辑。
- 兼容性：原 `/api/discover/tmdb` 行为不变；未知 provider 回退 Netflix。JustWatch 只影响发现，不增加订阅台账或获取通道。
- 验证：`tests/test_source_contract.py` 覆盖八个平台、未知 provider 回退和 TMDB URL 查询参数；测试不访问真实网络。
- 回滚：删除 `STREAMING_PROVIDERS`、`fetch_streaming()`、Flask 路由和 Express 来源枚举即可，无数据迁移。

## 2026-07-15 - 订阅进度只读门面不回写台账

- 原始文件和函数：`app/discover_runtime.py` 的 `load_subscription_items()`、`app/main.py` 的 `api_subscriptions_items()`。
- 问题证据：NasEmby 基线在 `include_progress=1` 时会调用 `enrich_subscription_items(remove_completed=True)` 并执行 `write_subscription_items_data()`；媒体控制中心阶段 5 只读页面如果直接复用，会因一次 GET 删除已完成条目并回写真实订阅文件。
- 修改：`load_subscription_items()` 增加 `remove_completed` 和 `persist_progress` 可选参数，默认均保留原行为；`/api/subscriptions/items` 接受内部 `read_only=1`，此时不删除完成项、不持久化进度，并为每条响应补充由 NasEmby 原函数计算的 `key`、`media_type` 和 `tmdb_id`。
- 修改理由：中控只读迁移需要完整快照和稳定身份，不能让浏览页面产生订阅业务写入；身份仍由 NasEmby 原函数生成，Node 不猜业务键。
- 行为兼容性：未传 `read_only=1` 的 NasEmby 原页面和调度调用保持原行为。Express `NasembyCoreAdapter` 固定使用只读参数。
- 验证：`tests/test_source_contract.py` 断言只读进度不调用写函数且保留条目；Node 契约测试检查 `read_only=1` 和映射字段。
- 回滚：移除两个可选参数与 `read_only` 分支即可；不涉及数据迁移。

## 2026-07-16 - Gunicorn 单 worker 生产入口

- 原始文件和函数：`Dockerfile` 的进程入口，`app/main.py` 的三个调度器启动函数。
- 问题证据：基线以 Flask 开发服务器运行；三个调度器只在 `python -m app.main` 的 `__main__` 分支启动。直接切换普通 WSGI 会漏掉调度，多个 worker 又会重复调度。
- 修改：增加 Gunicorn 23.x 依赖和固定单 worker、四线程配置；新增统一幂等后台启动函数，并由 `post_worker_init` 在唯一 worker 中调用。调度异常日志只包含调度器名称和异常类型。
- 修改理由：使用生产 WSGI 监管内部 API，同时保持单实例订阅调度所有权。
- 行为兼容性：本地 `python -m app.main` 仍可运行；Flask API、订阅数据、调度频率和外部开关不变。当前禁止增加 worker 数或 Core 副本。
- 验证：Python 契约测试覆盖依赖、Docker 命令、Gunicorn 固定配置、生命周期钩子和启动幂等性；Docker 使用空命名卷验证健康、内部端口和并发只读请求。
- 回滚：恢复 Dockerfile 的 `python -m app.main` 并移除 Gunicorn 配置与依赖；统一后台启动函数可以保留，不涉及数据迁移。

## 2026-07-16 - 统一 Flask 应用壳

- 原始文件和函数：`app/main.py` 的全局 Flask 实例与路由装饰器。
- 问题证据：基线把 62 条路由直接绑定到模块级 `app`，不能创建隔离应用实例，也缺少统一请求 ID 和未捕获 API 异常脱敏边界，后续迁移 Express 能力会继续扩大单一入口耦合。
- 修改：把原路由机械迁到 `core_routes` Blueprint，新增 `create_app()` 并保留模块级 `app` 兼容 Gunicorn；新增 `app/http_runtime.py`，负责合法请求 ID、API HTTP 错误 JSON 化和未捕获异常脱敏；新增 `/api/health`，只返回配置布尔值。
- 行为兼容性：归档基线 62 条路由的方法与路径全部保留，只新增一条只读健康路由；路由函数、订阅数据、调度频率和外部写开关不变。
- 验证：Python 契约测试覆盖应用实例隔离、路由一致性、调度不随工厂启动、请求 ID、API/页面 404 区分、500 脱敏和健康响应不泄露凭据；另与归档标签逐条比对路由集合。
- 回滚：恢复模块级 Flask 实例和 `@app` 装饰器，删除 `http_runtime.py` 与 `/api/health`；不涉及数据迁移。

## 2026-07-16 - Python 整站认证与 React 静态托管

- 原始文件和函数：媒体控制中心 `server/auth/accessAuth.ts`、`server/auth/loginPage.ts`、`server/routes/authRoutes.ts` 和 `server/app.ts` 的认证与生产静态托管行为。
- 问题证据：Python 已成为目标统一后端，但此前只有 Express 能保护 React、Mineradio 和 API；直接移除 Express 会失去整站认证、登录限流、Origin 校验和 SPA 页面提供能力。
- 修改：新增 `access_auth.py`、`auth_runtime.py`、`login_page.py`、`frontend_runtime.py` 和登录模板；复用与 Express 完全相同的 Cookie 版本、HMAC 密钥派生上下文、七天有效期、Cookie 名称与安全属性。显式配置 `MCC_FRONTEND_DIST` 后由 Python 提供 Vite 构建产物和 SPA 回退。
- 行为兼容性：认证未配置时保持开发环境原行为；当前 Compose 未把认证或 React 构建目录切给 Core。固定签名向量由 Python 生成并被 Express 接受，已有有效会话可跨后端继续使用。
- 验证：Python 测试覆盖弱密钥拒绝、Cookie 固定向量、401/303、篡改清理、第五次失败锁定、Origin、反向代理、登录页 CSP、React 首页/SPA/哈希资产和 API 404 隔离；Node 测试反向验证 Python Cookie。
- 回滚：删除四个运行模块和登录模板，恢复原 `create_app()` 组装顺序；当前未切生产入口，不涉及订阅或静态资源迁移。
