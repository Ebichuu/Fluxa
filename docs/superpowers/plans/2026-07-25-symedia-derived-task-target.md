# Symedia 派生任务目标实施计划

依据：`docs/superpowers/specs/2026-07-24-symedia-derived-task-target-design.md`

## 目标

在没有 Fluxa/Torra 订阅时，以 Symedia 的明确电视剧 TMDB、季号和标题建立媒体目标，并将唯一匹配的 qB 下载证据合并到同一任务链。保持 Emby 作品级和秒传批次级证据边界。

## 批次 1：失败回归与所有权契约

涉及文件：

- `services/nasemby-core/tests/test_evidence_ownership_runtime.py`
- `services/nasemby-core/tests/test_task_chain_runtime.py`

实施：

1. 增加无订阅、Symedia 明确 TMDB/季号、qB 方括号中文标题的失败样本。
2. 增加同标题同季不同 TMDB 的冲突样本。
3. 增加缺 TMDB、缺季号、缺标题不派生目标的样本。
4. 增加已有订阅目标时不重复创建派生链的样本。

验证：新用例在修改运行代码前失败，失败原因必须是 qB/Symedia 仍被拆成孤立链。

## 批次 2：Symedia 派生目标与统一裁决

涉及文件：

- `services/nasemby-core/app/evidence_ownership_runtime.py`
- `services/nasemby-core/tests/test_evidence_ownership_runtime.py`

实施：

1. 预扫描 Symedia，建立满足 TMDB、电视剧类型、季号和标题条件的派生目标。
2. 与已有相同 `targetKey` 合并标题键，不创建重复目标。
3. Symedia 自身记录使用 `symedia_tmdb_anchor` 强绑定。
4. qB 使用保守中文标题、类型、同季和唯一候选裁决。
5. 纯派生目标的 qB 匹配记录为 `symedia_title_season_unique`。
6. 多候选保持冲突；所有证据最多一个所有者。

验证：所有权回归通过，输入顺序变化不改变结果。

## 批次 3：无订阅统一任务链

涉及文件：

- `services/nasemby-core/app/task_chain_runtime.py`
- `services/nasemby-core/tests/test_task_chain_runtime.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`

实施：

1. 为纯 Symedia 派生目标建立统一任务项。
2. 合并 qB Hash、Symedia ID、阶段证据和集级证据。
3. 任务来源明确显示未发现追更订阅，不把无订阅归为身份未识别。
4. 用派生 TMDB 读取 Emby 作品级证据，保持 `embyEvidenceScope=title`。
5. 没有逐文件秒传证据时继续显示上传方式未确认。

验证：同一目标顶层只返回一条链，v2 `chainId/targetKey` 稳定，摘要与详情兼容。

## 批次 4：文档与全量验收

涉及文件：

- `docs/Fluxa-前端UI改造实施计划.md`
- `docs/API_CONTRACT.md`

验证：

1. 后端全量 unittest 通过。
2. 前端 `npm run typecheck` 与 `npm run build` 通过。
3. `git diff --check` 通过。
4. 只读实机接口确认目标链数量下降，不执行 RSS 回填或任何外部写操作。
5. 保留 `.codex-app-*` 日志和 `frontend-reference.html` 未跟踪文件，不纳入提交。
