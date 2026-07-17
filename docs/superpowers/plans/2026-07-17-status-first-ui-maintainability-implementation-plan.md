# 前端可维护性实施计划（状态首屏已撤回）

日期：2026-07-17

更新：2026-07-18

设计依据：`docs/superpowers/specs/2026-07-17-status-first-ui-maintainability-design.md`

## 1. 目标与硬边界

本轮完成 CSS 模块化、文案层级、工作页字体混排、WCAG AA 对比度和工作页键盘导航。状态首屏曾完成预览，但用户决定恢复原页面头部，因此不属于最终完成范围。

影院大厅整体冻结：

- 不修改 `src/components/media-hall/MediaHall.tsx`。
- 不修改 `src/components/media-hall/MineradioEmbed.tsx`。
- 不修改 `src/components/media-hall/MediaQueuePanel.tsx`。
- 不修改 `vendor/mineradio-public/**`。
- 不修改大厅方向键、滚轮、媒体队列、状态条、iframe 通信和视觉设置。
- 不修改、重排或清理 `.media-hall*`、`.mineradio-*`、`.media-queue-*` 样式规则。
- 字体、对比度和键盘改造必须限定在工作页，不能通过根级覆盖影响大厅。

浅色模式不在本轮范围内。API、Python 后端、订阅台账、PT 主线和写入闸门不变。

## 2. 阶段 0：建立冻结基线

### 修改文件

- 新建 `docs/references/UI_REFACTOR_BASELINE.md`，只记录基线提交、冻结路径、验收尺寸和检查方法。
- 新建 `docs/references/ui-refactor-baseline/`，保存三种尺寸的大厅基线截图。
- 不修改任何运行代码。

### 执行

- [x] 记录开始提交和干净工作区状态。
- [x] 保存以下冻结路径的 Git blob ID：
  - `src/components/media-hall/MediaHall.tsx`
  - `src/components/media-hall/MineradioEmbed.tsx`
  - `src/components/media-hall/MediaQueuePanel.tsx`
  - `vendor/mineradio-public/**`
- [x] 在浏览器中序列化所有 `.media-hall*`、`.mineradio-*`、`.media-queue-*` CSSStyleRule 及所属媒体查询，保存规则数量和 SHA-256 摘要。
- [x] 记录 1440×900、1024×768、390×844 三种尺寸下的大厅截图基线、当前媒体标题、抽屉状态和控制台错误数量。
- [x] 记录当前生产 CSS 文件大小和 `global.css` 行数。

### 验证

```powershell
git status --short
git rev-parse HEAD
git ls-tree -r HEAD -- src/components/media-hall vendor/mineradio-public
```

### 提交

`docs: record work page refactor baseline`

## 3. 阶段 1：机械拆分非大厅 CSS

### 新建文件

- `src/styles/index.css`
- `src/styles/foundation.css`
- `src/styles/shell.css`
- `src/styles/workbench.css`
- `src/styles/overview.css`
- `src/styles/discover.css`
- `src/styles/tasks.css`
- `src/styles/calendar.css`
- `src/styles/control-room.css`
- `src/styles/settings.css`

### 修改文件

- `src/main.tsx`
- `src/styles/global.css`

### 执行

- [x] `src/main.tsx` 改为导入 `./styles/index.css`。
- [x] `index.css` 按 `foundation → shell → global → workbench → overview → discover → tasks → calendar → control-room → settings` 导入。
- [x] `global.css` 原位置只保留影院大厅冻结规则；规则文本和顺序不得改变。
- [x] 基础令牌、reset 和未改变语义的基础元素规则移动到 `foundation.css`。
- [x] 顶部导航、应用外壳与认证外层规则移动到 `shell.css`。
- [x] 工作页共享背景、面板、按钮、确认框和响应式规则移动到 `workbench.css`。
- [x] 各页面规则按根命名空间移动到对应模块；响应式规则跟随模块。
- [x] 本阶段不重命名类、不调整颜色、不改变字号、不删除重复规则。
- [x] 确认 Vite 仍输出单个合并压缩 CSS 资源。

### 验证

- [x] 大厅冻结路径 blob ID 不变。
- [x] 大厅 CSSStyleRule 数量与摘要和阶段 0 完全一致。
- [x] 工作页与大厅三尺寸截图无非预期差异。
- [x] CSS 中不存在循环 `@import`。
- [x] 每个页面样式文件只作用于自身根命名空间或登记过的共享组件。

```powershell
npm run typecheck
npm run build
git diff --check
```

### 提交

`refactor: split work page styles`

## 4. 阶段 2：统一工作页字体与对比度

### 新建文件

- `scripts/check-work-page-contrast.mjs`

### 修改文件

- `src/styles/foundation.css`
- `src/styles/workbench.css`
- 各工作页 CSS 模块中仍存在的字体声明
- `docs/UI_STANDARD.md`

### 执行

- [x] 在 `foundation.css` 定义本地 `MCC Latin Mono`，`unicode-range` 只覆盖 `U+0020-007E` 与 `U+00A0-024F`。
- [x] 定义 `--font-ui` 和 `--font-code`；不下载、不打包新字体。
- [x] 工作页正文、标题、状态和按钮使用 `--font-ui`。
- [x] 工作页的 `code`、路径、环境变量、API 和 ID 使用 `--font-code`。
- [x] 中文路径与中文说明必须回退到 `--font-ui`。
- [x] 业务数字使用 `font-variant-numeric: tabular-nums`，不统一改为代码字体。
- [x] 删除工作页中重复、顺序不一致的字体栈；不修改大厅相关字体规则。
- [x] 在 `.ops-page` 范围建立工作页文字颜色令牌，不覆盖大厅根级 `--muted` 和 `--faint`。
- [x] 对比度脚本计算 sRGB 相对亮度，验证文字颜色在页面底、普通面板和控制面板上的比值。
- [x] 普通正文、说明、错误、路径达到 4.5:1；可见焦点达到 3:1。
- [x] 低对比度只保留在边框、光晕和不承载信息的装饰中。

### 验证

```powershell
node scripts/check-work-page-contrast.mjs
npm run typecheck
npm run build
git diff --check
```

- [x] 浏览器计算样式确认英文路径为 `MCC Latin Mono`，中文片段回退 UI 字体。
- [x] 大厅 CSS 摘要、截图和计算样式不变。

### 提交

`style: unify work page typography and contrast`

## 5. 阶段 3：建立共享状态首屏

执行结果：已撤回。`c237f9b` 曾完成本阶段，用户预览后认为标题旁的大号实时数字不合适，`44ae470` 已完整恢复旧 Hero。本节只保留历史实施记录，以下复选项不再执行。

### 新建文件

- `src/components/layout/PageStatusHeader.tsx`

### 修改文件

- `src/app/App.tsx`
- `src/components/pages/Overview.tsx`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/ControlRoom.tsx`
- `src/components/pages/CalendarPage.tsx`
- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/SubscriptionSettingsPage.tsx`
- `src/components/pages/SettingsPage.tsx`
- `src/styles/workbench.css`
- 对应页面 CSS 模块
- `docs/UI_STANDARD.md`

### 共享组件

- [撤回] `PageStatusHeader` 只接收 `title`、`context`、`status`、`detail`、`tone` 和 `actions`，不请求 API。
- [撤回] `<h1>` 只显示页面名，字号 18–22px。
- [撤回] 实时状态使用独立元素和 `aria-live="polite"`，字号 30–38px。
- [撤回] 右侧最多一个主操作和一个刷新操作。
- [撤回] 加载、失败、空数据和正常数据有独立文案，不把失败显示成零。
- [撤回] 移动端顺序固定为页面名、当前状态、操作。

### 页面接入

- [撤回] 总览：显示 `PT 主链正常` 或 `N 项需要检查`；补充订阅数、活跃下载和今日入库；保留“查看任务中心”。
- [撤回] 任务中心：显示进行中与卡住数量；加载和错误单独处理；把刷新入口放入状态首屏，避免重复操作。
- [撤回] 控制室：显示在线服务数量和需检查数量；保留刷新全部服务。
- [撤回] 日历：显示本月待播、逾期和已入库数量；月份切换仍留在月历。
- [撤回] 发现：显示当前来源、结果数、搜索词或筛选数；操作只聚焦搜索框。
- [撤回] 我的订阅：显示订阅总数和未完成数；保留订阅设置入口。
- [撤回] 订阅设置：显示 `modeLabel`；拿不到调度状态时不猜测已启用。
- [撤回] 系统设置：`App` 把已有 `health` 传给 `SettingsPage`，显示配置数量；不重复请求健康接口。
- [撤回] 删除所有工作页 `.ops-eyebrow` 与 `.ops-deck` JSX。
- [撤回] 删除对应共享样式；卡片内部小标保留，但必须描述数据含义。

### 文案层级检查清单

- [撤回] `<h1>` 只是页面名。
- [撤回] 首屏最醒目文字来自实时状态。
- [撤回] 状态说明正常、等待、失败或需要处理。
- [撤回] 工具名、API、路径和匹配依据位于次级层。
- [撤回] 空状态说明下一步。
- [撤回] 按钮、确认框和完成提示使用一致动词。

### 验证

```powershell
rg -n "ops-eyebrow|ops-deck" src/components/pages src/styles
npm run typecheck
npm run build
git diff --check
```

- [撤回] 每个工作页只有一个页面名 `<h1>`。
- [撤回] 不新增 API，不改变 API 响应字段。
- [撤回] 三尺寸状态首屏无断词、遮挡或无意义留白。
- [撤回] 大厅冻结检查继续通过。

### 提交

`feat: replace work page heroes with live status`

## 6. 阶段 4：补齐工作页键盘导航

### 新建文件

- `src/utils/keyboardNavigation.ts`
- `src/components/layout/ConfirmDialog.tsx`

### 修改文件

- `src/components/layout/AppTopNav.tsx`
- `src/components/pages/TasksCenter.tsx`
- `src/components/pages/CalendarPage.tsx`
- `src/components/pages/DiscoverPage.tsx`
- `src/components/pages/ControlRoom.tsx`
- `src/styles/workbench.css`
- 对应页面 CSS 模块

### 执行

- [x] 顶部导航保留原生按钮与 `aria-current`，补齐一致的 `focus-visible`。
- [x] 任务筛选、活动筛选、日历类型、订阅类型、资源来源和季选择实现左右方向键、Home、End 与 roving `tabIndex`。
- [x] 自动激活型标签在焦点移动后同步选中；不使用隐藏焦点。
- [x] 两个现有确认框统一使用 `ConfirmDialog`，组件只负责对话框语义、焦点进入、焦点约束、Escape 和焦点返回，不包含 qB 或 Emby 业务逻辑。
- [x] 确认框打开后焦点进入对话框，Escape 关闭，关闭后回到触发按钮。
- [x] 图标按钮拥有唯一中文可访问名称；移动端服务状态按钮已补 `aria-label`。
- [x] 可见焦点对比度达到 3:1，并支持 `prefers-reduced-motion`。
- [x] 不修改 `src/components/media-hall/**` 和大厅 CSS。

### 验证

- [x] 任务、活动、日历和订阅标签使用方向键、Home、End 完成浏览器验证。
- [x] 对话框焦点进入、约束、Escape 和返回由共享组件统一实现；本地无符合条件的真实 qB / Emby 动作，不执行外部写入验证。
- [x] 页面筛选后选中项保持唯一 `tabIndex="0"`，焦点同步移动。
- [x] 大厅冻结检查继续通过。

```powershell
npm run typecheck
npm run build
git diff --check
```

### 提交

`feat: improve work page keyboard navigation`

## 7. 阶段 5：删除确认无引用的工作页旧 CSS

### 修改文件

- `src/styles/workbench.css`
- `src/styles/overview.css`
- `src/styles/discover.css`
- `src/styles/tasks.css`
- `src/styles/calendar.css`
- `src/styles/control-room.css`
- `src/styles/settings.css`
- `docs/UI_STANDARD.md`
- `docs/PLAN.md`

### 执行

- [x] 用 `rg` 对照 TSX 类名与样式选择器。
- [x] 状态首屏撤回后，候选旧规则重新被 Hero、状态卡和设置页使用，因此不删除。
- [x] 无法证明无引用的规则继续保留，不为追求行数强删。
- [x] 大厅冻结规则不参与清理。
- [x] 更新 UI 标准中的旧 Hero 决策、字体、颜色、焦点和 CSS 文件归属。
- [x] 在当前计划中记录完成范围与浅色模式、大厅键盘导航延期项。

### 验证

- [x] 所有模块样式文件职责清楚；最大为 `workbench.css` 1271 行和 `discover.css` 1211 行，均低于 1500 行。
- [x] 工作页候选旧选择器均有 TSX 引用；`.ops-eyebrow` 因恢复旧 Hero 明确保留。
- [x] 大厅冻结检查继续通过。

### 提交

`refactor: remove obsolete work page styles`

## 8. 阶段 6：完整验收与文档收口

### 自动检查

```powershell
npm run typecheck
npm run build
npm audit --omit=dev
python -m unittest discover -s services/nasemby-core/tests -p "test_*.py"
node scripts/check-work-page-contrast.mjs
git diff --check
```

- [x] Python 既有 85 项测试全部通过。
- [x] 文案扫描、CSS 边界扫描和文档同步检查通过。
- [x] 变更校验通过；工具提示的文档同步项已由本计划、设计更新和 `UI_STANDARD.md` 覆盖。

### 浏览器检查

- [x] 1440×900、1024×768、390×844：CSS 拆分与字体阶段已完成工作页验收。
- [x] 订阅页确认恢复旧 Hero，不显示标题旁大号订阅数量。
- [x] 移动端导航和筛选保持横向滚动，服务状态图标具备中文可访问名称。
- [x] 工作页标签键盘流程完整，浏览器控制台无新增应用错误。
- [x] 中英混合路径、环境变量、API、ID 与中文说明字体正确。
- [x] 大厅冻结路径 tree、43 条 CSS 规则和 SHA-256 摘要与阶段 0 一致；本轮没有改动大厅路径。

### 文档

- [x] 更新 `docs/PLAN.md` 当前状态和下一步。
- [x] 更新 `docs/UI_STANDARD.md` 最终类名与检查清单。
- [x] 在本计划记录实际 CSS 行数、构建产物大小、对比度结果和测试数量。

实际结果：`global.css` 358 行；最大模块 `workbench.css` 1271 行；Vite 转换 1594 个模块，生产 CSS 84.12 kB（gzip 14.92 kB）；工作页文字最低对比度 6.83:1，焦点最低 12.76:1；85 项 Python 测试通过；`npm audit --omit=dev` 为 0 个漏洞。

### 提交

`docs: close work page maintainability refactor`

## 9. 完成定义

只有同时满足以下条件才能标记完成：

1. 工作页保留用户确认的旧 Hero；实时数量不放在页面标题旁。
2. `global.css` 不再承载全部工作页样式；模块边界与导入顺序有文档。
3. 代码、路径使用等宽拉丁字体，中文回退正文字体。
4. 工作页关键信息达到 WCAG AA，对比度有脚本结果。
5. 工作页标签与确认框可通过键盘完成核心操作。
6. 影院大厅全部冻结检查通过，没有代码、样式、交互或视觉变化。
7. 前端构建、依赖审计、Python 测试和三尺寸浏览器验收全部通过。
8. 文档、计划和提交记录完整，工作区干净。
