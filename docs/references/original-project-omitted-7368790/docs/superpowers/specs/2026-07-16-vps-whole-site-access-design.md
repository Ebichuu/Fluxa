# 媒体控制中心整站访问保护设计

状态：方案 A 已确认，待文档复核
日期：2026-07-16
关联计划：`docs/PLAN.md` 第 11 节“VPS 登录保护”

## 1. 背景

媒体控制中心当前没有自身认证：Express 对全部 `/api`、`/mineradio` 和生产静态页面直接响应，并使用开放的 `cors()`。这在本机和受信局域网内方便开发，但不能直接作为公网 VPS 的访问边界。

用户已选择方案 A：打开媒体控制中心时先登录，登录成功后才能访问整套 React 页面、影院大厅、Mineradio 嵌入资源和全部业务 API。认证只属于媒体控制中心，不修改 Emby、qBittorrent、Torra、Symedia 或 NasEmby 的原认证方式。

## 2. 目标

- 一个访问密钥保护整站和全部业务 API。
- 登录成功后使用 HttpOnly 签名 Cookie 保持会话，密钥不进入浏览器存储。
- 生产环境缺少或使用过短密钥时拒绝启动，避免误把未保护服务暴露到公网。
- 限制跨域来源、登录暴力尝试、Cookie 伪造和开放重定向。
- 保持本地 Vite 预览默认无登录，避免影响当前开发流程。
- 不修改影院大厅 UI、媒体队列和顶部导航结构。

## 3. 非目标

- 不建立用户表、角色、注册、找回密码或多用户权限。
- 不接 OAuth、OIDC、LDAP、短信、邮件或双因素认证。
- 不让页面保存、显示或修改 `MCC_ACCESS_KEY`。
- 不用 Nginx Basic Auth 代替应用登录页。
- 不把上游 Emby、qB、Torra、Symedia 或 NasEmby 凭据作为中控登录凭据。
- 不在本阶段部署 VPS、申请证书或修改公网 DNS。

## 4. 方案对比与决策

### 方案 A：应用登录页 + 签名 Cookie（采用）

Express 提供同源登录页、恒定时间密钥校验和无状态签名会话。用户获得完整、可退出的应用体验；中控能统一保护 React、Mineradio 和 API，并能精确限制 CORS。

### 方案 B：只保护 `/api`（不采用）

未经认证仍会加载 React 外壳，随后出现大量 401、空状态和失败提示。它也会让站点结构与静态资源继续公开，安全和体验都弱于整站保护。

### 方案 C：反向代理 Basic Auth（暂不采用）

配置简单，但使用浏览器原生认证弹窗，退出和会话管理较差，也无法与中控设置页形成一致入口。它可以作为未来额外的外层防护，但不替代应用认证。

## 5. 信任边界与路由顺序

路由顺序固定为：

```text
安全响应头与受限 CORS
  -> GET /healthz
  -> GET/POST /auth/login
  -> POST /auth/logout
  -> GET /api/auth/session
  -> 整站认证闸门
  -> 现有 /api/*
  -> /mineradio/*
  -> 生产静态文件和 SPA 回退
```

只有以下入口允许未登录访问：

- `GET /healthz`：只返回 `{ "status": "ok" }`，不暴露服务配置、版本、地址或凭据。
- `GET /auth/login`：返回登录页。
- `POST /auth/login`：提交访问密钥。
- `POST /auth/logout`：清除当前浏览器 Cookie；允许损坏或过期 Cookie 执行。
- `GET /api/auth/session`：只返回认证是否启用、当前是否已登录和会话到期时间，不返回密钥或签名材料。
- 受允许来源的 `OPTIONS` 预检请求。

现有 `/api/health` 仍包含服务配置摘要，因此位于认证闸门之后。`/mineradio/embed` 和所有 Mineradio 静态资源同样位于认证闸门之后；同源 iframe 会自动携带会话 Cookie。

## 6. 环境配置

新增配置：

- `MCC_ACCESS_KEY`：整站访问密钥。生产环境必填，UTF-8 长度至少 16 个字符；建议使用 32 个以上随机字符。
- `MCC_ALLOWED_ORIGINS`：可选的逗号分隔完整来源列表，例如 `https://media.example.com`。默认不允许跨域，只允许同源访问。
- `MCC_COOKIE_SECURE`：可选布尔值。生产环境默认 `true`，开发环境默认 `false`；只有受信局域网的临时 HTTP 测试可以显式设为 `false`。

固定配置：

- 会话有效期 7 天，不做滑动续期。
- Cookie 名称 `mcc_session`，`Path=/`，不设置 `Domain`。
- 生产 `NODE_ENV=production` 且密钥缺失或少于 16 字符时，服务在监听端口前抛出脱敏配置错误并退出。
- 非生产环境且未设置 `MCC_ACCESS_KEY` 时认证关闭，当前 Vite + 8788 开发预览保持原行为。
- 非生产环境一旦显式设置密钥，认证完整启用，便于本地验收。

Docker Compose 传入上述三个变量，但不在仓库提供真实密钥或默认密钥。`.env.example` 只能保留空值和说明。

## 7. 密钥与会话格式

登录提交使用 `application/x-www-form-urlencoded`，密钥只存在于 POST 请求体。服务端先对配置密钥和提交值分别计算 SHA-256，再用 `timingSafeEqual` 比较固定长度摘要；不使用普通字符串比较。

签名 Cookie 使用无状态格式：

```text
v1.<expires_unix_ms>.<random_nonce_base64url>.<hmac_sha256_base64url>
```

- nonce 使用 Node `crypto.randomBytes(16)`。
- 签名覆盖版本、到期时间和 nonce。
- HMAC 密钥由 `MCC_ACCESS_KEY` 和固定上下文字符串派生，不增加第二个必须管理的秘密。
- 校验签名同样使用恒定时间比较。
- 修改 `MCC_ACCESS_KEY` 会立即使全部旧会话失效。
- Cookie 属性：`HttpOnly`、`SameSite=Strict`、生产默认 `Secure`、`Max-Age=604800`。
- Cookie 不包含访问密钥、用户信息、上游 Token 或服务状态。

该设计没有服务端会话表。退出只清除当前浏览器 Cookie，无法撤销已经被复制的有效 Cookie；发生泄露时必须轮换 `MCC_ACCESS_KEY`。这是单用户、7 天固定会话模型的已知限制。

## 8. 登录、重定向与错误语义

### 页面请求

- 未登录访问普通 `GET` 页面时返回 `303 See Other`，跳转到 `/auth/login?next=...`。
- `next` 只接受以单个 `/` 开头的站内路径；拒绝 `//`、绝对 URL、反斜杠和 `/auth/*`，非法值回退 `/`。
- 登录成功设置 Cookie，并以 303 跳转到安全的 `next`。
- 登录失败返回同一登录页和 HTTP 401，只显示“访问密钥不正确”，不区分密钥长度、字符或其他内部原因。

### API 与资源请求

- 未登录 `/api/*` 返回 HTTP 401：`{ "error": "需要登录", "code": "AUTH_REQUIRED" }`。
- 未登录非 HTML 资源返回 401，不把脚本、图片或 iframe 请求重定向成 HTML。
- Cookie 过期、格式错误或签名错误均按未登录处理，并附带清除 Cookie 的响应头。
- 跨域来源不符合规则时，危险方法返回 HTTP 403：`{ "error": "来源不允许", "code": "ORIGIN_FORBIDDEN" }`。

## 9. 登录限速

使用进程内、按 `request.socket.remoteAddress` 统计的登录失败窗口：

- 15 分钟内最多 5 次失败。
- 达到上限后锁定 15 分钟并返回 429。
- 成功登录会清除该地址的失败记录。
- 响应只给通用提示，不返回剩余次数。
- 日志不记录提交的密钥、Cookie、请求体或查询参数。

媒体控制中心当前是单 Node 进程，该内存限速与部署模型一致。进程重启会清空限速记录；未来横向扩容时必须迁移到共享限速存储。反向代理必须限制公网直接访问 8787，避免绕开代理层的网络限速。

## 10. CORS 与 CSRF

- 移除当前无条件 `cors()`。
- 默认不返回跨域允许头；同源 React、Mineradio iframe 和 Vite 代理不依赖 CORS。
- `MCC_ALLOWED_ORIGINS` 只接受完整、精确的 `http://` 或 `https://` origin，不支持 `*`、正则或子域通配。
- 命中允许列表时返回精确 `Access-Control-Allow-Origin`、`Vary: Origin` 和 `Access-Control-Allow-Credentials: true`。
- `OPTIONS` 只对允许来源返回预检结果。
- 带 `Origin` 的 POST/PUT/PATCH/DELETE 请求必须来自同源或允许列表，否则在业务路由前返回 403。
- `SameSite=Strict` Cookie 与危险方法 Origin 校验共同承担 CSRF 防护；本阶段不为每个现有业务表单增加独立 CSRF Token。

## 11. 登录页面与退出入口

登录页由 Express 直接返回完整 HTML，不先加载 React，也不引用外部字体、图片、脚本或 CDN。页面使用媒体控制中心现有深色雾面方向，但不复刻影院大厅，不使用金青色、渐变、装饰球或营销文案。

页面内容只包含：

- “媒体控制中心”主标题和“私有访问”状态。
- 一个 `type=password`、支持密码管理器的访问密钥输入框。
- “进入控制中心”提交按钮。
- 通用错误或限速状态。

登录页在桌面和手机上完整适配，表单最大宽度固定，按钮和文本不溢出。登录页使用独立严格 CSP：默认禁止所有资源，只允许带 nonce 的内联样式和自身表单提交；禁止被 iframe 嵌入。

退出入口放在系统设置页，不增加顶部导航项目，也不修改影院大厅。按钮使用 Lucide `LogOut` 图标，提交 `POST /auth/logout`，成功后跳转登录页。

## 12. 安全响应头

所有响应增加：

- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `X-Frame-Options: SAMEORIGIN`，允许同源 Mineradio iframe，禁止第三方站点嵌入。
- `Cache-Control: no-store` 用于登录、登录响应、退出和认证状态。

不在本阶段给整套 Mineradio/React 页面增加全局 CSP，因为 Mineradio 内置资源包含现有内联脚本和运行时资源加载，未经专项回归直接收紧会导致影院大厅空白。严格 CSP 只用于独立登录页。

## 13. 代码边界

计划新增或修改：

- `server/config.ts`：解析和验证认证配置，只保存进程内值。
- `server/auth/accessAuth.ts`：密钥比较、Cookie 签发与校验、限速、Origin 校验和认证中间件。
- `server/auth/loginPage.ts`：纯服务端登录 HTML 与安全转义。
- `server/routes/authRoutes.ts`：登录、退出、会话状态和最小健康路由。
- `server/index.ts`：按固定顺序挂载 CORS、认证路由、整站闸门和现有业务路由。
- `src/components/pages/SettingsPage.tsx`：增加退出动作，不改顶部导航。
- `src/services/api.ts`：增加只读会话状态接口。
- `docker-compose.yml`、新建根目录 `.env.example`、`README.md` 与计划文档：记录部署配置和 HTTPS 要求。
- `tests/core-stability.test.ts`：增加认证、Cookie、限速、CORS、重定向和生产配置契约。

只使用 Express 和 Node 标准 `crypto`，不增加 session、cookie-parser、认证框架或数据库依赖。

## 14. 验收标准

### 自动测试

- 生产环境缺失或弱密钥拒绝启动；开发环境空密钥保持关闭。
- 正确密钥登录成功，错误密钥只有通用 401。
- Cookie 包含 HttpOnly、SameSite=Strict、Path、Max-Age 和正确 Secure 行为。
- 有效 Cookie 可访问 API、Mineradio 和页面；篡改、过期 Cookie 被拒绝并清除。
- 五次失败后返回 429，成功登录清除失败窗口。
- `next` 不能跳转到外站或认证路由。
- `/healthz` 未登录可用且只返回最小状态；原 `/api/health` 需要登录。
- 默认无跨域允许头，未知 Origin 的危险方法返回 403，精确允许来源可携带 Cookie。
- 原 Node 回归、Python 契约和生产构建继续通过。

### 浏览器验收

在独立测试端口临时设置测试密钥，不写 `.env`：

- 1440px 和 390px 打开站点先看到登录页，无应用内容闪现。
- 错误密钥显示通用错误，正确密钥进入媒体中心。
- 登录后影院大厅与 Mineradio iframe 正常加载，页面和顶部导航 UI 不变。
- 刷新页面仍保持登录；系统设置退出后无法再访问 API 或页面。
- Cookie 不可由 `document.cookie` 读取。
- 当前 `5173 + 8788 + 12389` 无认证开发预览继续可用。

### 部署验收

- VPS 必须使用 HTTPS；生产默认 Secure Cookie。
- 8787 只允许反向代理或防火墙受信来源访问。
- 真实 `MCC_ACCESS_KEY` 只放在 VPS 环境变量或未跟踪 `.env`，不写 Compose、镜像、日志、前端包或文档。
- 修改访问密钥后旧 Cookie 立即失效。

## 15. 回滚

若登录保护造成生产访问故障：

1. 通过受信本机或容器环境修正 `MCC_ACCESS_KEY` 和 HTTPS/Cookie 配置。
2. 必要时回滚认证路由与中间件代码；不得通过在公网生产环境清空密钥来临时绕过。
3. 本地开发可以不设置密钥恢复无认证模式。

回滚不涉及订阅、媒体、任务链或 NasEmby 数据迁移。
