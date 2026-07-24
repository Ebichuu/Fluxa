# 持久任务台账链迁移与别名升级设计

## 背景

任务快照已经能够把没有订阅锚点的 qB 与 Symedia 证据合并为同一 TMDB 目标，但持久台账仍保留旧孤立链的 artifact 所有权。

`resource_artifacts.artifact_key` 是唯一主键。新链写入相同 artifact 时，当前仓库层会拒绝跨链改绑、标记 `ARTIFACT_CHAIN_CONFLICT`，因此实时页面正确但历史台账持续报告冲突。现有 `record_identity_alias()` 只能在同一链中补充新的 artifact 身份，不能迁移 artifact 的链所有权，也不能合并旧链事件。

本阶段在持久台账写入前增加只读迁移预检。只有满足全部安全条件的计划才会在同一 SQLite 事务中执行；无法证明安全的项目继续保持冲突，不猜测、不改绑。

## 方案选择

采用“A＋只读预检”：

- A 是自动主流程：快照计算计划、验证、条件迁移、写入新快照；
- B 保留为控制室高级诊断入口，展示自动迁移拒绝项，后续只对少量边缘案例提供人工预览与确认；
- C 的无条件覆盖 artifact owner 不采用。

## 目标

1. 把旧 qB 标题型孤立链中已经被当前快照可靠识别的 artifact 迁往标准 TMDB 链。
2. 完整保留可归属的历史事件，并为整链迁移建立永久旧链别名。
3. 所有自动迁移先生成只读计划和拒绝原因，再在同一事务中条件执行。
4. 并发状态变化、证据不足、多目标分裂或非旧孤立链一律拒绝迁移。
5. 第一次执行完成迁移，第二次执行迁移数必须为 0。

## 不在本阶段处理

- 不重新运行 RSS 身份回填；
- 不修改 Torra、qB、115、Symedia 或 Emby；
- 不对两个均有明确但不同 TMDB 的链执行合并；
- 不允许普通标题相似、路径包含或人工猜测触发自动迁移；
- 不删除无法证明为空且已完整迁移的旧链；
- 高级诊断的人工强制执行不属于自动主流程，必须另行预览和确认。

## 数据结构

新增 `resource_chain_aliases`：

- `alias_chain_id TEXT PRIMARY KEY`：已退休的旧 chainId；
- `canonical_chain_id TEXT NOT NULL`：当前标准 chainId；
- `reason_code TEXT NOT NULL`：固定为结构化迁移原因；
- `created_at TEXT NOT NULL`；
- `updated_at TEXT NOT NULL`；
- `payload_json TEXT NOT NULL DEFAULT '{}'`：只保存脱敏计数和迁移模式。

`canonical_chain_id` 引用现有 `resource_chains`。旧链删除后由别名表保留稳定解析关系。已有别名如果指向再次升级的链，迁移时统一更新到最终 canonical chain，禁止形成循环。

不修改现有 artifact 和事件主键。`resource_artifacts.chain_id`、`resource_events.chain_id` 与事件 `idempotency_key` 在事务内按计划更新。

## 只读迁移预检

仓库新增纯读取的迁移计划方法。输入为完整 v2 快照，输出每个冲突 artifact 的预期旧 owner、新 owner、迁移模式、证据方法、是否允许和拒绝原因。

预检不写数据库，也不改变当前链健康状态。自动快照和控制室诊断共用同一计划器，避免两套规则漂移。

### 新链基础条件

新链必须同时满足：

1. `identityState=linked`；
2. `targetKey` 使用明确 TMDB 身份；
3. 媒体类型与目标季号有效；
4. 当前快照确实包含该 artifact；
5. `evidenceOwnership` 中该 artifact 的 `ownerTargetKey` 等于新链 `targetKey`；
6. 该 evidence 没有冲突候选。

### 自动授权的匹配方法

以下证据可以独立授权：

- `artifact_exact` 且 `confidence=strong`；
- `tmdb_exact` 且 `confidence=strong`；
- `symedia_tmdb_anchor` 且 `confidence=strong`。

`symedia_title_season_unique` 不能单独授权迁移。它还必须满足：

1. 同一次快照、同一个 `targetKey` 存在 `symedia_tmdb_anchor`；
2. anchor 的来源为 Symedia；
3. 新链媒体类型为电视剧；
4. qB 证据季号、新链季号和 anchor 目标季号一致；
5. 当前目标只有一个 TMDB 候选。

普通 `title_season_unique`、标题加年份、路径相似和未结构化文本不允许自动迁移。

### 旧链条件

旧 owner 必须是未建立明确 TMDB 的历史孤立链：

- `tmdb_id` 为空；
- `target_key` 为标题型或 unknown 型身份；
- 不是其他明确 canonical chain；
- 没有已有别名指向不同目标；
- 当前 artifact owner 与计划中的 `expectedOldChainId` 一致。

任一条件不满足即拒绝迁移。

## 整链迁移与单 artifact 迁移

### 整链迁移

只有旧链的全部 artifact 都在同一次快照中获得明确新 owner，且全部指向同一个 canonical chain，才执行整链迁移：

1. 条件更新全部 artifact owner；
2. 迁移该旧链的全部历史事件，包括 `artifact_key=''` 的链级事件；
3. 为每条事件重新生成 canonical 幂等键；
4. 合并新链中已经存在的重复事件；
5. 建立永久 chain alias；
6. 更新已有下游别名指向 canonical chain；
7. 删除已经没有 artifact 和事件的旧链。

返回 `chainAliases += 1`、`deletedEmptyChains += 1`。

### 单 artifact 迁移

如果旧链仍有未明确归属的 artifact，或不同 artifact 指向多个新目标，只迁移当前安全 artifact：

1. 条件更新该 artifact owner；
2. 只迁移 `artifact_key` 等于该 artifact 的事件；
3. 重新生成并合并事件幂等键；
4. `artifact_key=''` 的链级事件继续留在旧链；
5. 不删除旧链；
6. 不建立整链别名。

这保证部分迁移不会把无法证明归属的历史事件带到新目标。

## 并发保护

每个 artifact 必须使用条件更新：

```sql
UPDATE resource_artifacts
SET chain_id = :new_chain, last_seen_at = :now
WHERE artifact_key = :artifact_key
  AND chain_id = :expected_old_chain
```

影响行数必须严格等于 1。任何 artifact 的影响行数不是 1，抛出并发冲突并回滚整批迁移和本次快照写入。

事务开始后还要重新验证：

- 新旧链仍存在；
- 别名没有被并发修改；
- 旧链 artifact 集合仍与迁移模式一致；
- 整链迁移仍全部指向同一 canonical chain。

不允许在条件失败后降级为普通覆盖。

## 事件幂等键迁移

现有事件幂等键包含 chainId。事件改到 canonical chain 时必须重新计算：

1. 使用 canonical chainId、artifactKey、阶段、状态、健康、证据、来源和原因生成新键；
2. 如果 canonical chain 已存在相同新键，保留已有 canonical 事件，删除待迁移的重复历史事件；
3. 如果不存在，更新历史事件的 `chain_id` 和 `idempotency_key`；
4. 为迁移动作本身写入一条结构化、确定性幂等事件；
5. 第二次快照不得再次生成相同阶段事件或迁移事件。

事件正文继续经过现有脱敏函数，不保存路径查询参数、Token、Cookie 或 Passkey。

## 快照事务顺序

1. 在事务外读取当前 artifact owner 和链状态，生成只读迁移预检；
2. 如果存在可执行迁移，确认一次性 SQLite 备份已经成功；
3. 开启 `BEGIN IMMEDIATE`；
4. 先 upsert 本次快照中的 canonical chain；
5. 按预检计划重新验证并执行条件迁移；
6. 重算和合并历史事件幂等键；
7. 建立整链别名并删除确认为空的旧链；
8. 按当前逻辑写入 artifact 和阶段事件；
9. 对仍未迁移的项目记录真实 artifact conflict；
10. 提交事务。

任何步骤失败全部回滚。

## 一次性备份

第一次存在可执行迁移时，必须在事务开始前使用 SQLite backup API 创建一致性备份。备份文件使用固定版本标识，已存在时不覆盖。

备份失败时：

- 不执行任何迁移；
- `record_snapshot()` 返回 `persisted=false`，本次快照不写入 chain、artifact 或事件；实时内存快照仍可返回给调用方；
- `migrationSkipped` 增加；
- 拒绝原因记录 `BACKUP_FAILED`；
- 控制室提示管理员处理备份问题。

普通接口不返回备份绝对路径，只返回是否创建成功和脱敏文件名。

## 返回统计

`record_snapshot()` 与任务摘要中的 ledger 结果增加可选字段：

- `artifactMigrations`：成功迁移 artifact 数；
- `chainAliases`：新增整链别名数；
- `artifactConflicts`：本次仍然真实存在的冲突数；
- `migrationSkipped`：预检或执行拒绝数量；
- `migrationSkipReasons`：结构化原因码到数量的映射；
- `deletedEmptyChains`：确认迁移完成后删除的空旧链数；
- `migrationBackupCreated`：本次是否创建备份。

旧字段继续返回，旧客户端忽略新增字段即可。

建议原因码至少包含：

- `NEW_CHAIN_NOT_LINKED`；
- `NEW_TARGET_WITHOUT_TMDB`；
- `OWNERSHIP_RECORD_MISSING`；
- `MATCH_METHOD_NOT_ALLOWED`；
- `SYMEDIA_ANCHOR_MISSING`；
- `TARGET_SCOPE_MISMATCH`；
- `OLD_CHAIN_NOT_LEGACY`；
- `OLD_CHAIN_SPLIT`；
- `OWNER_CHANGED_CONCURRENTLY`；
- `BACKUP_FAILED`。

## 高级诊断入口

控制室可以增加“任务台账迁移”折叠诊断：

- 默认只展示自动预检摘要、拒绝原因和脱敏链引用；
- 自动迁移成功项只显示计数和最近执行时间；
- 自动拒绝项可以进入人工预览，但不能从列表直接改绑；
- 后续人工执行必须使用“预览 → 确认 → 条件更新 → 复查 → 留痕”，并要求 expected old/new chain、artifact、幂等键和明确确认；
- 人工操作也不能绕过条件更新、事件幂等重算或备份要求。

高级诊断不作为第一阶段自动迁移成功的依赖。

## 验收标准

1. `symedia_title_season_unique` 没有同快照 `symedia_tmdb_anchor` 时拒绝迁移；
2. anchor 存在但媒体类型或季号不一致时拒绝迁移；
3. 条件更新影响 0 行或多于 1 行时整批回滚；
4. 旧链全部 artifact 指向同一新链时迁移全部事件、建立别名并删除空链；
5. 单 artifact 迁移只移动对应事件，空 artifactKey 事件保留在旧链；
6. 事件迁移后幂等键使用 canonical chainId，重复事件被合并；
7. 第一次快照返回非零迁移数，第二次相同快照 `artifactMigrations=0`；
8. 真实强身份冲突继续保留，不能被自动迁移；
9. 旧 chainId 可以解析到 canonical chain，任务深链接不失效；
10. SQLite 备份失败时没有任何迁移写入；
11. 返回全部迁移、跳过、冲突、别名和删除统计；
12. 所有日志和事件继续脱敏；
13. 上线前保存 SQLite 备份，首次运行后冲突降为真实剩余数量；
14. 不执行任何 Torra、qB、115、Symedia、Emby 或 RSS 外部写操作。

## 发布与回滚

该迁移必须独立发布。发布前保留数据库备份；发布后连续读取两次任务快照：

1. 第一次核对迁移数、别名数、跳过原因和剩余冲突；
2. 第二次确认迁移数为 0；
3. 使用旧 chainId 查询确认别名解析；
4. 如果迁移统计异常，停止新版本并恢复发布前 SQLite 备份与上一验证镜像。
