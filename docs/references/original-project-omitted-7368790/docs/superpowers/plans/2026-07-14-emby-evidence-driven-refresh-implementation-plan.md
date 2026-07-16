# Emby 证据驱动媒体库刷新实施计划

状态：已完成
日期：2026-07-14
设计依据：`docs/superpowers/specs/2026-07-14-emby-evidence-driven-refresh-design.md`

## 目标

在控制室 Emby 操作区增加证据驱动的手动全库刷新。只有 Symedia 最新成功入库时间晚于 Emby 最新索引时间和上次刷新证据时才允许触发；请求提交后立即返回，不等待后台扫描完成。

## 实施步骤

### 1. 时间证据与持久状态

- 增加统一的 Symedia / Emby 时间解析和比较函数。
- 新增 `data/emby-refresh-state.json` 状态存储，持久化上次触发时间和已处理证据时间。
- 固定 10 分钟冷却，文件损坏时回落空状态且不自动刷新。

验证：覆盖较新证据、已跟上、无法解析、同证据重复和跨实例冷却。

### 2. Emby 刷新适配器

- 扩展 `EmbyAdapter`，使用现有认证调用 `POST /Library/Refresh`。
- 成功只返回已接受请求，不等待扫描结束。
- 提交后清理 Emby 库内对照缓存。

验证：模拟 API Key 与动作响应，不连接真实 Emby。

### 3. 刷新状态与动作服务

- 新增 `EmbyRefreshService`，并发读取 Symedia 最近成功记录和 Emby 最近索引记录。
- 输出 `ready / up_to_date / cooldown / service_unavailable / insufficient_evidence`。
- POST 动作前重新判定；从证据复查开始加执行锁，并发请求返回 409；成功写持久状态和脱敏活动日志。

验证：状态判定、409/502/503 错误和日志安全。

### 4. API 契约

- `GET /api/media/emby/refresh-status` 返回证据、原因和冷却信息。
- `POST /api/media/emby/refresh` 成功返回 `202 Accepted`。
- 409 表示无新证据或冷却；502 表示 Emby 上游失败；503 表示服务不可用。
- 错误保持 `{ error }` 并增加 `code`。

### 5. 控制室交互

- 选中 Emby 时读取刷新状态并显示证据时间。
- 仅 `ready` 状态启用“刷新媒体库”。
- 二次确认说明全库后台扫描、立即返回、不等待完成。
- 成功后刷新状态和 Emby 概览；失败不做乐观更新。
- 不修改影院大厅、任务中心、媒体队列和顶部导航。

### 6. 文档与验收

- 更新 `README.md`、`docs/PLAN.md`、`docs/IMPLEMENTATION_SOURCES.md`、设计规格和本实施计划。
- 运行测试、构建、差异、凭据与冻结页面检查。
- 浏览器检查桌面和 390×844，真实 Emby 刷新不执行。

## 验收命令

```bash
npm test
npm run build
git diff --check
```

## 关机交付

全部检查通过、文档同步完成后，按用户明确要求执行 Windows 关机。关机前先在最终回复中交付结果和取消关机的方法，再使用延时关机命令，为用户保留短暂取消窗口。

## 验收结果

- `npm test`：24/24 通过（含证据检查阶段的并发请求拒绝回归测试）。
- `npm run build`：通过。
- `GET /api/media/emby/refresh-status` 在未配置环境返回 200 和 `service_unavailable`；POST 安全拒绝并返回 503 + `EMBY_REFRESH_UNAVAILABLE`。
- API 成功语义为 202 Accepted；409/502/503 不以 200 包装错误。
- 控制室仅在选中 Emby 时显示 `Symedia → Emby` 证据块和刷新动作。
- 桌面和 390×844 无横向溢出，浏览器控制台无错误或警告。
- 当前环境没有 Emby/Symedia 配置，只验证禁用状态；未执行真实 Emby 全库刷新。
- 影院大厅、任务中心、媒体队列、顶部导航和 Mineradio iframe 未修改。
