# NasEmby 原静态页面源码快照

来源：`D:\Projects\媒体控制中心` 提交 `7368790`  
用途：保留 NasEmby 原页面的接口参数、调用顺序、交互和视觉实现，作为合并进 React 媒体控制中心时的依据。

该目录不参与生产运行：

- 项目根 `.dockerignore` 排除了 `docs/references`。
- Flask 不注册这里的 `/static/app.js` 或 `templates/index.html`。
- 用户仍只使用媒体控制中心 React 页面。
- 未完成逐项迁移和测试前，不得删除本快照或对应 Python 核心接口。

## 文件清单

| 文件 | 大小 | SHA-256 |
| --- | ---: | --- |
| `services/nasemby-core/app/static/app.js` | 240252 | `5e1a853aecccc06523aa7314d42b770064c4f66b22475610715101e81c3b38c4` |
| `services/nasemby-core/app/static/styles.css` | 215017 | `0448c48cdb1cd77373083eb964ab99263042c22adb2d0be04b7ff91c1a628ee3` |
| `services/nasemby-core/app/templates/index.html` | 56703 | `ed903a30ba5da4ff69b52e5be26b4dfefbf41cbc99f70f3e7344999b80002f4a` |
| `services/nasemby-core/app/static/nasemby-logo.png` | 223460 | `de619bde0df33275ef363f1ee440fbbb44b8c02a0da9a09afeeb18bf5caf55dd` |
| `services/nasemby-core/app/static/nasemby-logo-icon.png` | 78647 | `ee7e1e5f2286268dc55b8b156219a67cd4668b452bbfd21930a62529561bfa97` |
| `services/nasemby-core/app/static/nasemby-logo-mark.png` | 62131 | `98b4cfabdf869f3486e3835098661c7c795743537fa070b2edcca762dcb3e7a5` |
| `services/nasemby-core/app/static/nasemby-logo-sidebar.png` | 145361 | `a993e3790d13e5c4e213201fbbfb0ebbb3929f8623a94c9055642d7b97bf3b85` |
