# Mineradio 嵌入迁移状态

首次记录：2026-07-07

最近复核：2026-07-16

## 最终方向

项目首页已经采用快速嵌入路线：

- React 负责媒体数据、媒体库选择、当前媒体项状态和页面壳。
- Mineradio 运行在 `/mineradio/embed` iframe 中。
- 服务端默认向项目内置 `vendor/mineradio-public/index.html` 注入桥接脚本；`MINERADIO_PUBLIC_DIR` 可覆盖为外部 Mineradio public 目录，本地内置目录缺失时才回退到 `D:\Mineradio\resources\app\public`。
- iframe 通过 `postMessage` 接收首页数据，并暴露 `window.MineradioAPI`。

这条路线已经取代旧的 React/TypeScript 分模块重写视觉舞台方案。

## 当前有效文件

- `src/components/media-hall/MediaHall.tsx`
- `src/components/media-hall/MineradioEmbed.tsx`
- `src/components/media-hall/MediaQueuePanel.tsx`
- `server/routes/mineradioRoutes.ts`
- `services/nasemby-core/app/mineradio_runtime.py`
- `services/nasemby-core/app/mineradio_fragments/embed-head.html`
- `services/nasemby-core/app/mineradio_fragments/embed-tail.html`
- `vendor/mineradio-public/`
- `src/styles/global.css`
- `vite.config.ts`
- `server/index.ts`

## 桥接行为

- `MediaHall` 加载 `/api/media/home`，并把 `items`、`libraries`、`activeItem`、`activeLibraryId`、`visualFx` 传给 `MineradioEmbed`。
- `MineradioEmbed` 向 iframe 发送 `mcc:mineradio-data`。
- 注入脚本把媒体库和媒体项映射成 Mineradio shelf 兼容数据。
- 点击媒体库卡片会向 React 发送 `mineradio:library-select`。
- 点击或滚动到媒体项会向 React 发送 `mineradio:item-select`。
- 左侧 React 面板提供媒体库 / 当前队列浏览，并与 iframe 的当前项同步。
- 音频播放入口被覆盖，不会启动 Mineradio 播放流程。

## iframe 暴露接口

注入脚本暴露：

- `window.MineradioAPI.updateData(payload)`
- `window.MineradioAPI.updateVisualFx(visualFx)`
- `window.MineradioAPI.selectItem(index)`
- `window.MineradioAPI.selectLibrary(libraryId)`
- `window.MineradioAPI.reset()`
- `window.MineradioAPI.destroy()`

## 已删除旧代码

废弃的 React/TypeScript 视觉重写路线已经删除；对应的旧首页舞台、海报轨道、旧视觉控制台 CSS 也已从 `src/styles/global.css` 删除。

## 验证记录

最近检查：

- `npm run build`：通过
- `quality_checker.js src --json`：通过，无 issue
- `quality_checker.js server --json`：通过，无 issue
- 旧首页符号源码扫描：无残留引用
- `/`：HTTP 200
- `/mineradio/embed`：HTTP 200
- `/api/media/home`：HTTP 200
- 注入 Mineradio 桥接脚本：Node 语法解析通过
- `change_analyzer.js --json`：脚本可运行；由于当前目录不是有效 git 仓库，未返回 diff 级变更列表

2026-07-16 Python 后端迁移复核：

- Python 已直接提供 `/mineradio/embed` 和 `/mineradio/*` 静态资源；Compose 已切到单容器 Python 公开入口，旧运行后端已删除。注入片段以迁移完成时的 SHA-256 快照继续回归。
- Python 注入头、桥接尾与 `server/routes/mineradioRoutes.ts` 模板逐字契约测试通过，base、音乐 API 拦截、封面处理和消息名保持不变。
- 项目内置 138 万字节 `index.html` 的真实注入返回 200，注入脚本经 Node 语法解析通过。
- 桌面和 390×844 移动视口均显示原星河/WebGL 画布与视觉设置入口，浏览器控制台 0 条错误。
- React、影院大厅、顶部导航、媒体队列、Three.js/GSAP 源码和 `vendor/mineradio-public` 均未修改。

2026-07-07 补充：

- 新增 `MediaQueuePanel`，恢复左侧媒体库 / 当前队列浏览入口。
- 面板默认贴左侧只露出把手，鼠标悬停或图钉常开后展开。
- 面板选择媒体项会更新首页当前焦点，选择媒体库会重新加载该库并切到队列视图。
- 修复右下 Mineradio 预设切换回跳：iframe 手动切换预设会回传 React，周期性 flush 不再用旧父级视觉参数覆盖用户选择。
- 修复竖版电影海报被 Mineradio 方形封面逻辑裁头：桥接层会先生成完整海报安全图，再传给粒子舞台，两侧用模糊背景填充。
- 修复嵌入态视觉控制台尺寸偏小：PC 端 `#fx-panel` 改为右侧整栏式 `top/bottom` 布局，避免动态等内容较少的 tab 缩成右下角小浮窗。
- 下调首页默认 shelf 尺寸：`shelfSize` 默认值从 `1` 调整为 `0.82`，并迁移旧本地默认值，降低右侧媒体库 / 队列卡片对主视觉的遮挡。
- 调整右侧 shelf 卡片比例：卡片从原版 2:1 收窄到约 1.83:1，并给封面区域设置宽度上限，避免文字区过窄或电影海报被拉伸。
- 微调嵌入态视觉控制台宽度：PC 端 `#fx-panel` 从 `536px` 收到 `500px`，保留整栏高度但降低右侧遮挡。
- 2026-07-08 补充：
  - 嵌入态视觉控制台最终收窄为 PC `480px`，展开态右边距 `48px`，并补充 `#fx-fab` 点击兜底，避免按钮事件在嵌入页中偶发不生效。
  - 移动端仅在 iframe bridge 层覆写 shelf 参数为 `shelfSize=0.65`、`shelfOffsetX=-1.2`，避免右侧当前播放卡和媒体库卡片被窄屏裁切；PC 仍沿用默认 `0.82 / -0.76`。
  - `/api/media/external-image` 对上游 404/非 2xx 图片返回内置 SVG 占位图，避免 fallback 海报失效时首页控制台出现图片加载错误。
