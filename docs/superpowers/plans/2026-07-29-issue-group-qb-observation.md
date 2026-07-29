# 问题组统计与 qB 观察窗实施计划

对应设计：`docs/superpowers/specs/2026-07-29-issue-group-qb-observation-design.md`

## 1. 后端失败用例

- 扩展首页摘要测试，覆盖可靠身份、冲突身份、机械标题规范化、无标题逐资源、全量资源和旧字段兼容。
- 扩展 qB 来源事实测试，覆盖 899/900 秒、missing/error、未来或缺失活动时间、正速度恢复、校验、暂停和排队。
- 扩展资源事件仓储测试，确保 qB 观察窗的 waiting/failed 不进入永久历史。

## 2. 后端实现

- 新增只读问题组键与身份确认判定，不修改任务身份或所有权。
- 在首页 `counts` 增加可选 `actionRequiredGroups` 与 `actionRequiredIdentityUnconfirmedResources`，保留旧计数。
- 将 qB 普通 stalled/零速度改为基于 `observedAt - lastActivity` 的 900 秒当前投影；missing/error 保持立即失败，正速度立即恢复。
- 在事件持久化边界排除 qB 观察窗 reason code，不改变永久状态白名单的其他行为。

## 3. 前端与契约

- 首页切换到“问题组”文案，并展示资源数与身份未确认数。
- 首次加载、超时和请求失败时统计卡片显示未知；成功零值继续显示 0。
- TypeScript、API 契约、路线图和前后端设计记录同步新增可选字段与观察窗语义。

## 4. 验证与发布

- 运行定向测试、全部 Python 回归、TypeScript、生产构建、Compose 配置和变更关卡。
- 用本地实例复核问题组文案、qB 等待/恢复状态和首页错误态。
- 提交并推送 `main`，等待多架构镜像、双架构冒烟和 `latest` manifest 全部成功。
- 始终排除本地 `services/nasemby-core/mcc_data.db`，不执行关机。

## 5. qB 共享评估与控制室收口

### 5.1 失败测试

- 为纯函数补齐 `899 / 900` 秒边界、`missing/error`、校验、做种、暂停、排队、正速度覆盖普通 `stalled`、活动时间缺失/无效/来自未来和未知状态。
- 覆盖混合任务聚合优先级：`action_required -> unknown -> observing -> normal`，并验证相同任务与 `observedAt` 结果稳定。
- 验证 `/api/qbittorrent/summary` 只增可选 `assessment`，保留原始 `counts.stalled`；任务链继续使用同一单任务评估结论。
- 验证观察中、等待、处理中及达到 900 秒的轮询失败都不写永久事件。

### 5.2 后端实现

- 新增无副作用共享模块 `qbittorrent_assessment_runtime.py`，分别提供 `assess_qb_task(task, observed_at)` 与 `summarize_qb_assessments(results, observed_at)`。
- `QbittorrentClient.summary()` 只捕获一次检查时间；任务列表、单任务评估和聚合响应共用该 `observedAt`，评估器内部禁止读取系统时间。
- `pipeline_source_fact_runtime.py` 删除本地 qB 阈值判断，改为消费共享单任务评估结果；公开原因与建议继续经过脱敏边界。

### 5.3 控制室投影

- 普通 qB 服务状态只读取 `assessment`：真实异常为 `warn`，观察中与未知保持中性，均不计入顶部“需检查”。
- 后端未返回 `assessment` 时不从原始 `stalled` 反推异常；`counts.stalled` 只在高级诊断显示为“qB 原始 stalled”。
- 顶部按服务计数，同一批 qB 任务无论多少真实异常都只增加一项。

### 5.4 契约、文档与发布验收

- 同步 TypeScript、HTTP API 契约、前后端 `DESIGN.md`、`ROADMAP.md` 和测试基线；不改变 URL、方法、状态码、旧字段名称或类型。
- 运行 qB 定向测试、完整 Python 回归、前端测试与生产构建、Compose 检查、API 契约审查和 `verify-change`。
- 重启本地服务后验收控制室顶部数量与 qB 普通/高级诊断文案，再提交、推送并等待双架构镜像与 `latest` 发布成功。
