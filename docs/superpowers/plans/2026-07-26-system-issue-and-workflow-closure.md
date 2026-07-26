# Fluxa 系统异常闭环与日常页面收口实施计划

设计依据：`docs/superpowers/specs/2026-07-26-system-issue-and-workflow-closure-design.md`

## 1. 完成定义

以下条件全部满足才算完成：

1. 秒传状态不再只凭失败数或 `nextRunAt` 判定；10 分钟宽限、24 小时计划上限、自动调度能力和时区均有测试。
2. 首页可精确进入秒传系统问题面板；当前实机状态显示分类级批次趋势和 Torra 提供的下次计划。
3. 自动恢复有效时不突出手动重试；手动动作具备预检、确认、幂等、锁、持久化和状态复查。
4. 加入追更返回五类明确 activation，页面不再混用“订阅成功”“自动获取”和“仅保存”。
5. 发现页只显示最近 3 条追更，不渲染完整筛选和年份控件。
6. RSS 三类资源计数互斥且总和恒等于资源总数；普通页面不出现“下载入口”，不泄露下载地址、详情地址或 Passkey。
7. 任务快照变化可解释，活动重点视图在 limit 前折叠后台同步。
8. 手机端全局异常角标可见、可点击、可分享，320 至 430 像素无重叠。
9. 设置默认收口为连接、自动化、通知、安全四组，其余进入高级设置。
10. 后端全量回归、前端类型检查/构建、契约校验、浏览器深浅主题与多视口回归全部通过。
11. 代码、文档、Git 和多架构镜像同步；fnOS `/healthz.revision` 命中新提交；最后直接关机。

## 2. 约束

- 不读取或挂载 Torra SQLite，不扫描 Torra 私有目录。
- 不从 Symedia、qB、115、标题或其他成功 job 推断当前失败文件。
- 自动化测试不得调用真实外部写接口。
- 新响应字段保持可选，旧字段和旧路由继续兼容。
- 不修改真实业务开关、下载任务、追更台账或持久化数据。
- 每个文件同一时间只交给一个执行者修改。

## 3. 波次一：秒传系统问题闭环

### 3.1 后端状态机与安全摘要

涉及文件：

- `services/nasemby-core/app/torra_read_runtime.py`
- `services/nasemby-core/app/secupload_issue_runtime.py`（新增）
- `services/nasemby-core/app/task_public_runtime.py`
- `services/nasemby-core/app/task_chain_v2_runtime.py`
- `services/nasemby-core/app/home_summary_runtime.py`
- `services/nasemby-core/app/main.py`

步骤：

1. 在 Torra 读取层保留安全的 config item、task、schedule 和 recent run 结构，建立内部分类映射。
2. 新增纯函数状态机，固定 600 秒宽限和 86400 秒计划上限。
3. 构造分类公开摘要：稳定摘要 ID、分类名、最近批次、近三批失败数、策略、下次计划和证据能力。
4. 公共 presenter 只放行安全字段，继续清空插件 key、原始分类 ID、目录和路径。
5. 新增只读系统问题接口，并把摘要附加到任务 summary/chains 的可选 `systemIssues`。
6. 首页关注项改为 `/tasks?systemIssue=secupload_failures`，recovering 不计入红色真实异常。

验证：

- 新增 `services/nasemby-core/tests/test_secupload_issue_runtime.py`。
- 扩展 `test_torra_read_runtime.py`、`test_task_chain_v2_runtime.py`、`test_home_summary_runtime.py`。
- 覆盖跨天、时区、宽限边界、24 小时上限、字段缺失、接口失败和脱敏。

### 3.2 手动重试动作

涉及文件：

- `services/nasemby-core/app/secupload_issue_runtime.py`
- `services/nasemby-core/app/quality_watch_repository.py`（只复用现有 provider action，原则上不改 schema）
- `services/nasemby-core/app/torra_read_runtime.py`
- `services/nasemby-core/app/main.py`

步骤：

1. 增加预检接口，重新检查插件、分类、活动运行、自动计划和 `allowManualRun`。
2. 增加确认执行接口，校验幂等键和请求字段白名单。
3. 使用 `provider_actions`，provider=`torra`，action_type=`secupload_retry`，系统目标键固定为 `system:torra:secupload`。
4. claim 前检查全局活动动作和分类级活动动作，竞争返回 409。
5. 调用 Torra 正式任务运行接口并保存 run ID。
6. 状态读取通过插件 `recent_runs` 查找 run ID，终态写回 provider action，再刷新秒传摘要。
7. 自动计划有效时高级操作禁用；只有 action_required 时允许成为主操作。

验证：

- 同幂等键重放；不同幂等键竞争；预检失败不调用上游；Torra 失败释放租约；run ID 轮询；终态复查。

## 4. 波次二：追更生效与活动记录契约

### 4.1 追更 activation

涉及文件：

- `services/nasemby-core/app/subscription_workbench_runtime.py`
- `services/nasemby-core/app/subscription_compat_runtime.py`
- `services/nasemby-core/app/discover_runtime.py`
- `services/nasemby-core/tests/test_subscription_workbench_runtime.py`
- `services/nasemby-core/tests/test_mcc_compat_runtime.py`

步骤：

1. capabilities 增加 `manualFollow` 与 `sourceScan`，保留旧字段。
2. 从保存结果的 replaced、subscription_task、provider、queued、pushed 和 errors 生成五类 activation。
3. 兼容保存接口白名单返回 activation，不公开内部队列键、URL、原始 ID 或错误堆栈。
4. 异步排队只返回 `saved_and_queued`，不提前声称 Torra 已推送。

验证：

- 写入关闭、push 关闭、push 开启但 scheduler 停止、已存在、排队成功、明确推送失败。

### 4.2 活动重点视图

涉及文件：

- `services/nasemby-core/app/activity_log.py`
- `services/nasemby-core/app/activity_api_runtime.py`
- `services/nasemby-core/tests/test_activity_api_runtime.py`

步骤：

1. 增加 `view=important`，先按 category 过滤，再倒序扫描并折叠，最后应用 limit。
2. 只折叠 background success/info/skip 的相同 category/action/status。
3. error 和人工 request ID 永不折叠。
4. 折叠项返回 repeatCount、firstTime、lastTime；默认 raw 行为不变。

验证：

- 200 条后台同步不能挤掉第 201 条人工失败；raw 顺序和数量不变；不同错误不合并。

## 5. 波次三：发现与 RSS 前端收口

涉及文件：

- `src/types/subscriptions.ts`
- `src/types/rssSeedLibrary.ts`
- `src/services/api.ts`
- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/RssSeedLibraryPage.tsx`
- `src/styles/discover.css`
- `src/styles/rss-library.css`

步骤：

1. 更新 capabilities、activation 和 RSS 展示类型。
2. 发现页按钮和成功反馈直接使用 manualFollow/activation。
3. 非追更视图只渲染最近 3 条和“查看全部追更”；完整分支保持在 `/following`。
4. 把 RSS 每条资源归入 explicit_episode、season_pack、scope_pending 中唯一一类。
5. 资源摘要显示总数和三类互斥计数，删除 x/y 集。
6. 预览显示匹配原因、范围、官种未知、执行证据、优先检查理由。
7. 删除普通页面“下载入口”“已保留下载信息”“可下载”等文案；动作改为“交给 Torra 处理”或“查看识别证据”。
8. 不增加直接访问私有详情页的链接。

验证：

- TypeScript 类型检查和生产构建。
- 使用混合 5 条资源样本做浏览器 DOM 断言，三类总和等于 5。
- 浏览器网络与 DOM 检查不出现下载 URL、详情 URL 或 Passkey。
- 390×844 下最近追更最多 3 条、无年份控件、资源摘要不溢出。

## 6. 波次四：任务、移动端和设置

涉及文件：

- `src/types/taskChain.ts`
- `src/types/operations.ts`
- `src/types/homeSummary.ts`
- `src/services/api.ts`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/Overview.tsx`
- `src/components/pages/RuntimeSettingsPanel.tsx`
- `src/components/pages/SettingsPage.tsx`
- `src/components/layout/AppTopNav.tsx`
- `src/styles/tasks.css`
- `src/styles/overview.css`
- `src/styles/runtime-settings.css`
- `src/styles/settings.css`
- `src/styles/shell.css`

步骤：

1. 任务中心读取 systemIssue URL，系统秒传面板优先出现并保持可分享。
2. 轮询只在相同全局口径且 version 改变时计算 userCounts/identity 差值。
3. ledger 明确返回 artifactMigrations 时才显示身份整理数量。
4. 活动默认请求 important，提供 raw 切换，并把 activityView/category 写入 URL。
5. 主导航任务项增加 99+ 封顶角标，异常时准确进入 action_required。
6. 首页移动文案缩为“任务中心”，保持单行。
7. RuntimeSettingsPanel 建立四组常用视图模型和唯一高级区；搜索覆盖全部。
8. 保存后的连接验证按 dirty key 前缀选择服务，不使用展示组 ID。

验证：

- 首次加载、304、筛选切换、加载更多和服务重启不误报差值。
- 320/360/390/430/768/1440 视口，深浅主题和键盘回归。
- 角标不改变导航宽度，不遮挡选中指示器，0 项和读取失败时隐藏。
- 设置敏感字段不回显、字段不重复、dirty key 保存和重启提示保持。

## 7. 契约与文档

涉及文件：

- `docs/API_CONTRACT.md`
- `docs/contracts/http-api-contract-v1.json`
- `docs/contracts/http-api-contract-v2.json`
- `src/DESIGN.md`
- `services/nasemby-core/DESIGN.md`
- `docs/Fluxa-前端UI改造实施计划.md`
- `docs/ROADMAP.md`

要求：

1. 记录秒传状态机、可选 DTO、重试动作、activation、activity view 和安全边界。
2. 记录 RSS 三类互斥口径与普通页面禁用“下载入口”。
3. 记录新 URL 参数 systemIssue/activityView/activityCategory。
4. 文档不得记录真实 Token、Cookie、Passkey、文件路径或 Torra 原始分类 ID。

## 8. 统一验证与发布

1. 运行秒传、订阅、活动、任务、首页、RSS 定向测试。
2. 运行后端全量回归。
3. 运行 `npm run typecheck`、`npm run build`。
4. 运行契约 JSON 校验、compileall、Compose 校验、actionlint 和 `git diff --check`。
5. 用浏览器完成 320/390/768/1440、深浅主题、键盘和 URL 回归。
6. 执行变更校验，确认代码、测试和文档同步。
7. 提交并推送 `main`，等待 Actions 与双架构镜像发布成功。
8. fnOS 执行 pull/recreate，`/healthz.revision` 必须等于新提交 SHA。
9. 实机只读复核秒传分类、下次时间、追更文案、发现最近 3 条、RSS 统计、移动角标和设置分组。
10. 全部通过后直接关闭计算机。

## 9. 并行文件所有权

- Agent A：波次一后端与对应 Python 测试。
- Agent B：波次二后端与对应 Python 测试。
- Agent C：波次三前端与对应样式。
- 主代理：波次四、契约文档、交叉集成、浏览器验收、发布和实机部署。

Agent 不得修改未分配文件；跨边界需求先回报主代理。主代理在合并后统一审查和提交。
