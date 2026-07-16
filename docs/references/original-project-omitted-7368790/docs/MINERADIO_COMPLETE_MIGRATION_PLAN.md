# Mineradio 首页嵌入迁移计划

日期：2026-07-07

## 当前决策

首页采用“直接嵌入 Mineradio 原始视觉”的快速路线，不再继续逐模块重写 Three.js 视觉舞台。

这条路线的目标是：

- 保留 Mineradio 的 Three.js 视觉、封面粒子、视觉预设和 shelf 交互。
- React 继续负责媒体中心数据、当前媒体库、当前媒体项和页面壳。
- 不迁音乐播放器、登录、搜索、歌词、播客、评论、收藏和音乐平台歌单业务。

## 实现结构

```text
React 首页
  MediaHall
    MineradioEmbed      -> iframe /mineradio/embed
    MediaQueuePanel     -> 左侧媒体库 / 当前队列

Express 服务端
  /mineradio/embed      -> 动态读取 Mineradio index.html 并注入桥接脚本
  /mineradio/*          -> 静态代理 Mineradio 原始资源
```

## 数据流

1. `MediaHall` 请求 `/api/media/home`。
2. `MineradioEmbed` 通过 `postMessage` 把 `items`、`libraries`、`activeItem`、`activeLibraryId`、`visualFx` 发给 iframe。
3. 注入脚本把媒体库和媒体项映射为 Mineradio shelf 兼容队列。
4. iframe 点击媒体库卡片回传 `mineradio:library-select`。
5. iframe 点击或滚动媒体项回传 `mineradio:item-select`。
6. `MediaQueuePanel` 提供左侧媒体库 / 当前队列浏览，选择结果同步到 React 和 iframe。

## 服务端注入

`server/routes/mineradioRoutes.ts` 会在运行时读取项目内置资源：

```text
vendor/mineradio-public/index.html
```

该目录复制自 `D:\Mineradio\resources\app\public`。VPS / Docker 部署默认直接使用内置资源，不需要额外挂载 Mineradio 目录；`MINERADIO_PUBLIC_DIR` 仅用于覆盖为另一份 Mineradio public 资源。本地开发时如果内置目录不存在，才回退到 `D:\Mineradio\resources\app\public`。

并注入：

- `<base href="/mineradio/">`
- iframe 嵌入样式
- 音乐业务 API guard
- 媒体中心数据桥接脚本
- `window.MineradioAPI`

## 保留范围

- Three.js 视觉舞台
- 封面粒子效果
- 视觉预设
- 3D shelf 展示和交互
- 视觉控制台
- 原始 assets / vendor 资源引用
- 左侧媒体库 / 当前队列浏览入口

## 剔除范围

- 音频播放
- 播放器底栏和迷你队列
- 音乐平台登录
- 搜索
- 歌词
- 播客
- 评论
- 收藏和音乐歌单业务
- Electron 桌面更新器、桌面歌词等桌面业务

## 当前有效文件

- `src/components/media-hall/MediaHall.tsx`
- `src/components/media-hall/MineradioEmbed.tsx`
- `src/components/media-hall/MediaQueuePanel.tsx`
- `server/routes/mineradioRoutes.ts`
- `vendor/mineradio-public/`
- `src/styles/global.css`
- `vite.config.ts`
- `server/index.ts`

## 验收标准

- 首页能加载 `/api/media/home` 示例或 Emby 数据。
- `/mineradio/embed` 返回 Mineradio 原始视觉并暴露 `window.MineradioAPI`。
- 音频播放入口被覆盖，不启动真实播放。
- 媒体库卡片点击能切库。
- 媒体项点击、滚动、方向键、左侧队列选择能同步当前焦点。
- 左侧面板折叠态只露出把手，展开后可浏览媒体库和当前队列。
- `npm run build` 通过。

## 已废弃路线

逐模块 TypeScript 重写视觉舞台的方案已经停止。相关旧源码已删除，后续不要再按该路线新增视觉舞台、海报轨道或独立 shader 模块。
