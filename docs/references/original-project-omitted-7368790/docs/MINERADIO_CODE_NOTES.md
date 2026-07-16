# Mineradio 代码结构与实现原理笔记

本文档记录对 `D:\Mineradio` 的代码阅读结果。`D:\Mineradio` 是一个已打包的 Electron 应用目录，真实业务代码在 `D:\Mineradio\resources\app`，不是普通源码仓库根目录。

## 1. 项目定位

Mineradio 是一个桌面音乐播放器，核心形态是：

- Electron 主进程负责窗口、全屏、托盘式附加窗口、登录窗口、全局热键、更新安装器等桌面能力。
- Node 本地 HTTP 服务负责音乐平台代理、登录态、歌曲 URL、歌词、歌单、评论、天气电台、更新下载、beatmap 缓存等 API。
- `public/index.html` 是主要播放器前端，直接使用 Three.js、WebAudio、Canvas、GSAP 和大量原生 DOM 逻辑构建沉浸式播放器。
- `public/desktop-lyrics.html` 和 `public/wallpaper.html` 是 Electron 创建的附加透明/壁纸窗口页面，通过 preload 暴露的 IPC 桥接收主页面状态。

## 2. 主要目录结构

```text
D:\Mineradio\
  Mineradio.exe                  # 打包后的应用入口
  resources\
    app\
      package.json               # 应用元数据，main 指向 desktop/main.js
      server.js                  # 本地 HTTP API 与静态资源服务
      dj-analyzer.js             # 电台/DJ 音频节拍分析
      desktop\
        main.js                  # Electron 主进程
        preload.js               # 主播放器页面 IPC 桥
        overlay-preload.js       # 桌面歌词/壁纸窗口 IPC 桥
      public\
        index.html               # 主播放器 UI + Three.js 场景 + WebAudio 动效
        desktop-lyrics.html      # 桌面歌词悬浮窗
        wallpaper.html           # 桌面壁纸渲染页
        default-user-fx-archive.json
        assets\skull-decimation-points.bin
        vendor\
          three.r128.min.js
          gsap.min.js
          music-tempo.min.js
```

`package.json` 显示版本为 `1.1.1`，主入口是 `desktop/main.js`，依赖包含 `gsap`、`mpg123-decoder`、`NeteaseCloudMusicApi`。更新配置指向 GitHub 仓库 `XxHuberrr/Mineradio`。

## 3. 启动链路

启动链路是典型的“Electron 壳 + 本地 Web 服务 + 前端单页应用”：

1. `desktop/main.js` 先设置 Chromium 性能开关，例如 GPU raster、后台定时器不节流、禁用背景渲染降频等。
2. `createWindow()` 中寻找可用端口，默认从 `3000` 开始。
3. 主进程设置运行时环境变量：
   - `HOST=127.0.0.1`
   - `PORT=<动态端口>`
   - `COOKIE_FILE=<Electron userData>\.cookie`
   - `QQ_COOKIE_FILE=<Electron userData>\.qq-cookie`
   - `MINERADIO_UPDATE_DIR=<Electron userData>\updates`
4. 主进程 `require('../server.js')` 启动本地 HTTP 服务，并等待服务监听完成。
5. 主窗口使用无边框透明窗口加载 `http://127.0.0.1:<port>`。
6. `server.js` 对 `/` 返回 `public/index.html`，其余静态资源从 `public` 目录读取。

这个设计让前端始终按浏览器页面开发，但敏感能力由 Electron 主进程和本地服务包起来。

## 4. Electron 桌面层

### 主窗口

`desktop/main.js` 的主窗口使用：

- `frame: false`
- `transparent: true`
- `contextIsolation: true`
- `nodeIntegration: false`
- `preload: desktop/preload.js`

窗口状态通过 `desktop-window-state` 事件回传给页面，页面据此切换最大化、全屏、主屏/副屏等 UI 状态。

### preload 暴露的主页面 API

`desktop/preload.js` 只把受控能力暴露为 `window.desktopWindow`，包括：

- 窗口控制：最小化、全屏/退出全屏、关闭、读取窗口状态。
- 登录窗口：打开/清理网易云、QQ 音乐登录。
- 更新：打开下载的安装器、重启应用。
- 全局热键：配置热键、监听热键动作。
- JSON 导入导出：通过系统文件对话框读写存档。
- 桌面歌词：开启、更新、监听锁定状态。
- 壁纸模式：开启、更新壁纸状态。

页面不能直接访问 Node，只能走这些 IPC 方法。

### 桌面歌词窗口

`createDesktopLyricsWindow()` 创建一个透明、无边框、不可聚焦、跳过任务栏、置顶的窗口，加载 `desktop-lyrics.html`。它使用 `overlay-preload.js` 暴露 `window.desktopOverlay`，接收主页面推送的歌词、进度、颜色、节拍、字体、透明度等状态。

中键逻辑在这里也很重要：

- 主进程用 PowerShell 调用 Win32 `GetAsyncKeyState(4)` 轮询中键。
- 检测到 `MMB` 后，如果鼠标在桌面歌词热区内，就切换 `clickThrough`。
- 锁定状态通过 `mineradio-desktop-lyrics-lock-state` 回传主页面。

所以桌面歌词里的中键是“锁定/解锁歌词窗口交互”，不是主舞台 3D 旋转。

### 壁纸窗口

`createWallpaperWindow()` 创建铺满主屏的无边框窗口，加载 `wallpaper.html`，再通过 PowerShell/Win32 把窗口挂到 Windows 桌面 `WorkerW` 后面。窗口设置 `setIgnoreMouseEvents(true, { forward: true })`，所以它更像动态桌面背景，不抢鼠标。

## 5. 本地 HTTP 服务

`server.js` 没有使用 Express，而是直接用 `http.createServer(async (req, res) => {...})` 手写路由。它统一提供 JSON 响应、静态文件服务、cookie 持久化、代理下载和平台 API 包装。

### 路由分组

主要路由包括：

- 应用与更新：
  - `/api/app/version`
  - `/api/update/latest`
  - `/api/update/download`
  - `/api/update/download/status`
  - `/api/update/patch`
  - `/api/update/patch/status`
- beatmap 缓存：
  - `/api/beatmap/cache/status`
  - `/api/beatmap/cache`
- 发现与天气电台：
  - `/api/discover/home`
  - `/api/weather/radio`
  - `/api/weather/ip-location`
- 网易云：
  - `/api/search`
  - `/api/song/url`
  - `/api/login/qr/key`
  - `/api/login/qr/create`
  - `/api/login/qr/check`
  - `/api/login/status`
  - `/api/logout`
  - `/api/user/playlists`
  - `/api/song/like/check`
  - `/api/song/like`
  - `/api/playlist/create`
  - `/api/playlist/add-song`
  - `/api/lyric`
  - `/api/song/comments`
  - `/api/artist/detail`
  - `/api/playlist/tracks`
- QQ 音乐：
  - `/api/qq/search`
  - `/api/qq/song/url`
  - `/api/qq/lyric`
  - `/api/qq/login/status`
  - `/api/qq/login/cookie`
  - `/api/qq/logout`
  - `/api/qq/user/playlists`
  - `/api/qq/playlist/tracks`
  - `/api/qq/artist/detail`
  - `/api/qq/song/comments`
- 播客/电台：
  - `/api/podcast/search`
  - `/api/podcast/hot`
  - `/api/podcast/detail`
  - `/api/podcast/programs`
  - `/api/podcast/my`
  - `/api/podcast/my/items`
  - `/api/podcast/dj-beatmap`
- 媒体代理：
  - `/api/cover`
  - `/api/audio`

### 服务层原理

- cookie 被标准化后写入 `.cookie` 和 `.qq-cookie`，受保护 API 会带上登录 cookie。
- 网易云 API 通过 `NeteaseCloudMusicApi` 封装调用，失败时根据播放限制、VIP、试听等情况返回结构化错误。
- QQ 音乐走自定义请求与 cookie 解析，代码里区分登录态、播放态和音质上限。
- `/api/audio` 支持 `Range`，给 `<audio>` 流式播放使用。
- `/api/cover` 添加 CORS 和缓存头，方便前端 Canvas/Three 提取封面颜色或作为纹理。
- 更新模块支持 GitHub release、镜像下载、校验摘要、补丁包下载和回滚备份。
- beatmap 缓存默认放在 `D:\MineradioCache\beatmaps`，并主动避免使用 C 盘作为缓存根。

## 6. 首页前端结构

`public/index.html` 是一个超大单文件应用，结构上分成几类：

- CSS：无边框桌面窗口、启动页、搜索、首页卡片、播放控制条、FX 面板、歌单面板、歌词面板、更新弹窗等。
- HTML：主画布容器、搜索区、首页推荐、播放器控制条、歌词/FX/歌单/更新等面板。
- JS 状态层：播放器状态、登录状态、歌单队列、视觉预设、性能设置、热键设置、天气电台状态、桌面歌词/壁纸同步状态。
- JS 渲染层：Three.js 场景、粒子/封面/骷髅架/3D 歌单架、相机系统、WebAudio 分析、歌词粒子、桌面 overlay 同步。

### API 调用

前端用 `apiJson(url, opts)` 包装 `fetch`，支持超时中断。业务函数围绕这些 API 实现搜索、登录、播放、歌词、歌单、播客、更新等流程。

### 音频分析

`initAudio()` 创建：

- `AudioContext`
- `MediaElementSource`
- 主 `AnalyserNode`
- 节拍专用 `beatAnalyser`
- `GainNode`

主循环里每帧调用：

- `analyser.getByteFrequencyData(frequencyData)`
- `analyser.getByteTimeDomainData(timeDomainData)`

然后把频段拆成 kick、人声、中高频乐器、高频，并做动态峰值跟踪。它没有把所有中频都当成鼓点，而是主动排除了人声主频段，避免歌词/人声导致镜头误触发。

### Three.js 场景

首页直接创建：

- `new THREE.Scene()`
- `new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 100)`
- `new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: 'high-performance' })`

渲染器挂到 `#canvas-container`。渲染像素比不是固定设备 DPR，而是由 `renderQualityProfile()` 和像素预算动态计算，防止大屏/高分屏把 GPU 压满。

## 7. 3D 视角与中键旋转

首页相机系统分两层：

- `orbit.userTheta/userPhi/userRadius`：用户拖拽和滚轮产生的永久视角偏移。
- `orbit.cineTheta/cinePhi/cineRadius`：音乐/节拍驱动的电影镜头微偏移。

最终相机使用两者叠加：

```text
theta = userTheta + cineTheta
phi = userPhi + cinePhi
radius = userRadius + cineRadius
```

鼠标/中键旋转的关键路径是：

1. `beginParticlePointerDrag(e)` 只排除右键 `e.button === 2`，没有限制必须左键。
2. 因此左键和中键都能进入 `orbit.rotating = true`。
3. `mousemove` 中如果 `orbit.rotating` 为真，就计算 `dx/dy`。
4. 位移用于粒子惯性拖动，也会更新用户相机偏移。
5. `updateCamera()` 每帧把 `orbit` 转成相机位置并 `camera.lookAt(orbit.lookAt)`。

所以用户之前提到“中键是旋转 3D 视角”是成立的：在主首页画布里，中键拖拽走的就是普通拖拽旋转路径。

另一个容易混淆的点是自由镜头：

- `R` 调用 `toggleFreeCamera()`。
- 进入后使用 Pointer Lock。
- `W/A/S/D` 移动，鼠标转向，`Q/E` roll，滚轮调 FOV。
- `K` 或双击可回正。

自由镜头是更完整的第一人称相机控制，不等同于中键拖拽 orbit。

## 8. 动效主循环

`animate()` 每帧执行：

1. 自适应跳帧和性能采样。
2. 更新时间 uniform。
3. 启动页遮挡时仅低频预热渲染。
4. 读取 WebAudio 频谱和波形。
5. 计算 bass/mid/treble/energy/beatPulse。
6. 使用实时 beat 引擎或预解析 beatmap 触发镜头节拍。
7. 更新 shader uniforms：`uBass`、`uMid`、`uTreble`、`uBeat`、`uEnergy`、鼠标位置等。
8. 更新预设切换、涟漪、浮层、歌单架、歌词粒子、首页音频可视化。
9. 更新电影镜头、自由镜头、orbit 相机和骷髅相机姿态。
10. 同步桌面歌词/壁纸状态。
11. `renderer.render(scene, camera)`。

这套动效不是单个“首页 CSS 动画”，而是“音频分析 -> 状态平滑 -> shader/Three uniforms -> 相机运动 -> 渲染”的闭环。

## 9. 桌面歌词与壁纸页面

### desktop-lyrics.html

桌面歌词页有自己的渲染循环和状态机。它接收主页面推送的：

- 当前歌词文本和逐字进度。
- 播放时间、播放状态和播放速率。
- beatmap、beatGlow、bass、highBloom 等运动参数。
- 颜色、字体、字号、行高、透明度。
- 锁定/解锁和点击穿透状态。

它使用 CSS 渐变文本表现歌词进度，用 Canvas 叠加光晕、粒子、节拍发光。窗口层面由 Electron 控制透明、置顶和点击穿透。

### wallpaper.html

壁纸页更轻：只有一个 Canvas。它接收歌曲标题、歌手、封面、播放状态、预设和颜色，绘制动态背景、封面虚化层、环绕粒子和径向光晕。

## 10. beatmap 与 DJ 分析

`dj-analyzer.js` 用低频能量、hit energy、hop size、节拍相位等信息构建 beatmap。`server.js` 的 `/api/podcast/dj-beatmap` 会根据音频 URL 和时长调用：

- `analyzePodcastDjIntro`
- `analyzePodcastDjStream`

主页面优先使用预解析 beatmap 驱动镜头；如果 beatmap 尚未准备好，就用实时频谱引擎做补位。

## 11. 可迁移到媒体控制中心的部分

媒体控制中心一期边界是 Docker 中控，管理 Emby、qBittorrent、Torra、Symedia，不做播放器、刮削器或 PT 爬虫。因此适合迁移的是结构和交互方法，不是音乐业务本身。

可迁移：

- Electron/本地服务分层思路：桌面壳只负责系统能力，业务 API 放到本地服务。
- preload 白名单桥：前端只能调用受控方法，避免直接暴露 Node。
- 本地服务适配器模式：把 Emby、qBittorrent、Torra、Symedia 都包成稳定的 `/api/*` 聚合接口。
- 主页面 3D 舞台结构：`Three.Scene + PerspectiveCamera + WebGLRenderer + orbit/cinema offset` 可用于影院大厅。
- 中键/拖拽旋转视角：把拖拽输入映射到 `userTheta/userPhi`，电影镜头作为额外偏移叠加。
- 性能预算：根据窗口像素数限制 DPR，后台/遮挡时降频或休眠。
- overlay 同步模型：如果未来做桌面悬浮状态条，可复用“主页面生成 payload -> Electron overlay 窗口消费”的思路。
- CORS 媒体代理：Emby 封面/背景图需要 Canvas 取色或纹理时，可以用本地代理加统一缓存头。

## 12. 不应直接复制的部分

不建议迁移：

- 网易云/QQ 音乐登录、歌曲 URL、歌词、评论、歌单收藏等音乐平台业务。
- `server.js` 的巨型手写路由文件。媒体控制中心已经是 Express + TypeScript，更适合保留分层路由和服务适配器。
- 大量单文件 HTML/JS 写法。媒体控制中心应该保留 React/Vite 模块化结构。
- 播放器专用 WebAudio 频谱、beatmap、歌词光效，除非未来明确做媒体播放可视化。
- Win32 WorkerW 动态壁纸挂载，一期 Docker 中控不需要。
- 自动更新安装器和补丁系统，除非后续真的做桌面发行。

## 13. 对媒体控制中心的实现建议

1. 首页 3D 大厅只借鉴 Mineradio 的相机和舞台原理，不搬音乐业务。
2. 用现有 `shared.css` 或项目设计 token 保持所有页面风格统一。
3. 把中键/拖拽旋转统一成一个交互契约：
   - 左键/中键拖拽大厅舞台：旋转 3D 视角。
   - 滚轮：控制景深、推进或缩放。
   - 双击空白：回正。
   - 禁止在按钮、导航、卡片可点击区域触发舞台旋转。
4. 后端继续走 Express 路由：
   - `/api/emby/*`
   - `/api/qbittorrent/*`
   - `/api/torra/*`
   - `/api/symedia/*`
   - `/api/health`
5. 需要 Canvas 取色时，新增一个受限媒体代理，只允许代理已配置的 Emby 域名，不做任意 URL 代理。
6. 性能控制要提前写进大厅模块：DPR 上限、后台暂停、窗口不可见时停止非必要动画。

## 14. 源码证据索引

- `D:\Mineradio\resources\app\package.json`：应用元数据、入口和依赖。
- `D:\Mineradio\resources\app\desktop\main.js`：
  - 动态端口、本地服务启动、主窗口加载：`createWindow()`
  - IPC handlers：`ipcMain.handle(...)`
  - 桌面歌词窗口：`createDesktopLyricsWindow()`
  - 壁纸窗口：`createWallpaperWindow()`
  - 中键锁定歌词：`handleDesktopLyricsGlobalMiddleClick()`
- `D:\Mineradio\resources\app\desktop\preload.js`：`window.desktopWindow` IPC 白名单。
- `D:\Mineradio\resources\app\desktop\overlay-preload.js`：`window.desktopOverlay` IPC 白名单。
- `D:\Mineradio\resources\app\server.js`：
  - HTTP 服务：`http.createServer(...)`
  - JSON/静态服务：`sendJSON()`、`serveStatic()`
  - 网易云/QQ/播客/天气/更新/代理路由。
- `D:\Mineradio\resources\app\public\index.html`：
  - Three 初始化：`THREE.Scene`、`PerspectiveCamera`、`WebGLRenderer`
  - 相机系统：`orbit`、`freeCamera`
  - 中键/拖拽旋转：`beginParticlePointerDrag()`、`mousemove`、`updateCamera()`
  - 主循环：`animate()`
  - 桌面 overlay 同步：`pushDesktopLyricsState()`、`pushWallpaperState()`
- `D:\Mineradio\resources\app\public\desktop-lyrics.html`：桌面歌词渲染。
- `D:\Mineradio\resources\app\public\wallpaper.html`：动态壁纸渲染。
- `D:\Mineradio\resources\app\dj-analyzer.js`：DJ/播客 beatmap 分析。
