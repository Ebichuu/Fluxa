# qBittorrent 可回滚控制动作设计

状态：已实施
日期：2026-07-14
适用范围：任务中心内的 qBittorrent 暂停与恢复

## 1. 目标

在统一任务链的任务卡上提供 qBittorrent 暂停和恢复能力，使用户可以直接控制当前媒体任务关联的下载，而不必离开媒体控制中心。

本阶段只实现可回滚、影响范围明确的暂停与恢复。删除任务、删除文件、重新校验、移动文件、修改分类、修改标签和全局批量操作均不在本阶段范围内。

## 2. 已确认交互

- 操作入口只放在任务中心任务卡，不放控制室，避免同一动作出现多个入口。
- 一条媒体任务关联多个 qB hash 时，一次操作全部关联下载，不在第一版增加逐个选择界面。
- 暂停和恢复都必须经过二次确认。
- 操作完成后刷新统一任务链，使用 qB 返回的真实状态更新卡片。
- 操作失败时保留当前页面和原状态，展示明确错误信息，不进行自动重试。

## 3. 架构

采用轻量 `QbittorrentActionService`，与现有只读 `QbittorrentClient` 配合：

```text
TasksCenter
  → POST /api/qbittorrent/actions/pause|resume
  → QbittorrentActionService
      → 请求校验与 hash 去重
      → qBittorrent Web API 批量动作
      → 重新读取对应任务状态
      → ActivityLog 审计记录
  → 刷新 GET /api/tasks/chain
```

路由只负责 HTTP 输入输出；登录、动作调用、状态复查和审计由服务层完成。现有 `GET /api/qbittorrent/summary` 与 `GET /api/tasks/chain` 的只读行为保持不变。

## 4. API 契约

### 暂停

`POST /api/qbittorrent/actions/pause`

### 恢复

`POST /api/qbittorrent/actions/resume`

请求：

```json
{
  "hashes": ["qB torrent hash"],
  "taskId": "任务链行 ID",
  "title": "用于用户可读审计的媒体标题"
}
```

响应：

```json
{
  "action": "pause",
  "requested": 2,
  "succeeded": 2,
  "failed": 0,
  "tasks": [
    {
      "hash": "qB torrent hash",
      "status": "paused",
      "state": "pausedDL"
    }
  ]
}
```

服务端以 hashes 作为实际操作目标。`taskId` 和 `title` 仅用于审计与错误定位，不作为授权依据，也不写入 qB。

## 5. 输入校验

- `hashes` 必须是非空数组。
- 每个 hash 必须是 40 位十六进制 qB torrent hash。
- 服务端去重并限制一次最多操作 20 个 hash，防止误把全局任务列表传入。
- 操作前读取 qB 当前任务，拒绝不存在的 hash。
- 暂停只允许作用于未暂停任务；恢复只允许作用于已暂停任务。已经处于目标状态的任务计为跳过，不重复调用。
- 请求中只要存在格式错误或不存在的 hash，整次操作拒绝，不做部分写入。

## 6. qBittorrent 调用与状态复查

- 沿用现有账号密码登录和 Cookie 仅存于单次服务端请求内的方式。
- 暂停调用 qB Web API `POST /api/v2/torrents/pause`。
- 恢复调用 qB Web API `POST /api/v2/torrents/resume`。
- 请求体使用 `application/x-www-form-urlencoded`，hash 以 `|` 连接。
- qB 动作接口返回成功后，重新请求对应任务状态。
- 暂停验收状态为 qB state 包含 `pause`；恢复验收状态为不再包含 `pause`。恢复后允许进入排队、元数据获取或下载状态，不要求立即出现下载速度。
- 状态复查失败时返回“动作已提交但状态未确认”，并记录为错误审计，前端立即刷新任务链以获取最新证据。

## 7. 审计日志

复用现有 `ActivityLog`，新增：

- `category`: `qbittorrent`
- `action`: `pause` 或 `resume`
- `status`: `start`、`success`、`error` 或 `skip`
- `message`: 媒体标题、任务数量和简要结果

日志不记录 qB Cookie、账号、密码、完整请求体或文件保存路径。hash 只记录前 8 位用于排查，不保存完整值。

## 8. 前端交互

- 任务卡存在 `sourceIds.qbHashes` 时才显示 qB 操作。
- 关联任务中只要存在未暂停项，显示“暂停下载”；全部已暂停时显示“恢复下载”。
- 用户点击后弹出确认层，显示媒体标题、关联下载数量和动作说明。
- 确认后按钮进入执行中状态，阻止重复点击。
- 成功后显示简短结果并刷新任务链。
- 失败后显示错误信息；不修改本地任务状态，不做乐观更新。
- 未关联的纯 qB 行同样允许操作自身唯一 hash，因为其操作目标仍然明确。
- 手机端操作按钮放在任务卡底部操作区，不改变四步证据链 2×2 排列。

影院大厅、媒体队列、顶部导航、Mineradio iframe 和其他工作页不在本阶段修改范围内。

## 9. 错误处理

- qB 未配置或离线：返回服务不可用，前端禁用操作并提供“打开 qB”入口。
- 登录失败：返回认证失败，不泄露上游响应正文。
- hash 不存在：整次请求拒绝，提示任务链数据可能已过期并建议刷新。
- 部分状态不符合动作要求：符合目标状态的项跳过，其余项操作；响应分别返回 succeeded、failed 和 skipped 数量。
- qB 接口超时：记录错误，不自动重试，避免重复提交。
- 动作提交后复查失败：明确标记“已提交、未确认”，不宣称成功。

## 10. 测试与验收

自动测试使用模拟 qB 服务，不连接真实局域网实例：

1. hash 校验、去重和 20 个上限。
2. 多 hash 批量暂停与恢复。
3. 已在目标状态的任务跳过。
4. 不存在 hash 时整次拒绝且不调用写接口。
5. qB 登录失败、动作失败和状态复查失败。
6. 活动日志不包含密码、Cookie、完整 hash 或完整请求载荷。
7. 前端按钮只在存在 qB hash 时出现，执行期间不可重复点击。
8. 操作完成后重新读取统一任务链。

验收命令：

```bash
npm test
npm run build
git diff --check
```

浏览器验收覆盖 1440×900、1024×768 和 390×844，确认任务卡操作区无横向溢出。真实 qB 暂停/恢复只在用户另行明确授权后执行。

## 11. 后续扩展

本阶段稳定后，再分别设计：

- Emby 媒体库刷新。
- 订阅手动重推 Torra。
- qB 重新校验。

这些动作不会共用一个无边界的“万能操作接口”；每种动作保持独立校验、独立审计和明确影响范围。
