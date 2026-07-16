# 网盘订阅与获取通道计划

状态：未完成，等待 v2 代码收口后实施  
更新时间：2026-07-17

## 1. 目标

在媒体控制中心内保留并统一管理 NasEmby 已有的网盘搜索、115 转存、Telegram 频道、HDHive / pansou 和 Symedia 能力。用户仍只使用一套 React 页面和一份 NasEmby 订阅台账。

## 2. 获取优先级

```text
创建订阅
  → PT / Torra 搜索与下载（默认）
  → 满足兜底条件且开关开启
  → 网盘资源搜索
  → 用户确认或受控自动选择
  → 115 转存
  → Symedia 处理
  → Emby 入库确认
```

自动云盘兜底默认关闭。不存在“云盘优先、PT 兜底”的默认策略。

## 3. 开关模型

### 全局开关

- `cloud_acquisition_enabled`：是否允许使用网盘通道，默认 `false`。
- `cloud_auto_fallback_enabled`：是否允许 PT 等待后自动进入网盘，默认 `false`。
- `cloud_manual_actions_enabled`：是否允许用户手动搜索和确认转存，默认 `false`，实机前保持关闭。

### 订阅级设置

- `allow_cloud_fallback`：当前订阅是否允许网盘兜底。
- `cloud_sources`：允许的来源，例如 Telegram、HDHive / pansou。
- `cloud_wait_minutes`：PT 等待多久后允许兜底。
- `cloud_auto_select`：是否允许按资源规则自动选择候选，默认 `false`。

所有字段必须写入 NasEmby 原订阅条目或其正式配置，不建立前端独立台账。

## 4. 保留的业务模块

- `app/services.py`：115、影巢、Torra、Symedia 等业务函数。
- `app/discover_runtime.py`：订阅规则、资源搜索、provider 队列和通知。
- `app/telegram_runtime.py`：Telegram 登录、频道和通知能力。
- `app/hdhive_auth.py`、`app/hdhive/`：HDHive / pansou 资源能力。
- `app/legacy/`：仍被 115 / 123 可选流程动态调用的模块。

这些是待整合能力，不是可以删除的废代码。

## 5. 待实现的 Python API

旧 NasEmby 管理路由不直接恢复。v2 新增受认证、Origin、写闸门和脱敏保护的统一接口：

- 读取各网盘来源的配置状态与连接状态。
- Telegram 登录状态、验证码流程和频道选择。
- 115 账号只读检查与目标目录映射。
- HDHive / pansou 状态与资源搜索。
- 网盘候选预览，不执行转存。
- 确认后的单条 115 转存。
- Symedia 转存状态复查。
- 失败重试和取消，必须带幂等键与服务端复查。

具体 URL 在实现前加入新的机器契约；不把未设计的旧接口塞进现有 47 条 v1 契约。

## 6. 待实现的 React 页面

### 订阅设置

- 增加“允许网盘兜底”总开关和等待阈值。
- 默认关闭并明确显示 PT / Torra 主通道。
- 订阅卡允许单独覆盖全局策略。

### 系统设置

- 增加 115、Telegram、HDHive / pansou、Symedia 配置状态卡。
- 显示是否配置、是否在线、上次检查和安全提示。
- 敏感凭据不回填明文。

### 任务中心

- 增加 PT 与网盘两条候选支线，但最终仍汇入同一订阅任务。
- 展示选择依据、阻塞原因、转存证据和重复阻止结果。
- 手动搜索和转存必须先预览，再确认。

## 7. 兜底条件

只有全部满足时才允许进入网盘流程：

1. 全局或订阅级网盘开关开启。
2. 订阅具有稳定 TMDB ID、媒体类型和季号。
3. Emby 尚未满足目标媒体。
4. Torra 没有等价完成订阅。
5. qB 没有正在下载或已完成的等价任务。
6. PT 已明确无结果、失败或超过等待阈值。
7. 115 目标分类与目录映射完整。
8. 本次媒体单元没有正在执行的网盘幂等任务。

## 8. 状态机

```text
pt_active
  → pt_waiting
  → cloud_allowed
  → cloud_searching
  → cloud_candidate_ready
  → cloud_transfer_pending
  → cloud_transferring
  → symedia_processing
  → emby_indexed
```

异常状态：`blocked`、`duplicate_prevented`、`manual_review`、`failed_retryable`、`failed_final`。

## 9. 安全要求

- 所有写动作默认关闭。
- 浏览器不接收 115 Cookie、Telegram Session、Token 或密码。
- 候选链接和上游错误必须脱敏。
- 一次只处理一个确认的媒体单元，不提供无上限批量转存。
- 每次转存前重新检查 Emby、Torra、qB、115 和 Symedia 状态。
- 写请求不盲目自动重试；未知结果先读取状态。
- 活动日志记录媒体身份、来源、结果和请求 ID，不记录凭据或完整分享链接。

## 10. 实施阶段

### 阶段 A：只读状态与契约

- 梳理 NasEmby 现有函数输入输出。
- 冻结网盘来源、账号状态和候选预览契约。
- 使用模拟响应实现 Python 只读 API 和测试。

### 阶段 B：页面与手动预览

- 增加订阅开关和系统设置状态卡。
- 增加任务中心网盘候选展示。
- 只允许搜索和预览，不执行转存。

### 阶段 C：单条安全转存

- 增加幂等任务、确认、复查、冷却和审计。
- 自动测试只使用临时目录和模拟 115 / Symedia。
- 真实转存等待用户指定实机测试条目。

### 阶段 D：自动兜底

- PT 主链稳定后再实现等待阈值与自动兜底。
- 初始版本仍保持 `cloud_auto_fallback_enabled=false`。
- 经过单条实机验证和重复阻止验证后，才允许用户手动开启。

## 11. 验收

- 关闭网盘开关时，任何订阅都不会搜索或转存网盘资源。
- PT 有有效任务时，不启动网盘通道。
- 同一媒体不会同时创建 PT 与网盘重复任务。
- 所有网盘状态显示真实证据，不使用模拟成功。
- 网盘功能使用 NasEmby 唯一台账，不产生第二份订阅数据。
- 影院大厅、顶部导航和媒体队列 UI 不受影响。
