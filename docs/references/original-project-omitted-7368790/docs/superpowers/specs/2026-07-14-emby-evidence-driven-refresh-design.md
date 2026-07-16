# Emby 证据驱动媒体库刷新设计

状态：已实施
日期：2026-07-14
适用范围：控制室内的 Emby 全库刷新

## 1. 目标

利用 Symedia 已有的转存和 STRM 入库记录判断 Emby 是否可能尚未完成索引。只有存在“Symedia 已成功入库、Emby 最新索引时间仍较旧”的证据时，控制室才允许用户手动触发一次 Emby 全库刷新。

本功能不自动刷新、不循环等待扫描完成，也不把“刷新请求已提交”写成“媒体库扫描已完成”。

## 2. 为什么仍需要 Emby 刷新

Symedia 能证明源文件已经转存、STRM 已生成并写入转存历史；Emby 是否已经发现并索引该 STRM 是另一份证据。两者职责不同：

- Symedia：证明下游文件准备完成。
- Emby：证明媒体已经进入最终播放索引。

中控使用 Symedia 作为刷新前置证据，而不是无条件扫描 Emby。

## 3. 已确认交互

- 入口只放在控制室，且只在选中 Emby 服务卡时显示。
- 只允许手动触发，不做自动刷新。
- 每次触发前都显示二次确认。
- 全库刷新采用 10 分钟服务端冷却，重启服务后冷却仍然有效。
- 服务端从重新读取证据开始只允许一个刷新请求执行，并发请求返回 409。
- 点击确认后立即返回“扫描已触发”，不等待 Emby 扫描结束。
- 没有待索引证据、Emby 离线、Symedia 离线或处于冷却期时按钮禁用，并显示具体原因。

## 4. 证据判定

新增刷新状态服务，同时读取：

1. Symedia 最近成功转存记录的最新 `date`。
2. Emby 最近入库项目的最新 `DateCreated`。
3. 中控上一次成功触发 Emby 刷新的时间。

第一版判定规则：

- Symedia 和 Emby 均已配置且在线。
- Symedia 存在成功记录，且时间可解析。
- Symedia 最新成功记录晚于 Emby 最新索引时间。
- Symedia 最新成功记录晚于上一次刷新触发时间，避免对同一批记录重复扫描。
- 当前时间已超过 10 分钟冷却截止时间。

满足全部条件时状态为 `ready`，显示“检测到 Symedia 有较新的入库证据”。

以下情况不宣称“待索引”：

- 时间字段缺失或无法解析。
- 两台服务时间不同步导致比较结果不可信。
- Symedia 记录成功但缺少明确时间。

这些情况状态为 `insufficient_evidence`，按钮禁用并提示检查服务时间或打开原工具。

## 5. API

### 读取刷新状态

`GET /api/media/emby/refresh-status`

响应：

```json
{
  "configured": true,
  "connected": true,
  "state": "ready",
  "canRefresh": true,
  "reason": "检测到 Symedia 有较新的入库证据",
  "latestSymediaAt": "2026-07-14T08:00:00.000Z",
  "latestEmbyAt": "2026-07-14T07:30:00.000Z",
  "lastTriggeredAt": "",
  "cooldownUntil": ""
}
```

`state` 取值：

- `ready`
- `up_to_date`
- `cooldown`
- `service_unavailable`
- `insufficient_evidence`

### 触发刷新

`POST /api/media/emby/refresh`

服务端必须重新执行证据判定，不接受前端传入的 `canRefresh`。成功提交 Emby 后返回 `202 Accepted`：

```json
{
  "triggered": true,
  "message": "Emby 媒体库扫描已触发",
  "triggeredAt": "2026-07-14T08:05:00.000Z",
  "cooldownUntil": "2026-07-14T08:15:00.000Z"
}
```

错误状态：

- 409：没有新的 Symedia 证据或仍在冷却。
- 503：Emby 或 Symedia 未配置、离线。
- 502：Emby 刷新接口调用失败。

错误响应保持项目现有 `{ error }` 结构，并增加机器可读 `code`。

## 6. Emby 调用

Emby 适配器新增全库刷新方法：

- `POST /Library/Refresh`
- 使用现有 API Key 或账号密码换取的 Token。
- 请求成功只表示 Emby 接受刷新请求。
- 不轮询后台任务，不等待扫描完成。
- 提交成功后清理中控 Emby 库内对照缓存，避免长期保留旧索引；扫描期间页面仍可能暂时显示旧状态，这是预期行为。

## 7. 冷却与持久化

刷新状态写入已忽略的 `data/emby-refresh-state.json`：

```json
{
  "lastTriggeredAt": "2026-07-14T08:05:00.000Z",
  "evidenceAt": "2026-07-14T08:00:00.000Z"
}
```

- 冷却时间固定 10 分钟。
- `evidenceAt` 记录本次刷新处理的 Symedia 最新证据时间。
- 同一条或更早的 Symedia 证据不会重复触发刷新。
- 文件损坏时按空状态处理，但不自动触发刷新。

## 8. 活动日志

复用 `ActivityLog`：

- `category`: `emby`
- `action`: `refresh_library`
- `status`: `start`、`success`、`error` 或 `skip`
- `message`: 只记录刷新原因、证据时间和结果

不记录 Emby API Key、账号、密码、Token、完整上游响应或媒体文件路径。

## 9. 控制室界面

选中 Emby 后，右侧操作区增加“刷新媒体库”：

- `ready`：按钮可用，旁边显示 Symedia 与 Emby 两个证据时间。
- `up_to_date`：按钮禁用，文案“Emby 已跟上最新入库”。
- `cooldown`：按钮禁用，显示剩余冷却时间。
- `service_unavailable`：按钮禁用，提示离线服务。
- `insufficient_evidence`：按钮禁用，提示时间证据不足。

确认层说明：这是全库后台扫描，提交后页面不会等待扫描完成。执行期间按钮禁用，返回后重新读取刷新状态和 Emby 概览。

影院大厅、媒体队列、顶部导航、任务中心和 Mineradio iframe 不在本阶段修改范围内。

## 10. 测试与验收

自动测试全部使用模拟 Emby / Symedia 响应：

1. Symedia 记录较新时状态为 `ready`。
2. Emby 时间更新时状态为 `up_to_date`。
3. 同一 Symedia 证据不会重复刷新。
4. 10 分钟冷却跨服务重启仍有效。
5. 时间字段无法解析时禁止刷新。
6. Emby 或 Symedia 离线时禁止刷新。
7. 成功调用 `POST /Library/Refresh` 后返回 202 并清理缓存。
8. 上游失败返回 502，日志不包含凭据或完整响应。
9. 控制室按钮、确认层和五种状态文案正确。
10. 第一个请求检查证据期间，第二个并发刷新请求即返回 409，且 Emby 只收到一次刷新调用。

验收命令：

```bash
npm test
npm run build
git diff --check
```

浏览器验收覆盖桌面和 390×844。真实 Emby 刷新不在自动验收中执行，除非用户另行明确指定测试窗口。

## 11. 后续

- 如果后续能够从 Symedia 或 Emby 获得稳定的后台任务状态，再增加“扫描中 / 扫描完成”证据。
- 单媒体刷新需要可靠的 Emby Item ID 映射，单独设计，不与本次全库刷新混在一起。
- 订阅手动重推 Torra 继续后置，因为它可能产生新的搜索和下载任务。
