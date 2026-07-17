# SQLite 单台账与私人 PT RSS 种子库实施计划

状态：第一版已落地并通过 Docker 候选验收；收集器硬化、真实脱敏夹具和原子迁移演练待完成

日期：2026-07-18

## 当前实施进度

- [x] 阶段 0：基线记录，85 项原回归、TypeScript 和构建通过。
- [x] 阶段 1～2 第一版：SQLite runtime、订阅仓储、JSON 迁移备份/报告和 NasEmby 读写切换。
- [x] 阶段 3 第一版：RSS 来源/条目/FTS5/抓取记录仓储与标准 RSS/Atom 解析。
- [~] 阶段 4：闸门、ETag、重定向、SSRF 和基本轮询已完成；429、指数退避、并发 2 和真实站点夹具待补。
- [x] 阶段 5 第一版：10 条 RSS/动作 v2 契约已注册；匹配列表暂返回稳定空集合。
- [x] 阶段 6 第一版：顶部“种子库”入口、搜索、时间/来源筛选和来源管理页面已完成。
- [x] 阶段 7：Docker 候选镜像、重启持久化和第一版停点复核。

当前自动回归为 98 项。真实 RSS、Torra、qB 和 MoviePilot 写调用仍为零。

依据：

- `docs/superpowers/specs/2026-07-18-sqlite-torra-quality-upgrade-design.md`
- `docs/superpowers/specs/2026-07-18-private-pt-rss-seed-library-design.md`

## 1. 目标与边界

本计划只完成两项基础能力：

1. 使用 `db/media_control_center.sqlite3` 替换两个 JSON 生产订阅台账文件。
2. 建立不修改 Torra 的私人 PT RSS 种子库，包括来源管理、轮询、最近 3/7/14 天索引、FTS5 搜索和只读页面。

本计划不实施：

- 真实私人 RSS 连接。
- Torra 追更洗版分析或下载动作。
- MoviePilot 推送。
- fnOS 真实订阅迁移。
- 影院大厅修改。
- RSS 种子直接推送到 qB。

所有测试使用临时目录、脱敏 RSS/Atom 夹具和假的 HTTP 会话。

## 2. 完成标准

- 订阅配置和条目只写 SQLite，不双写 JSON。
- JSON 迁移有备份、差异报告、失败停止和回滚说明。
- 现有订阅列表、详情、日历、保存、删除、分类和改季契约不变。
- 5～10 个 RSS 来源可以配置，但生产收集器默认关闭。
- SQLite 能保存、去重、搜索和自动清理 RSS 条目。
- 任何 API、日志、错误和活动记录都不返回完整 RSS/下载 URL。
- 现有 Python、TypeScript、Vite 和 Docker 验收继续通过。

## 3. 阶段 0：记录基线

### 任务 1：执行修改前全量基线

执行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
git status --short
```

记录：

- Python 测试数量与结果。
- 当前 JSON 台账测试夹具和契约快照。
- 当前 `docs/contracts/http-api-contract-v2.json` 数量。
- 当前 Git 提交 ID。

验收：

- 基线全部通过后才修改代码。
- 若现有失败与本轮无关，先记录并停止，不带病迁移。

## 4. 阶段 1：SQLite 基础设施

### 任务 2：建立连接工厂和 schema 管理器

新增：

- `services/nasemby-core/app/sqlite_runtime.py`
- `services/nasemby-core/tests/test_sqlite_runtime.py`

实现：

1. 从 `MCC_DATA_ROOT`/项目根解析 `db/media_control_center.sqlite3`，测试允许注入临时路径。
2. 每次操作使用独立短连接，设置：
   - `PRAGMA journal_mode=WAL`
   - `PRAGMA foreign_keys=ON`
   - `PRAGMA synchronous=NORMAL`
   - `PRAGMA busy_timeout=5000`
3. 使用显式事务上下文，不让网络调用进入数据库事务。
4. 建立 `schema_meta` 和单调递增的 schema version。
5. 启动时检查 SQLite FTS5；缺失时健康状态明确失败，不静默退回慢速模糊查询。
6. 提供 `BEGIN IMMEDIATE` 的短领取事务辅助函数。
7. 错误日志只包含数据库操作标签，不输出 SQL 参数中的 RSS 地址。

测试：

- 空数据库初始化。
- 重复初始化幂等。
- WAL、外键、busy timeout 和 schema version。
- 两线程竞争写入能等待或返回明确冲突。
- 事务异常自动回滚。
- FTS5 不可用时失败信息明确。

### 任务 3：建立订阅仓储接口

新增：

- `services/nasemby-core/app/subscription_repository.py`
- `services/nasemby-core/tests/test_subscription_repository.py`

实现：

1. 创建：
   - `subscription_config`
   - `subscriptions`
   - `migration_runs`
2. `subscription_config.payload_json` 保存完整 NasEmby 配置。
3. `subscriptions.payload_json` 保存完整原条目；身份字段单独建列和索引。
4. 提供与当前代码需要对应的仓储方法：
   - 读取/保存配置。
   - 列表、单条读取、upsert、删除和清空。
   - 原子批量保存。
   - 带 `version` 的乐观更新。
5. JSON 规范化使用稳定键顺序和 UTF-8，不丢未知字段。
6. 仓储不包含发现、分类、日历或 MoviePilot 业务规则。

测试：

- 完整 JSON 往返不丢字段。
- TMDB、媒体类型、季号和 subscription key 索引。
- 并发版本冲突返回明确异常。
- 批量保存中单条失败时全部回滚。
- 删除与清空不影响 schema 和迁移记录。

## 5. 阶段 2：JSON 一次性迁移

### 任务 4：实现迁移预检、备份和差异报告

新增：

- `services/nasemby-core/app/subscription_migration.py`
- `services/nasemby-core/tests/test_subscription_migration.py`

修改：

- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_source_contract.py`

实现：

1. 输入固定为：
   - `db/discover_subscriptions.json`
   - `db/discover_subscription_items.json`
2. 迁移前复制到 `db/migrations/<timestamp>/`。
3. 校验顶层结构、配置类型、条目 key、TMDB ID、媒体类型和季号。
4. 在同目录临时 SQLite 文件中导入，不直接覆盖正式数据库。
5. 生成：
   - `migration-report.json`
   - `migration-report.zh-CN.txt`
6. 对比配置规范化结果、条目数量、每个 key 和完整 payload。
7. 无阻塞差异后原子替换为正式数据库。
8. 已完成的相同源指纹迁移不得重复执行。
9. 迁移失败时健康状态报告失败，不能创建空台账继续启动。
10. 不在迁移器中删除原 JSON；成功后只作为备份和回滚输入。

测试夹具：

- 正常配置和多条订阅。
- 重复 key。
- 非法 JSON。
- 缺失 TMDB、类型或季号的可保留与阻塞边界。
- 导入中断。
- 已存在正式数据库。
- 报告内容不包含凭据。

验收：

- 正常迁移逐字段一致。
- 阻塞差异不切换。
- 回滚只需恢复 JSON 并启动迁移前镜像。

### 任务 5：将 NasEmby 订阅读写切换到仓储层

修改：

- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/app/subscription_compat_runtime.py`
- `services/nasemby-core/tests/test_mcc_compat_runtime.py`
- `services/nasemby-core/tests/test_source_contract.py`

实现：

1. 只替换 `discover_runtime.py` 中配置和条目持久化边界：
   - 当前约 496～540 行的配置读取/保存。
   - 当前约 1443～1698 行的条目读取、进度回写、保存、删除和批量更新。
   - 当前约 3301 行的配置直接写入。
2. 现有业务函数签名和返回结构保持不变。
3. 分类、季处理、日历、来源调度和 MoviePilot 逻辑继续使用原函数。
4. 运行时禁止再打开两个 JSON 进行写入。
5. 订阅详情缓存文件暂时保持原状；它不是唯一台账，不在本任务扩大迁移范围。
6. 列表中的进度更新改为显式仓储事务，读取不再隐式覆盖整个文件。
7. 删除 `_ledger_lock` 对文件覆盖写的所有权；必要的短业务锁只包围仓储事务。

测试：

- 现有兼容路由全部回归。
- 保存、删除、清空、分类、改季和进度更新。
- 同时保存两条订阅不丢失更新。
- 运行后 JSON 文件修改时间不变化。
- 未迁移或迁移失败时写入被阻止。

建议提交：

```text
feat: migrate subscription ledger to sqlite
```

## 6. 阶段 3：私人 RSS 数据层

### 任务 6：建立 RSS 来源、条目、抓取和匹配仓储

新增：

- `services/nasemby-core/app/private_rss_repository.py`
- `services/nasemby-core/tests/test_private_rss_repository.py`

实现 schema：

- `rss_sources`
- `rss_items`
- `rss_item_search` FTS5
- `rss_fetch_runs`
- `rss_subscription_matches`

实现：

1. RSS、详情和下载 URL 按用户选择明文保存。
2. 所有公共 DTO 永不包含完整 URL。
3. 来源指纹用于阻止重复添加相同 RSS。
4. 条目指纹优先 GUID，缺失时使用规范化组合哈希。
5. 写入条目与 FTS 更新在同一事务中完成。
6. 同条目重复出现只更新 `last_seen_at` 和允许更新的元数据。
7. 删除来源级联删除本地条目、FTS 和未执行匹配。
8. 提供分页搜索、站点筛选、时间范围和结构化条件。
9. 抓取记录按 30 天/每站 1000 条限制清理。
10. 条目按 3/7/14 天和 200000 条软上限清理。

测试：

- 明文能在数据库内部读取，但公共结果始终脱敏。
- GUID 与 fallback 指纹去重。
- FTS 中文、英文、制作组和 HDR 搜索。
- 分页稳定排序。
- 来源删除级联边界。
- 到期清理分批执行，不触发全库 VACUUM。

### 任务 7：增加 RSS/Atom 解析器和真实脱敏夹具

修改：

- `services/nasemby-core/requirements.txt`

新增：

- `services/nasemby-core/app/private_rss_parser.py`
- `services/nasemby-core/tests/test_private_rss_parser.py`
- `services/nasemby-core/tests/fixtures/private_rss/`

实现：

1. 增加固定版本的 RSS/Atom 解析依赖；优先选用成熟库，不手写完整 XML 方言。
2. 使用用户实际 5～10 个站点的脱敏样本建立夹具；Passkey、域名账号和下载参数替换为测试值。
3. 支持 RSS 2.0、Atom、CDATA、命名空间和中文编码。
4. 标准化 GUID、标题、发布时间、分类、大小、详情和下载链接。
5. 解析季号、单集、连续集范围和轻量版本摘要。
6. HTML 描述只提取纯文本，不直接返回页面。
7. XML 输入前执行 2 MiB 大小限制。
8. 不下载 `.torrent` 文件验证内容。

测试：

- 每个真实站点一份正常夹具和至少一份边界夹具。
- 缺失 GUID、发布时间、大小和 enclosure。
- 恶意 XML、异常编码和大响应。
- URL 与错误消息不泄露测试 Passkey。

阻塞条件：

- 没有真实脱敏样本时，只完成标准 RSS/Atom 解析，不猜测站点私有字段。

## 7. 阶段 4：RSS 收集器

### 任务 8：实现安全 HTTP 获取与轮询协调器

新增：

- `services/nasemby-core/app/private_rss_collector.py`
- `services/nasemby-core/tests/test_private_rss_collector.py`

修改：

- `services/nasemby-core/app/main.py`
- `services/nasemby-core/app/http_runtime.py`（仅复用或扩展脱敏辅助函数）

实现：

1. `MCC_PRIVATE_RSS_ENABLED=false` 时不启动线程、不读取 `feed_url`、不发出任何来源网络请求。
2. 单 worker 内只启动一个收集线程。
3. 每来源周期只允许 1、3、5 分钟，默认 5 分钟。
4. 全局并发最多 2，同来源绝不并发。
5. 发送 ETag/Last-Modified 条件请求并处理 304。
6. 0～15 秒错峰；429 使用 Retry-After；连续失败指数退避，最大 60 分钟。
7. 连接超时 5 秒、总超时 20 秒、最多 3 次重定向、响应体 2 MiB。
8. 每次重定向重新执行 SSRF 检查。
9. 默认 HTTPS；HTTP 和非标准端口必须来源配置明确允许。
10. 记录脱敏 fetch run，不把 `requests` 原始异常直接返回。
11. 成功解析后单事务 upsert 条目，最后更新来源状态。
12. 每小时运行一次分批清理。

测试：

- 闸门关闭零网络调用。
- 200、304、301/302、429、401/403、超时和断流。
- SSRF：环回、链路本地、内网、重定向变更地址。
- 并发上限和同来源互斥。
- 服务重启根据 `next_poll_at/backoff_until` 恢复。
- 一个来源失败不影响其他来源。

### 任务 9：把 RSS 状态加入健康和活动日志

修改：

- `services/nasemby-core/app/main.py`
- `services/nasemby-core/app/activity_log.py`
- `services/nasemby-core/tests/test_source_contract.py`

实现：

1. `/api/health` 只返回：
   - 功能是否启用。
   - 来源总数、启用数、异常数。
   - 最近成功时间和待处理清理数量。
2. 不返回来源 URL、路径查询参数或条目下载 URL。
3. 来源保存、暂停、测试、抓取失败和清理写脱敏活动。
4. 自动测试日志继续与真实活动隔离。

## 8. 阶段 5：RSS v2 API

### 任务 10：注册来源、种子和匹配 API

新增：

- `services/nasemby-core/app/private_rss_api_runtime.py`
- `services/nasemby-core/tests/test_private_rss_api_runtime.py`

修改：

- `services/nasemby-core/app/main.py`
- `services/nasemby-core/tests/test_source_contract.py`
- `docs/contracts/http-api-contract-v2.json`

实现接口：

- `GET /api/v2/rss-sources`
- `POST /api/v2/rss-sources`
- `GET /api/v2/rss-sources/:id`
- `PATCH /api/v2/rss-sources/:id`
- `DELETE /api/v2/rss-sources/:id`
- `POST /api/v2/rss-sources/:id/tests`
- `GET /api/v2/rss-items`
- `GET /api/v2/rss-items/:id`
- `GET /api/v2/rss-matches`

规则：

1. 所有接口要求整站认证。
2. POST/PATCH/DELETE 要求 Origin 校验。
3. `MCC_PRIVATE_RSS_ENABLED` 只控制后台收集和“测试 RSS”产生的真实网络访问；来源新增、编辑、暂停和删除只写本地 SQLite，可在收集器关闭时完成，但仍要求整站认证、Origin 校验和统一配置写保护。
4. 创建返回 201 + Location，删除返回 204。
5. 测试创建异步动作并返回 202；动作状态复用统一动作资源。
6. 列表统一 `limit/offset`，默认 50、最大 100。
7. 重复来源 409，验证错误 422，限频 429，上游失败 502，闸门关闭 503。
8. 错误不能以 200 包装。
9. `feedUrl` 只允许在写请求中出现，永不进入响应。
10. 新增 v2 接口统一使用 `{ "code", "error", "request_id" }` 错误包络，不复制 v1 的历史差异。

测试：

- 未认证、Origin、方法和状态码。
- 收集器关闭时来源 CRUD 仍可只写本地配置，且测试与后台轮询保持零网络调用。
- 创建、保持旧 URL 的 PATCH、更换 URL、删除级联。
- 测试动作不写种子库。
- 搜索分页和脱敏详情。
- 完整 URL/Passkey 不出现在响应、日志和异常。

建议提交：

```text
feat: add private rss seed index api
```

## 9. 阶段 6：React 种子库页面

### 任务 11：增加前端类型与 API 客户端

新增：

- `src/types/rssSeedLibrary.ts`

修改：

- `src/services/api.ts`

实现：

- 来源摘要、分页种子、搜索参数、抓取状态和测试动作类型。
- 来源 CRUD、测试、种子列表/详情和匹配列表客户端。
- 通用写请求辅助函数正确解析 201/202/204 和错误状态。
- 前端类型中不定义可读取的完整 RSS 或下载 URL 字段。

### 任务 12：增加“种子库”导航和页面

新增：

- `src/components/pages/RssSeedLibraryPage.tsx`
- `src/styles/rss-seed-library.css`

修改：

- `src/components/layout/AppTopNav.tsx`
- `src/app/App.tsx`
- `src/styles/index.css`

实现页面：

1. 更新流：最近 1 小时、24 小时和 7 天。
2. 本地搜索：关键词、站点、时间、媒体类型、季集和版本摘要。
3. 来源管理：新增、编辑、暂停、删除、周期、保留期和测试。
4. 新增/编辑表单只在写入时持有 RSS URL，保存成功后立即清空前端状态。
5. 来源列表只显示名称、域名、周期、状态和最近成功。
6. 删除来源二次确认并明确只删除本地索引。
7. 分页默认 50 条。
8. 页面文案使用业务语言；内部指纹和错误码放在次级信息。
9. 不修改影院大厅、媒体队列或 Mineradio 视觉。

测试与验收：

- `npm run typecheck`
- `npm run build`
- 键盘可到达来源操作、筛选和分页。
- 390、1024、1440 宽度无不可达操作。
- 页面 DOM 和网络响应中不存在完整 RSS URL。

## 10. 阶段 7：回归、镜像与文档

### 任务 13：全量自动化和安全回归

执行：

```powershell
python -m unittest discover -s services/nasemby-core/tests -t services/nasemby-core -v
npm run typecheck
npm run build
npm audit --audit-level=high
git diff --check
```

额外检查：

- `rg` 确认两个 JSON 没有运行时写路径。
- `rg` 确认 API DTO 不含 `feed_url`、`download_url`。
- 模拟异常中不含 Passkey。
- FTS5 和 WAL 在 Docker 中可用。

### 任务 14：Docker 只读候选镜像

修改：

- `Dockerfile`（只在新增 Python 依赖需要时）
- `docker-compose.yml`
- `.env.example`
- `docs/DEPLOYMENT.md`
- `docs/API_CONTRACT.md`
- `docs/PLAN.md`
- `docs/ROADMAP.md`

实现：

1. 增加 `MCC_PRIVATE_RSS_ENABLED=false` 示例。
2. 不在镜像中写入真实 RSS。
3. 临时容器使用模拟来源和临时数据库完成：
   - 登录。
   - SQLite 初始化/迁移。
   - RSS API 读取。
   - 收集闸门关闭时零外部 RSS 请求。
   - 本地来源配置写入与真实抓取闸门彼此独立。
   - 重启持久化。
   - 无 Node 运行层。
4. 候选镜像仍不连接真实 PT、Torra 或 qB 写接口。

验收：

- 新候选镜像通过后，才能进入第二份追更洗版实施计划。

2026-07-18 第一版验收结果：

- 98 项 Python 回归、TypeScript、Vite 构建、npm 高危审计和 Compose 配置通过。
- 候选镜像 `media-control-center:sqlite-rss-preview` 构建成功。
- 临时容器完成登录、来源本地写入、RSS API 读取和容器重启持久化。
- 容器内 SQLite 使用 WAL，FTS5 可用；运行层只有 Python / Gunicorn，没有 Node。
- API 响应未出现完整 RSS 地址或测试 Passkey；真实 RSS、Torra、qB 和 MoviePilot 写调用为零。
- 临时容器和验收目录已清理，只保留本地候选镜像。

建议提交：

```text
feat: complete sqlite and private rss foundation
```

## 11. 明确停点

完成本计划后必须停下来复核：

- JSON → SQLite 差异结果。
- RSS 明文存储风险仍被接受。
- 页面不泄露 Passkey。
- 实机 5～10 个 RSS 样本是否已经准备。
- 是否继续执行 Torra 追更洗版实施计划。

本次停点结论：模拟 JSON 迁移、页面/API 脱敏和 Docker 持久化已经通过；真实 5～10 个站点夹具、临时 SQLite 原子替换演练、429/退避和双并发仍是进入 Torra 追更洗版编码前的前置项。

未完成以上复核前，不开启真实 RSS 收集，不触发 Torra 写动作。
