# 媒体控制中心整站访问保护实施计划

状态：本地实现完成，待 VPS 部署验收
日期：2026-07-16
设计依据：`docs/superpowers/specs/2026-07-16-vps-whole-site-access-design.md`

## 目标

为媒体控制中心增加一个应用级访问密钥和 HttpOnly 签名会话，统一保护 React 页面、影院大厅、Mineradio 资源、生产静态文件和全部业务 API。开发环境未设置密钥时保持现有 `5173 + 8788 + 12389` 预览行为，且不修改影院大厅、媒体队列和顶部导航 UI。

## 固定边界

- 只做单用户整站访问保护，不引入用户表、角色、OAuth、数据库会话或独立认证框架。
- 只使用 Express 与 Node `crypto`，不增加 session、cookie-parser 或其他运行时依赖。
- 生产环境必须配置至少 16 个字符的 `MCC_ACCESS_KEY`，否则在监听端口前拒绝启动。
- 当前所有 Torra、qB、Symedia、115、Telegram 和 Emby 真实写开关保持关闭；认证测试只访问本地测试服务器。
- 登录页由 Express 独立输出，不加载 React、Mineradio、外部字体、图片、脚本或 CDN。
- 退出入口只放系统设置页，不增加顶部导航项目。

## 实施步骤

### 1. 拆出可测试的应用组装边界

新增 `server/app.ts`，把 Express 中间件、业务路由、Mineradio 和生产静态页面的组装集中到 `createApp(config)`。`server/index.ts` 只保留读取配置、创建 `AutoSubscribeRunner`、启动唯一调度器和监听端口。

验证：测试导入 `createApp` 不监听端口、不启动后台调度器；现有开发和生产启动命令保持不变。

### 2. 先补认证失败测试

在 `tests/core-stability.test.ts` 增加本地临时 Express 测试，覆盖：

- 生产环境缺少或使用少于 16 字符的访问密钥时配置校验失败。
- 开发环境未配置密钥时认证关闭，原业务路由保持可访问。
- 未登录访问 `/api/*` 返回 401 `AUTH_REQUIRED`，访问 HTML 页面返回 303 登录跳转，非 HTML 资源返回 401。
- `/healthz`、登录、退出、会话状态和合法 `OPTIONS` 是唯一未登录例外。
- 正确密钥签发 HttpOnly、SameSite=Strict、Path、Max-Age 和正确 Secure 属性；错误、篡改、过期 Cookie 被拒绝并清除。
- `next` 拒绝外站、双斜杠、反斜杠和 `/auth/*`。
- 同一地址五次失败后锁定，后续请求返回 429；成功登录清除失败窗口。
- 默认不允许跨域，危险方法的未知 Origin 返回 403，精确允许来源可携带 Cookie。

验证：新增测试先因模块和行为缺失而失败，现有 40 项测试仍保持通过。

### 3. 实现配置与无状态会话

修改 `server/config.ts`，增加 `access` 配置：认证启用状态、访问密钥、允许来源、Secure Cookie 和生产环境校验。新增 `server/auth/accessAuth.ts`：

- 用 SHA-256 固定长度摘要和 `timingSafeEqual` 比较访问密钥。
- 用 `v1.<expires>.<nonce>.<signature>` 格式签发七天固定会话。
- 用 HMAC-SHA256 校验 Cookie，所有签名比较使用恒定时间方法。
- 手工解析目标 Cookie，不把其他 Cookie 或请求体写入日志。
- 实现进程内失败窗口、严格 Origin 校验、安全响应头和整站认证闸门。

验证：密钥、Cookie、限速和 Origin 单元测试通过；仓库不出现真实访问密钥。

### 4. 实现登录路由与页面

新增 `server/auth/loginPage.ts` 和 `server/routes/authRoutes.ts`：

- `GET /healthz` 只返回最小健康状态。
- `GET/POST /auth/login` 输出服务端登录页并处理登录。
- `POST /auth/logout` 清除 Cookie。
- `GET /api/auth/session` 只返回启用、登录和到期状态。
- 登录页面使用独立严格 CSP、固定宽度表单、清晰焦点态和移动端适配；视觉沿用深色雾面控制层，不使用金青色、渐变或装饰图形。

验证：登录响应不引用外部资源，不泄露密钥或签名材料，桌面和手机宽度无溢出。

### 5. 把认证闸门接到整站

在 `server/app.ts` 按固定顺序挂载：安全响应头与受限 CORS、公开认证路由、整站认证闸门、JSON 解析、现有业务 API、Mineradio、生产静态文件和 SPA 回退。

验证：有效 Cookie 可访问 API、Mineradio 和生产页面；无 Cookie 时任何业务资源都不能绕过闸门。原 `/api/health` 仍位于闸门后。

### 6. 增加系统设置退出入口

修改 `src/services/api.ts`，增加只读会话状态和退出请求。修改 `src/components/pages/SettingsPage.tsx` 与必要的 `src/styles/global.css`：

- 设置页显示当前访问保护状态。
- 认证启用且已登录时提供带 Lucide `LogOut` 图标的“退出登录”按钮。
- 退出成功后浏览器跳转 `/auth/login`。
- 不改顶部导航、影院大厅和媒体队列。

验证：认证关闭的开发预览不出现误导性的退出动作；按钮有键盘焦点、忙碌和失败状态。

### 7. 同步部署配置和文档

修改 `docker-compose.yml`，传入 `MCC_ACCESS_KEY`、`MCC_ALLOWED_ORIGINS` 和 `MCC_COOKIE_SECURE`。新建根目录 `.env.example`，只提供空值和说明。更新 `README.md`、`docs/PLAN.md` 与本计划：

- VPS 必须使用 HTTPS，生产 Secure Cookie 默认开启。
- 8787 只允许反向代理或防火墙受信来源访问。
- 真实密钥只放未跟踪 `.env` 或 VPS 环境变量，不进入 Compose 默认值、镜像、日志、前端包和文档。

验证：Compose 生产配置缺少访问密钥时明确拒绝启动，不会静默暴露未认证服务。

### 8. 自动与浏览器回归

运行：

```powershell
npm test
npm run build
python -m unittest discover -s tests -v
git diff --check
```

Python 命令在 `services/nasemby-core/` 执行。另用独立测试端口和临时环境变量验证 1440px 与 390px 登录、刷新、退出、API 拒绝、Mineradio iframe 和无认证开发预览；不复用或改写当前 5173 预览进程。

## 基线与验收

- 实施前 Node 回归 `40/40` 通过，生产构建通过。
- 新认证测试、原 Node 回归、NasEmby Python 契约和生产构建全部通过。
- 未登录用户看不到 React、影院大厅、Mineradio、静态资源或业务 API。
- 开发环境未配置密钥时现有预览不受影响。
- 登录页与退出入口在桌面和手机无溢出、无控制台错误。
- 没有真实外部写动作，没有凭据落盘。

## 回滚

认证实现异常时回滚 `server/auth/`、认证路由、整站闸门和设置页退出入口；保留 `createApp(config)` 的可测试应用边界。生产环境不得通过清空 `MCC_ACCESS_KEY` 绕过认证，必须修复配置或回滚版本。回滚不迁移或删除订阅、媒体、任务链和 NasEmby 数据。

## 验收结果

- Express 应用组装已拆到 `server/app.ts`，入口只负责配置、调度所有权和监听；测试导入不会启动端口或后台调度器。
- 整站闸门、登录页、签名 Cookie、七天固定会话、密钥轮换失效、退出、限速、开放重定向、CORS/Origin、最小健康路由和脱敏错误均已实现。
- 系统设置页已增加访问状态与退出入口；顶部导航、影院大厅和媒体队列组件未修改。
- Node 回归 `52/52`、NasEmby Python 契约 `10/10`、TypeScript 与 Vite 6.4.3 生产构建、Compose 配置和 `git diff --check` 通过。
- 安全扫描 Critical/High/Medium 为 `0/0/0`；两项 Low 是服务启动日志与既有只读审计脚本日志。npm 依赖审计为 `0 vulnerabilities`。
- 1440px 与 390px 登录页无溢出；390px 设置页访问保护卡完整显示，认证关闭时不显示退出按钮，浏览器控制台无错误。
- 影院大厅保持原 UI：桌面 `1440×900` 下 iframe 和 WebGL 主画布非空，中心采样非暗像素占 `88.49%`、量化后有 `147` 个色阶，间隔 `0.9s` 的画面变化采样为 `5.28%`；媒体库切换和右方向键桥接通过。手机使用新标签冷启动验证，`390×844` 下主画布可见，CSS 尺寸为 `390×844`、内部渲染为 `526×1139`，控制台无错误。同一 WebGL 标签热切桌面/手机视口出现的 `1×1 hidden` 不作为正常冷启动缺陷处理。
- 内置浏览器未能触发原生表单 POST 导航，因此不把浏览器登录提交冒充为已通过；正确/错误登录、Cookie、篡改/过期、退出和 429 链路由本地真实 HTTP 集成测试覆盖。
- NasEmby Core Docker 镜像已构建成功；完整中控镜像拉取 `node:20-alpine` 时，当前 DNS 把 `auth.docker.io` 解析到不可达地址，IPv4/IPv6 均超时。该项记录为外部 DNS/网络阻塞，不通过修改 Dockerfile、替换未知镜像源或降低基础镜像约束绕过；当前没有 Compose 容器运行。
- 未部署 VPS、HTTPS 证书或公网 DNS；未调用 Torra、qB、Symedia、115、Telegram 或 Emby 真实写接口。
