# 首页与 Mineradio 当前差异

更新时间：2026-07-07

## 核心结论

当前首页不是重新实现 Mineradio，而是在 iframe 中运行 Mineradio 原始页面，并通过服务端注入脚本把它改造成媒体中心视觉舞台。

React 负责媒体中心状态，Mineradio 负责视觉。这个边界是当前实现的核心。

## 同源部分

- 使用项目内置 `vendor/mineradio-public/index.html` 作为视觉来源；该目录复制自 `D:\Mineradio\resources\app\public`。
- 复用 Mineradio 的 Three.js 舞台、封面粒子、视觉预设、视觉控制台和 shelf。
- 复用 Mineradio 的 assets / vendor 资源。
- 保留 shelf 卡片点击、滚动和视觉动效。

## 改造部分

- 服务端注入 `<base href="/mineradio/">`，确保 iframe 内资源走 `/mineradio/*`。
- 服务端注入音乐业务 API guard，阻断登录、搜索、评论、歌词、播客等请求。
- 注入桥接脚本暴露 `window.MineradioAPI`。
- React 通过 `mcc:mineradio-data` 把媒体中心数据传给 iframe。
- iframe 通过 `mineradio:item-select` 和 `mineradio:library-select` 把选择回传给 React。
- 左侧浏览入口由 React 的 `MediaQueuePanel` 实现，只展示媒体库和当前媒体项队列。

## 有意保留的差异

- 不启动音频播放。
- 不显示播放器底栏和迷你队列。
- 不显示登录、搜索、歌词、播客、评论、收藏和音乐平台歌单。
- 不使用 Mineradio 原左侧音乐歌单面板作为业务面板。
- 不把媒体中心首页改成音乐播放器。

## 当前项目文件

- 首页状态和布局：`src/components/media-hall/MediaHall.tsx`
- Mineradio iframe 包装：`src/components/media-hall/MineradioEmbed.tsx`
- 左侧媒体库 / 队列面板：`src/components/media-hall/MediaQueuePanel.tsx`
- Mineradio 注入和静态资源代理：`server/routes/mineradioRoutes.ts`
- 首页样式：`src/styles/global.css`

## 验证重点

- iframe 是否加载非空。
- `window.MineradioAPI` 是否存在。
- React -> iframe 数据是否能刷新 shelf。
- iframe -> React 选择消息是否能同步当前焦点。
- 左侧面板切库、切队列项是否能同步到底部状态和 iframe。
- 被剔除的音乐业务入口是否仍被隐藏或拦截。
