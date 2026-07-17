# 状态首屏与前端可维护性实施计划

日期：2026-07-17

设计依据：`docs/superpowers/specs/2026-07-17-status-first-ui-maintainability-design.md`

## 1. 目标与硬边界

本轮完成工作页状态首屏、CSS 模块化、文案层级、工作页字体混排、WCAG AA 对比度和工作页键盘导航。

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

- [ ] 记录开始提交和干净工作区状态。
- [ ] 保存以下冻结路径的 Git blob ID：
  - `src/components/media-hall/MediaHall.tsx`
  - `src/components/media-hall/MineradioEmbed.tsx`
  - `src/components/media-hall/MediaQueuePanel.tsx`
  - `vendor/mineradio-public/**`
- [ ] 在浏览器中序列化所有 `.media-hall*`、`.mineradio-*`、`.media-queue-*` CSSStyleRule 及所属媒体查询，保存规则数量和 SHA-256 摘要。
- [ ] 记录 1440×900、1024×768、390×844 三种尺寸下的大厅截图基线、当前媒体标题、抽屉状态和控制台错误数量。
- [ ] 记录当前生产 CSS 文件大小和 `global.css` 行数。

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

- [ ] `src/main.tsx` 改为导入 `./styles/index.css`。
- [ ] `index.css` 按 `foundation → shell → global → workbench → overview → discover → tasks → calendar → control-room → settings` 导入。
- [ ] `global.css` 原位置只保留影院大厅冻结规则；规则文本和顺序不得改变。
- [ ] 基础令牌、reset 和未改变语义的基础元素规则移动到 `foundation.css`。
- [ ] 顶部导航、应用外壳与认证外层规则移动到 `shell.css`。
- [ ] 工作页共享背景、面板、按钮、确认框和响应式规则移动到 `workbench.css`。
- [ ] 各页面规则按根命名空间移动到对应模块；响应式规则跟随模块。
- [ ] 本阶段不重命名类、不调整颜色、不改变字号、不删除重复规则。
- [ ] 确认 Vite 仍输出单个合并压缩 CSS 资源。

### 验证

- [ ] 大厅冻结路径 blob ID 不变。
- [ ] 大厅 CSSStyleRule 数量与摘要和阶段 0 完全一致。
- [ ] 工作页与大厅三尺寸截图无非预期差异。
- [ ] CSS 中不存在循环 `@import`。
- [ ] 每个页面样式文件只作用于自身根命名空间或登记过的共享组件。

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

- [ ] 在 `foundation.css` 定义本地 `MCC Latin Mono`，`unicode-range` 只覆盖 `U+0020-007E` 与 `U+00A0-024F`。
- [ ] 定义 `--font-ui` 和 `--font-code`；不下载、不打包新字体。
- [ ] 工作页正文、标题、状态和按钮使用 `--font-ui`。
- [ ] 工作页的 `code`、路径、环境变量、API 和 ID 使用 `--font-code`。
- [ ] 中文路径与中文说明必须回退到 `--font-ui`。
- [ ] 业务数字使用 `font-variant-numeric: tabular-nums`，不统一改为代码字体。
- [ ] 删除工作页中重复、顺序不一致的字体栈；不修改大厅相关字体规则。
- [ ] 在 `.ops-page` 范围建立工作页文字颜色令牌，不覆盖大厅根级 `--muted` 和 `--faint`。
- [ ] 对比度脚本计算 sRGB 相对亮度，验证文字颜色在页面底、普通面板和控制面板上的比值。
- [ ] 普通正文、说明、错误、路径达到 4.5:1；可见焦点达到 3:1。
- [ ] 低对比度只保留在边框、光晕和不承载信息的装饰中。

### 验证

```powershell
node scripts/check-work-page-contrast.mjs
npm run typecheck
npm run build
git diff --check
```

- [ ] 浏览器计算样式确认英文路径为 `MCC Latin Mono`，中文片段回退 UI 字体。
- [ ] 大厅 CSS 摘要、截图和计算样式不变。

### 提交

`style: unify work page typography and contrast`

## 5. 阶段 3：建立共享状态首屏

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

- [ ] `PageStatusHeader` 只接收 `title`、`context`、`status`、`detail`、`tone` 和 `actions`，不请求 API。
- [ ] `<h1>` 只显示页面名，字号 18–22px。
- [ ] 实时状态使用独立元素和 `aria-live="polite"`，字号 30–38px。
- [ ] 右侧最多一个主操作和一个刷新操作。
- [ ] 加载、失败、空数据和正常数据有独立文案，不把失败显示成零。
- [ ] 移动端顺序固定为页面名、当前状态、操作。

### 页面接入

- [ ] 总览：显示 `PT 主链正常` 或 `N 项需要检查`；补充订阅数、活跃下载和今日入库；保留“查看任务中心”。
- [ ] 任务中心：显示进行中与卡住数量；加载和错误单独处理；把刷新入口放入状态首屏，避免重复操作。
- [ ] 控制室：显示在线服务数量和需检查数量；保留刷新全部服务。
- [ ] 日历：显示本月待播、逾期和已入库数量；月份切换仍留在月历。
- [ ] 发现：显示当前来源、结果数、搜索词或筛选数；操作只聚焦搜索框。
- [ ] 我的订阅：显示订阅总数和未完成数；保留订阅设置入口。
- [ ] 订阅设置：显示 `modeLabel`；拿不到调度状态时不猜测已启用。
- [ ] 系统设置：`App` 把已有 `health` 传给 `SettingsPage`，显示配置数量；不重复请求健康接口。
- [ ] 删除所有工作页 `.ops-eyebrow` 与 `.ops-deck` JSX。
- [ ] 删除对应共享样式；卡片内部小标保留，但必须描述数据含义。

### 文案层级检查清单

- [ ] `<h1>` 只是页面名。
- [ ] 首屏最醒目文字来自实时状态。
- [ ] 状态说明正常、等待、失败或需要处理。
- [ ] 工具名、API、路径和匹配依据位于次级层。
- [ ] 空状态说明下一步。
- [ ] 按钮、确认框和完成提示使用一致动词。

### 验证

```powershell
rg -n "ops-eyebrow|ops-deck" src/components/pages src/styles
npm run typecheck
npm run build
git diff --check
```

- [ ] 每个工作页只有一个页面名 `<h1>`。
- [ ] 不新增 API，不改变 API 响应字段。
- [ ] 三尺寸状态首屏无断词、遮挡或无意义留白。
- [ ] 大厅冻结检查继续通过。

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

- [ ] 顶部导航保留原生按钮与 `aria-current`，补齐一致的 `focus-visible`。
- [ ] 任务筛选、活动筛选、日历类型、订阅类型、资源来源和季选择实现左右方向键、Home、End 与 roving `tabIndex`。
- [ ] 自动激活型标签在焦点移动后同步选中；不使用隐藏焦点。
- [ ] 两个现有确认框统一使用 `ConfirmDialog`，组件只负责对话框语义、焦点进入、焦点约束、Escape 和焦点返回，不包含 qB 或 Emby 业务逻辑。
- [ ] 确认框打开后焦点进入对话框，Escape 关闭，关闭后回到触发按钮。
- [ ] 图标按钮拥有唯一中文可访问名称。
- [ ] 可见焦点对比度达到 3:1，并支持 `prefers-reduced-motion`。
- [ ] 不修改 `src/components/media-hall/**` 和大厅 CSS。

### 验证

- [ ] 从顶部导航开始，只用 Tab、Shift+Tab、方向键、Home、End、Enter、Space 和 Escape 完成工作页核心流程。
- [ ] 对话框关闭后焦点返回正确按钮。
- [ ] 页面滚动和筛选不会出现焦点丢失。
- [ ] 大厅冻结检查继续通过。

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

- [ ] 用 `rg` 对照 TSX 类名与样式选择器。
- [ ] 只删除确认被 `ops-*` 新实现覆盖且无引用的工作页规则。
- [ ] 无法证明无引用的规则继续保留，不为追求行数强删。
- [ ] 大厅冻结规则不参与清理。
- [ ] 更新 UI 标准中的状态首屏、字体、颜色、焦点和 CSS 文件归属。
- [ ] 在当前计划中记录完成范围与浅色模式、大厅键盘导航延期项。

### 验证

- [ ] 所有模块样式文件职责清楚，没有重新出现超过 1500 行的单页样式文件；超过时必须记录原因并继续拆分。
- [ ] 工作页无孤立选择器、重复字体栈和重新引入的 `.ops-eyebrow`。
- [ ] 大厅冻结检查继续通过。

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

- [ ] Python 既有 85 项测试全部通过。
- [ ] 文案扫描、CSS 边界扫描和文档同步检查通过。
- [ ] 变更校验通过。

### 浏览器检查

- [ ] 1440×900：全部工作页状态首屏、完整网格和长列表。
- [ ] 1024×768：状态首屏换行、操作区和侧栏收缩。
- [ ] 390×844：页面名、状态、操作顺序；导航和筛选横向滚动。
- [ ] 工作页键盘流程完整，浏览器控制台无新增错误。
- [ ] 中英混合路径、环境变量、API、ID 与中文说明字体正确。
- [ ] 大厅三尺寸截图、交互、CSS 摘要和冻结路径 blob ID 与阶段 0 一致。

### 文档

- [ ] 更新 `docs/PLAN.md` 当前状态和下一步。
- [ ] 更新 `docs/UI_STANDARD.md` 最终类名与检查清单。
- [ ] 在本计划记录实际 CSS 行数、构建产物大小、对比度结果和测试数量。

### 提交

`docs: close status-first ui refactor`

## 9. 完成定义

只有同时满足以下条件才能标记完成：

1. 工作页不再显示宣言式 Hero，页面名与实时状态层级清楚。
2. `global.css` 不再承载全部工作页样式；模块边界与导入顺序有文档。
3. 代码、路径使用等宽拉丁字体，中文回退正文字体。
4. 工作页关键信息达到 WCAG AA，对比度有脚本结果。
5. 工作页标签与确认框可通过键盘完成核心操作。
6. 影院大厅全部冻结检查通过，没有代码、样式、交互或视觉变化。
7. 前端构建、依赖审计、Python 测试和三尺寸浏览器验收全部通过。
8. 文档、计划和提交记录完整，工作区干净。
