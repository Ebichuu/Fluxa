# 持久任务台账链迁移实施计划

依据：`docs/superpowers/specs/2026-07-25-resource-ledger-chain-migration-design.md`

## 目标

为任务快照增加只读迁移预检，把满足安全条件的旧标题型孤立链 artifact 和历史事件迁移到标准 TMDB 链；整链迁移建立永久别名，部分迁移保留无法归属的链级历史。

## 批次 1：迁移预检回归

涉及文件：

- `services/nasemby-core/tests/test_resource_task_repository.py`
- `services/nasemby-core/app/evidence_ownership_runtime.py`

实施：

1. evidence ownership 补充媒体类型和季号，供仓库层复核。
2. 增加合法 Symedia anchor、缺 anchor、季号冲突、普通标题方法和强 TMDB 冲突样本。
3. 预检返回计划、迁移模式和结构化拒绝原因，不写数据库。

验证：`symedia_title_season_unique` 只有同快照存在一致 anchor 时可进入计划。

## 批次 2：整链与单 artifact 事务迁移

涉及文件：

- `services/nasemby-core/app/resource_task_repository.py`
- `services/nasemby-core/tests/test_resource_task_repository.py`

实施：

1. 新增 chain alias 表和解析方法。
2. 快照先 upsert canonical chain，再按预检结果条件更新 artifact owner。
3. 整链迁移全部历史事件、建立别名并删除空旧链。
4. 单 artifact 只迁移对应事件，保留空 artifactKey 链级事件和旧链。
5. 条件更新行数异常时回滚整个快照事务。

验证：旧 owner 变化、旧链分裂和真实 TMDB 冲突均不会被覆盖。

## 批次 3：事件幂等、备份与统计

涉及文件：

- `services/nasemby-core/app/resource_task_repository.py`
- `services/nasemby-core/tests/test_resource_task_repository.py`

实施：

1. 迁移事件使用 canonical chainId 重算幂等键。
2. canonical 已存在相同事件时删除迁移重复项。
3. 首次可执行迁移前使用 SQLite backup API 创建固定版本备份。
4. 备份失败时返回 `persisted=false`，不写任何快照内容。
5. 返回迁移、别名、冲突、跳过原因和删除空链统计。

验证：第一次迁移非零，第二次相同快照迁移数为零且事件不重复。

## 批次 4：服务接入与高级只读诊断

涉及文件：

- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`
- `docs/contracts/http-api-contract-v2.json`
- `docs/API_CONTRACT.md`

实施：

1. 旧 chainId 在详情和列表筛选中解析到 canonical chain。
2. 增加只读迁移预检接口，返回脱敏计划和拒绝摘要。
3. 不增加人工强制改绑接口；后续根据真实拒绝原因单独设计。

验证：旧深链接有效，预检读取不改变 artifact、事件或 chain。

## 批次 5：全量验收与发布准备

1. 更新 `docs/Fluxa-前端UI改造实施计划.md`。
2. 运行后端全量 unittest、前端 typecheck/build 和 `git diff --check`。
3. 使用临时 SQLite 验证备份、首次迁移、第二次幂等和回滚。
4. 只读检查本地真实台账迁移预检，不执行真实迁移。
5. 保留 `.codex-app-*` 日志和 `frontend-reference.html` 未跟踪文件，不纳入提交。
