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
