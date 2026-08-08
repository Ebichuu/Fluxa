# Fluxa 前端按路由拆包与 Actions Node 24 升级设计

日期：2026-08-08
状态：已实施，待发布验收
实施边界：只处理前端加载边界与容器发布工作流，不修改业务接口、状态体系、数据库或外部媒体系统

## 1. 目标

本维护波次从根因关闭两个既有警告：

1. Vite 生产构建的主 JavaScript 包超过 500 KB。
2. GitHub Actions 使用的 8 个第三方 Action 仍运行在 Node 20。

两项修改分成两个独立实施提交，任何一项失败都可以单独定位和回滚：

1. `perf(web): lazy-load non-home routes`
2. `ci: upgrade actions to node 24 releases`

设计文档单独提交，不计入上述两个实施提交。

## 2. 前端拆包设计

### 2.1 加载边界

首页 `Overview`、应用导航、全局状态、首页摘要轮询和 URL 状态保持同步加载。以下非首页页面改为 `React.lazy()` 动态导入：

- 媒体概览；
- 媒体馆；
- 控制室；
- 任务中心；
- 日历；
- 发现与追更工作台；
- 追更设置；
- RSS 资源中心；
- 系统设置。

页面导出方式保持不变，通过动态导入适配现有具名导出，不为了拆包重写页面模块。共享依赖由 Vite/Rollup 按现有规则处理；本阶段不手工维护 `manualChunks`，除非实测路由懒加载后仍存在超过 500 KB 的单一 JS chunk。

### 2.2 加载与失败状态

所有懒加载页面共用一个 `Suspense` 加载占位，页面切换期间保持应用导航可用。增加统一的懒加载错误边界：

- chunk 正常加载时渲染目标页面；
- chunk 加载失败时显示明确的“页面暂时无法加载”状态和一次“重新加载页面”动作；
- 不自动无限重试，不吞掉错误后展示空白页；
- 路由变化后错误边界能够恢复，不影响其他页面。

首页不经过懒加载边界，冷启动仍能直接显示首页骨架与缓存摘要。

### 2.3 性能验收

构建前记录当前首页基线：主 JS 约 533.9 KB，首页静态入口当前包含全部页面。构建后同时检查：

1. 不修改 `chunkSizeWarningLimit`，所有 JavaScript chunk 均小于 500 KB。
2. 首页首次打开实际请求的 JavaScript 总传输量低于修改前。
3. 首页首次打开的 JavaScript 请求数保持受控，不因拆包预加载全部页面；以浏览器网络记录为准。
4. 首页不请求媒体馆、任务、RSS、设置等非当前路由页面 chunk。
5. 首次导航到某页面只加载该页面及必要共享 chunk，后续返回不重复下载。

请求数不设脱离实测的任意固定数字。验收使用同一生产构建、同一浏览器缓存状态和同一入口 URL 对比拆分前后数据，并在实施记录中保存具体结果。

### 2.4 路由验收矩阵

每个页面必须分别验证：

- 从首页或顶部导航进入；
- 直接输入页面 URL；
- 直接 URL 打开后刷新；
- 浏览器前进和后退；
- 懒加载失败时不出现永久空白，恢复动作有效。

至少覆盖现有全部页面：`/`、`/media`、`/hall`、`/control`、`/tasks`、`/calendar`、`/discover`、`/subscriptions`、`/subscription-settings`、`/rss-library`、`/settings`。

## 3. GitHub Actions Node 24 升级

### 3.1 稳定版本门禁

升级前通过各上游仓库的正式 release 和 `action.yml` 同时确认：

- 目标 major 已发布稳定版本；
- `runs.using` 明确为 Node 24；
- 不使用 beta、preview、预发布标签或未发布分支。

截至设计确认时，8 个 Action 均已有稳定 Node 24 major：

| 当前版本 | 目标稳定版本 |
| --- | --- |
| `actions/checkout@v4` | `actions/checkout@v7` |
| `actions/setup-node@v4` | `actions/setup-node@v7` |
| `actions/setup-python@v5` | `actions/setup-python@v7` |
| `docker/setup-qemu-action@v3` | `docker/setup-qemu-action@v4` |
| `docker/setup-buildx-action@v3` | `docker/setup-buildx-action@v4` |
| `docker/login-action@v3` | `docker/login-action@v4` |
| `docker/metadata-action@v5` | `docker/metadata-action@v6` |
| `docker/build-push-action@v6` | `docker/build-push-action@v7` |

若实施时任一稳定版本被撤回或无法确认 Node 24，该项记录为上游阻塞并保持原版本，不以测试版消除警告。

### 3.2 工作流兼容边界

只更新 Action major，不改变工作流触发条件、权限、并发策略、镜像标签、构建平台、缓存、SBOM、provenance、健康检查或 `latest` 提升门禁。

发布验证必须确认：

1. 源码与契约校验通过。
2. `docker/build-push-action` 仍输出非空不可变镜像 digest。
3. SHA 标签对应 OCI 多架构索引，包含 `linux/amd64` 与 `linux/arm64`。
4. 两个架构的烟测都依赖同一个 publish digest 并全部通过。
5. `latest` 只在两个烟测通过且提交仍为 `main` HEAD 时提升。
6. `latest` digest 与本次 SHA 镜像 digest 完全一致。
7. 新运行不再出现这 8 个 Action 的 Node 20 弃用警告。

## 4. 测试与发布顺序

第一实施提交完成后：

- 运行 `npm test` 与 `npm run build`；
- 比较构建前后 chunk 清单；
- 使用生产构建验证首页 JS 总下载量、请求数和全部页面路由矩阵；
- 验证加载错误边界。

第二实施提交完成后：

- 校验 workflow YAML 与 Action 稳定版本证据；
- 运行本地前后端完整回归；
- 推送 `main`，等待正式 GitHub Actions 发布；
- 核对构建 digest、双架构 manifest、两个烟测、依赖关系与 `latest` 指向。

## 5. 回滚

前端拆包提交可以单独回滚，恢复同步导入，不影响业务数据和 API。Action 升级提交也可以单独回滚到原 major，不影响已发布的不可变 SHA 镜像。任何发布失败都不得手工提升 `latest`，保持上一份已验证镜像继续服务。

## 6. 实施结果

前端拆包构建对比：

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 首页初始 JS 请求 | 1 | 1 |
| 首页初始 JS 原始大小 | 533,906 B | 220,141 B |
| 首页初始 JS gzip 大小 | 154,678 B | 69,220 B |
| 最大 JS chunk | 533,906 B | 220,141 B |

首页初始 JS 原始大小减少约 58.8%，gzip 大小减少约 55.3%。生产构建不再出现超过 500 KB 的 chunk 警告，且 `index.html` 没有预加载非首页页面。11 个路由的 SPA 入口和全部 23 个生成的 JS 资产均通过本地 HTTP 读取验证。

应用内浏览器受管理员安全策略限制，无法访问 localhost，因此未把静态 HTTP 验证冒充为 DOM 导航、刷新或错误边界的浏览器实机通过。该限制不影响 TypeScript 检查、生产构建和静态资源验证，浏览器路由矩阵仍需在策略允许的环境补验。

Action 升级前已通过上游正式 release 与对应 `action.yml` 确认表中 8 个目标 major 均为稳定版本，且 `runs.using` 为 Node 24。工作流只替换 Action major，未修改 digest、manifest、烟测依赖或 `latest` 提升逻辑；最终结果以正式 GitHub Actions 发布为准。
