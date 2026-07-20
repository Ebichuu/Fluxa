# Torra 订阅单向镜像实施计划

设计依据：`docs/superpowers/specs/2026-07-20-torra-subscription-mirror-design.md`

## 目标

将 Torra 已有订阅安全地镜像到 Fluxa SQLite，支持预览、幂等导入和定时状态同步；保留现有受控推送流程，并为同步、推送和接口拒绝建立可查询的 v2 活动日志。

## 任务 1：扩展订阅持久层

涉及文件：

- `services/nasemby-core/app/subscription_repository.py`
- `services/nasemby-core/tests/test_subscription_repository.py`

实施内容：

1. 创建 `torra_subscription_links` 和同步幂等记录表。
2. 增加远端关联查询、批量导入、状态更新和远端缺失标记方法。
3. 保证远端 ID 与本地订阅 key 双向唯一。
4. 批量导入与关联写入使用同一 SQLite 事务。
5. 为重复导入、冲突回滚和远端缺失补单元测试。

验收：仓库层可以在不访问网络的情况下完成稳定、幂等的镜像写入。

## 任务 2：实现 Torra 同步服务与 API

涉及文件：

- `services/nasemby-core/app/torra_read_runtime.py`
- `services/nasemby-core/app/torra_subscription_sync_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_torra_subscription_sync_runtime.py`

实施内容：

1. 将 Torra 原始订阅规范化为安全的镜像候选。
2. 实现远端 ID、TMDB 身份和标题降级匹配。
3. 注册同步预览、确认导入和手动状态同步接口。
4. 要求管理员会话、确认字段和幂等键。
5. 导入只写本地 SQLite，不调用 Torra 写接口。
6. 已导入条目写入 `origin=torra`、`readOnly=true` 和映射状态。
7. 同步失败保留上次成功数据，缺失条目标记 `remote_missing`。

验收：模拟 112 条远端订阅时可预览并幂等导入，重复执行不会新增重复条目。

## 任务 3：记录 Torra 推送关联

涉及文件：

- `services/nasemby-core/app/subscription_compat_runtime.py`
- `services/nasemby-core/app/subscription_torra_action_runtime.py`
- `services/nasemby-core/tests/test_subscription_torra_action_runtime.py`

实施内容：

1. Torra 推送成功或命中已有订阅后保存远端 ID 关联。
2. 镜像条目的推送预览明确返回“已由 Torra 管理”。
3. 阻止镜像条目进入创建流程。
4. 推送失败保留本地订阅与可重试状态。

验收：导入条目永不重复推送；Fluxa 新订阅成功推送后进入统一同步模型。

## 任务 4：增加 v2 活动日志闭环

涉及文件：

- `services/nasemby-core/app/activity_log.py`
- `services/nasemby-core/app/activity_api_runtime.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/app/private_rss_api_runtime.py`
- `services/nasemby-core/tests/test_activity_api_runtime.py`

实施内容：

1. 注册 `GET /api/v2/activity/logs` 与 `DELETE /api/v2/activity/logs`。
2. 记录同步、导入、推送、重试和受控写接口拒绝。
3. 所有活动记录附带请求 ID 和稳定错误码。
4. 对密码、Token、Cookie、Authorization、RSS URL 和 Passkey 做统一脱敏。
5. 保留 JSONL 存储，不开启旧兼容总接口。

验收：任务中心能看到真实失败原因，并且测试证明敏感字段不会进入日志。

## 任务 5：接入运行配置与定时同步

涉及文件：

- `services/nasemby-core/app/config.py`
- `services/nasemby-core/app/runtime_settings.py`
- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_runtime_settings.py`

实施内容：

1. 增加 `MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED`，默认关闭。
2. 提供中文名称和用途说明，并允许网页热更新。
3. 后台循环每 10 分钟检查开关；关闭时不访问 Torra。
4. 应用启动后复用同一同步服务，不创建第二套客户端或仓库。

验收：开关可以在网页修改，关闭状态不会产生外部请求，开启后按间隔同步。

## 任务 6：实现订阅页同步界面

涉及文件：

- `src/types/subscriptions.ts`
- `src/services/api.ts`
- `src/components/pages/DiscoverPage.tsx`
- `src/styles/discover.css`
- `src/styles/workbench.css`

实施内容：

1. 在订阅页增加 Torra 同步状态区和预览入口。
2. 展示新增、已关联、重复、无法映射和冲突数量。
3. 确认导入时生成幂等键，并复用现有确认弹窗。
4. 镜像条目显示“来自 Torra”和只读状态。
5. 第一阶段不显示删除或反向编辑入口。
6. 深色、浅色和 1920x1080 宽屏布局保持一致。

验收：用户可以在订阅页完成预览和确认导入，并清楚区分本地订阅与 Torra 镜像。

## 任务 7：切换任务中心日志接口

涉及文件：

- `src/services/api.ts`
- `src/types/operations.ts`
- `src/components/pages/TasksCenter.tsx`
- `src/styles/tasks.css`

实施内容：

1. 将活动读取切换到 `/api/v2/activity/logs`。
2. 增加“Torra 同步”筛选。
3. 显示错误码、请求 ID 和可重试提示。
4. 旧 `/api/activity/logs` 保留兼容但不再由 React 调用。

验收：同步、推送和 403/503 拒绝均可在任务中心追查。

## 任务 8：契约、文档与完整验证

涉及文件：

- `docs/contracts/http-api-contract-v2.json`
- `docs/API_CONTRACT.md`
- `README.md`
- `services/nasemby-core/README.md`

实施内容：

1. 更新 v2 机器契约和接口说明。
2. 说明第一阶段只读镜像和第二阶段删除边界。
3. 运行新增后端测试，再运行完整 Python 测试。
4. 运行 TypeScript 检查、生产构建、Compose 解析和 Markdown 检查。
5. 执行敏感信息和依赖安全扫描。

验收：全部验证通过，提交并推送 `main`，由 GitHub Actions 更新 `latest` 镜像。
