# Fluxa 证据所有权、日历与 RSS 身份实施计划

依据：docs/superpowers/specs/2026-07-22-evidence-ownership-calendar-rss-design.md

## 当前进度

截至 2026-07-23：

- 批次 1 至 7 已完成专项测试：唯一证据所有权、脱敏新旧对比、正常保护、集级证据、日历摘要/详情、移动日历和首页安全摘要均已落地。
- 批次 8 已完成：RSS 身份增量迁移、明确 ID 提取、冲突状态、唯一追更补充、身份筛选和中文详情抽屉已通过后端专项测试与 TypeScript 检查。
- 批次 9 已完成：桌面主导航保留五个核心页面，影院大厅使用独立入口，种子库进入任务中心高级入口；旧路由保持有效。
- 批次 10 与阶段 H 收口已完成：271 项 Python 回归、TypeScript、生产构建和 52 条 v2 API 契约通过；桌面及 390×844 深浅主题、月/周日历、44px 触控区和 RSS 身份回填已完成浏览器验收，发布后只保留真实家庭影音链路冒烟。
- 本轮未执行任何 Torra、qBittorrent、115、Symedia、下载、删除或推送等真实外部写操作。

## 批次 1：证据候选与唯一所有权

涉及文件：

- services/nasemby-core/app/task_chain_runtime.py
- services/nasemby-core/app/task_chain_v2_runtime.py
- services/nasemby-core/tests/test_task_chain_runtime.py
- services/nasemby-core/tests/test_task_chain_v2_runtime.py

实施：

1. 建立目标规范化、年份提取、候选收集和统一裁决纯函数。
2. TMDB 精确匹配只接受非空有效 ID。
3. 电视剧回退要求类型、保守标题、季数和唯一候选。
4. 电影回退要求类型、保守标题、年份和唯一候选。
5. qB、Torra、Symedia 证据统一生成所有权元数据。
6. 多候选证据不绑定，生成 EVIDENCE_OWNER_CONFLICT。
7. 任务输入重排后所有权结果必须相同。

验证：

- 空 TMDB 不会精确匹配。
- 同一 Symedia 文件面对 36 个无关电影时没有错误所有者。
- 同一 artifactKey 最多一个 ownerTargetKey。

## 批次 2：只读新旧串链对比

涉及文件：

- services/nasemby-core/app/task_chain_v2_runtime.py
- services/nasemby-core/tests/test_task_chain_v2_runtime.py
- docs/API_CONTRACT.md

实施：

1. 在任务详情快照中增加脱敏 ownershipComparison。
2. 只统计新增绑定、解除绑定、冲突和未识别数量。
3. 不返回路径、Hash、Token、Cookie、Passkey 或完整候选内容。

验证：

- 对比读取无外部写操作。
- 对比结果不暴露敏感字段。

## 批次 3：结构化正常保护

涉及文件：

- services/nasemby-core/app/task_exception_runtime.py
- services/nasemby-core/app/task_chain_runtime.py
- services/nasemby-core/tests/test_task_exception_runtime.py
- services/nasemby-core/tests/test_home_summary_runtime.py

实施：

1. 建立保护规则表和 reasonCode。
2. 旧文本只作为兼容识别来源，并记录 matchedProtectionRule。
3. 混合真实失败优先 action_required。
4. 全部保护性拒绝归入 protected。

验证：

- “评分低于目标文件，取消覆盖”等现有文本全部归入 protected。
- 正常保护不计入首页 actionRequired。

## 批次 4：集级证据集合

涉及文件：

- services/nasemby-core/app/task_chain_runtime.py
- services/nasemby-core/app/task_chain_v2_runtime.py
- services/nasemby-core/tests/test_task_chain_runtime.py
- services/nasemby-core/tests/test_task_chain_v2_runtime.py

实施：

1. 解析单集、范围、多集、S00、绝对集数和季包文件列表。
2. 从 qB、Torra、Symedia 生成 episodeEvidence。
3. 合并相同集号范围的阶段证据，不从纯季级状态推导集级状态。

验证：

- S01E03、S01E03-E05、S00 和明确季包文件解析正确。
- 没有集号的季包不产生单集完成证据。

## 批次 5：日历摘要、详情和集级状态

涉及文件：

- services/nasemby-core/app/calendar_timeline_runtime.py
- services/nasemby-core/tests/test_calendar_timeline_runtime.py
- docs/contracts/http-api-contract-v2.json
- docs/API_CONTRACT.md
- src/services/api.ts
- src/types/subscriptions.ts

实施：

1. 日历仅使用同季同集 episodeEvidence。
2. 保留无新参数的旧响应。
3. 增加 view=summary 月摘要。
4. 增加 date=YYYY-MM-DD&view=detail 当日详情。
5. 支持 from、to 并保持 ETag。

验证：

- 季级任务不再批量标绿。
- 月摘要响应小于 200KB。
- 旧请求、摘要请求和日期详情契约均通过。

## 批次 6：移动日历与五项导航

涉及文件：

- src/components/layout/AppTopNav.tsx
- src/components/pages/CalendarPage.tsx
- src/styles/shell.css
- src/styles/calendar.css

实施：

1. 移动导航显示首页、发现、追更、任务中心和日历。
2. 移动月视图只显示日期、状态点和数量。
3. 移动周视图改成纵向日期列表。
4. 点击日期按需读取详情。
5. 移除移动端 860px 最小宽度和横向滚动。

验证：

- 390×844、430×932 无横向溢出。
- 深浅主题文字和状态均可读。

## 批次 7：首页安全摘要

涉及文件：

- services/nasemby-core/app/home_summary_runtime.py
- services/nasemby-core/tests/test_home_summary_runtime.py
- src/components/pages/Overview.tsx

实施：

1. 建立用户级错误摘要映射。
2. 首页标题补充明确季集。
3. 路径、Hash 和内部 ID 不进入首页响应。
4. 冲突证据只计一项，不复制到候选目标。

验证：

- 首页不出现文件路径、Hash 或内部 Key。
- 正常保护和真实异常计数一致。

## 批次 8：RSS 身份与中文详情

涉及文件：

- services/nasemby-core/app/private_rss_parser.py
- services/nasemby-core/app/private_rss_repository.py
- services/nasemby-core/app/private_rss_api_runtime.py
- services/nasemby-core/tests/test_private_rss_parser.py
- services/nasemby-core/tests/test_private_rss_repository.py
- services/nasemby-core/tests/test_private_rss_api_runtime.py
- src/types/rssSeedLibrary.ts
- src/services/api.ts
- src/components/pages/RssSeedLibraryPage.tsx
- src/styles/rss-seed-library.css

实施：

1. SQLite 增量迁移身份字段。
2. 从 RSS 结构化字段、纯文本简介和公开 ID 提取 TMDB/IMDb。
3. 多身份候选标记 conflict，不做模糊标题反向认领。
4. 增加 identityStatus 可选筛选。
5. 增加按需详情请求和中文抽屉。
6. 简介仅以纯文本渲染。

验证：

- identified、conflict、unidentified 均有覆盖。
- 旧 RSS 请求保持兼容。
- 页面与日志不回显下载地址或 Passkey。

## 批次 9：桌面导航收口

涉及文件：

- src/components/layout/AppTopNav.tsx
- src/components/pages/TasksCenter.tsx
- src/app/App.tsx
- src/styles/shell.css
- src/styles/tasks.css

实施：

1. 桌面主导航保留五个核心页面。
2. 影院大厅改为独立入口。
3. 种子库放入任务中心高级入口。
4. 保留旧路由和直接访问。

验证：

- 旧 /hall 与 /rss-library 可访问。
- 桌面导航不拥挤，移动端保持五项。

## 批次 10：全量验收与发布准备

1. 运行 Python 全量测试。
2. 运行 v2 契约检查、TypeScript 和生产构建。
3. 运行 git diff --check、变更完整性、质量和安全扫描。
4. 浏览器验证 1920×1080、1366×768、430×932、390×844 深浅主题。
5. 检查任务、首页、日历和 RSS 数字一致性。
6. 保存新旧串链只读对比结果，不执行外部写操作。
