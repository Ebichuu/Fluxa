# 追更工作台可信状态与资源检索修正版实施计划

依据：`docs/superpowers/specs/2026-07-23-workbench-trust-corrections-design.md`

## 实施原则

- 只修改与状态、原因、首页问题定位、RSS 检索/身份验收和订阅海报有关的代码。
- 先增加可复现失败的测试，再进行最小实现。
- 不执行 Torra、qBittorrent、115、Symedia、Emby 或 PT 的真实写操作。
- 不向正式 RSS 数据库插入固定测试样本。
- 所有新增响应字段均为可选字段，保持旧客户端兼容。

## 批次 1：正常保护状态与原因分层

涉及文件：

- `services/nasemby-core/app/task_exception_runtime.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/tests/test_task_exception_runtime.py`
- `services/nasemby-core/tests/test_task_chain_v2_runtime.py`

实施：

1. 增加回归样本：顶层 `state=blocked`，唯一阻塞阶段为低分保护。
2. 调整执行状态裁决顺序：真实失败优先，保护优先于旧顶层 blocked。
3. 保持 `identityState` 独立；已关联保护条目得到 `linked/protected/protected`。
4. 在阶段适配时保留原始 `technicalReasonText`，并让兼容 `reasonText` 固定等于用户原因。
5. 根据 `reasonCode + 阶段 + 来源`生成用户原因；路径正则只作为旧日志兜底。
6. 验证普通原因不包含 Windows/Linux/UNC 路径、Hash、内部 ID 或鉴权 URL。

验证：

- 四类保护文本不会产生 `executionState=action_required`。
- 混合保护和真实失败时，真实失败仍为最高优先级。
- `technicalReasonText` 保留原始路径，`userReasonText` 与 `reasonText` 均为安全中文。

## 批次 2：首页季集定位与原因优先级

涉及文件：

- `services/nasemby-core/app/home_summary_runtime.py`
- `services/nasemby-core/tests/test_home_summary_runtime.py`
- `src/types/homeSummary.ts`
- `src/components/pages/Overview.tsx`

实施：

1. 从问题阶段、标准 `targetKey`、任务顶层依次提取季集。
2. 首页问题增加可选 `displayTitle`、`seasonNumber`、`episodeNumber` 和 `secondaryReasonText`。
3. 真实执行失败优先于身份未关联；身份问题仅作为次要说明。
4. 前端优先显示 `displayTitle`，保留旧 `headline/title` 回退。
5. 精准跳转继续携带现有 `chainId` 和 `targetKey`。

验证：

- 同一作品的 S01E03、S01E05 显示为两个可区分问题项。
- Symedia 未查询到媒体信息作为主因，身份未关联作为次要说明。
- 首页响应不出现路径、Hash 和内部 ID。

## 批次 3：RSS 分层搜索与回填运行记录

涉及文件：

- `services/nasemby-core/app/private_rss_repository.py`
- `services/nasemby-core/app/private_rss_api_runtime.py`
- `services/nasemby-core/app/rss_subscription_match_runtime.py`
- `services/nasemby-core/tests/test_private_rss_repository.py`
- `services/nasemby-core/tests/test_private_rss_api_runtime.py`
- `services/nasemby-core/tests/test_rss_subscription_match_runtime.py`

实施：

1. 将目标搜索拆成明确身份和未识别候选两个查询分支，再按条目 ID 合并。
2. 明确 TMDB 相同优先；不同 TMDB 或身份冲突直接排除。
3. 电视剧标题回退不再要求年份；季号允许相同或未知，但明确不同季号继续排除。
4. 未识别媒体类型只在没有类型冲突且标题可靠时作为人工候选。
5. 电影标题回退继续强制年份。
6. 返回匹配方式、置信度和季号待确认标记；回退候选不进入自动动作。
7. 新增 `rss_identity_backfill_runs` 运行记录，保存扫描、识别、冲突、未变化数量和时间。
8. RSS 摘要返回最近回填状态；旧摘要字段保持不变。

验证：

- 《清醒点，桃子》可命中不含年份、季号相同或未知的可靠候选。
- 不同 TMDB、不同季号和电影年份不一致的条目均被排除。
- 非空但无法分词的查询仍返回 0。
- 旧 `/api/v2/rss-items` 请求和分页行为保持兼容。

## 批次 4：RSS 固定样本端到端验收

涉及文件：

- `services/nasemby-core/tests/test_rss_identity_acceptance.py`（新增）
- 必要时补充 `private_rss_parser.py`、`private_rss_repository.py` 和 `rss_subscription_match_runtime.py` 的最小修复

实施：

1. 在临时 SQLite 中创建三个固定 RSS 样本：TMDB 字段、IMDb 链接、唯一追更匹配。
2. 增加一个多追更候选冲突样本。
3. 通过正式解析器、仓库写入、身份回填和 API 查询执行完整流程。
4. 断言身份来源、置信度、状态和筛选结果。

验证：

- 三条可靠样本均变为已识别。
- 冲突样本变为 `conflict`，不随机绑定。
- 测试只使用临时数据库，不写入仓库或正式运行数据。

## 批次 5：已有订阅海报补齐

涉及文件：

- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `services/nasemby-core/tests/test_subscription_workbench_runtime.py`
- `src/components/layout/PosterImage.tsx`
- 必要时补充追更页面测试或类型定义

实施：

1. 新增按明确 TMDB ID 解析订阅视觉元数据的窄函数。
2. 先查本地发现/TMDB 缓存，仅在已配置且缓存缺失时按 ID 请求。
3. 本地订阅只在 `poster_url` 为空且成功获得海报时执行一次 SQLite 合并写入。
4. 仅 Torra 的只读条目只补响应，不创建本地订阅。
5. 无可靠身份或加载失败继续使用首字占位。
6. 保持外部图片允许列表代理和单次失败回退。

验证：

- 已有 TMDB ID 的空海报订阅返回真实 `posterUrl`，再次读取不重复请求。
- 无 TMDB ID 的订阅不会按标题猜图。
- 只读 Torra 条目不会因补图写入本地台账。
- 图片代理失败不显示裂图、不形成重试循环。

## 批次 6：契约、文档与全量验证

涉及文件：

- `docs/API_CONTRACT.md`
- `docs/contracts/http-api-contract-v2.json`
- `docs/PRODUCT_DESIGN.md`
- `docs/Fluxa-前端UI改造实施计划.md`
- 相关 TypeScript 类型与页面

实施：

1. 文档记录新增可选字段、RSS 回填摘要和分层匹配语义。
2. API 契约只增加可选响应字段，不删除或重命名旧字段。
3. 执行专项测试后运行全部 Python 回归。
4. 运行 TypeScript 类型检查、Vite 生产构建、API 契约 JSON、Compose 解析和 `git diff --check`。
5. 运行变更、质量、安全和 API 兼容审查。
6. 本地浏览器验证追更海报、资源搜索、任务普通/技术原因和首页季集展示；不执行外部写操作。

完成标准：

- 规格中的 12 条验收标准全部有自动测试或可重复的浏览器证据。
- 未跟踪的 `frontend-reference.html` 始终不进入提交。
- 每个批次可独立回滚，不依赖外部自动化写入才能验证。

完成记录（2026-07-23）：

- 285 项 Python 回归、53 条 v2 机器契约、TypeScript 类型检查、Vite 生产构建、Compose 解析和 `git diff --check` 已通过。
- 本地浏览器已只读验证首页季集定位、普通/技术原因分层、正常保护和资源搜索的明确空结果反馈，未执行外部写操作。
- 本机没有可用于复现的正式 RSS/本地订阅数据，RSS 固定身份样本与追更海报补齐由临时 SQLite、模拟 TMDB 响应和端到端自动化测试覆盖，不把自动化结果表述为正式数据实机验收。
