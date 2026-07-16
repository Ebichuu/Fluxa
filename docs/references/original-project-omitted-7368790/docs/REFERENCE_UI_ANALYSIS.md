# 参考项目 UI 分析记录

记录时间：2026-07-04  
分析范围：仅 UI、视觉资源、交互方式、布局结构和可借鉴设计。不展开安装检测、下载资源、主题文件改写等非 UI 逻辑。

## 参考项目概览

| 项目 | 类型 | UI 技术 | 核心价值 |
| --- | --- | --- | --- |
| `参考/DKG-Theme-Modifier-2.8.3` | Playnite 插件 | C# + WPF/XAML | 提供主题设置面板和少量可嵌入主题的自定义控件，重点是“配置 UI”和“主机风格按钮控件”。 |
| `参考/PS5reborn_saVantCZ` | Playnite 全屏主题 | WPF ResourceDictionary + XAML 模板 + 资源素材 | 完整 PS5 风格全屏 UI：启动页、游戏横向列表、详情页、控制中心、商店/Plus/欢迎面板、菜单、音视频氛围。 |

## DKG-Theme-Modifier UI 分析

### UI 结构

- 设置入口位于 `source/DKGThemeModifierSettingsView.xaml`，整体是一个大型 `TabControl`。
- 一级 Tab 按主题分组，例如 General、Elegance、Switch、PS5ish、XBOXSERIESish 等。
- 每个主题内部再用子 `TabControl`、`ScrollViewer` 和 `StackPanel` 组织选项。
- 主题是否显示依赖 `DataTrigger` 绑定，例如 `Settings.IsThemeInstalled_*` 为 true 时显示对应面板。
- 设置控件主要是 `CheckBox`、`RadioButton`、`TextBox`、`Slider`、`Button`。

### 视觉特征

- 设置页本身偏工具型，视觉重点不强，基本是 WPF 默认控件加少量自定义 RadioButton 色块。
- 色彩选择器使用 30x30 的 RadioButton 色块，选中/悬停时显示边框，这个交互可以借鉴为“主题色选择器”。
- 多处使用固定宽度文本说明，例如 `TextBlock Width="200"` 搭配右侧控件，形成简单表单布局。
- 按钮文案以 `Apply Changes`、`Restore Defaults`、`Download Icons`、`Open Folder` 为主，偏维护工具，不适合作为前台用户界面直接照搬。

### 自定义主题控件

- `Controls/PS5ish_StoreButton.xaml` 是最值得看的 UI 控件。
- 它定义了一个 105x105 的商店按钮，焦点或鼠标悬停时放大到 1.6 倍。
- 焦点态包含两层效果：旋转渐变描边 `AnimatedCoverBrush`，以及扫光 `AnimatedFlashCover`。
- 控件通过 `Trigger Property="IsFocused"` 和 `Trigger Property="IsMouseOver"` 同时支持手柄/键盘焦点与鼠标悬停。
- 这个模式适合当前项目里的“电视端/遥控器友好入口按钮”：默认简洁，聚焦后明显放大、描边、扫光。

### 可借鉴点

- 配置项可以按视觉模块拆分：主题色、背景、封面、预告片、侧边栏、标签文本。
- 色块 RadioButton 可以用于“主题色/氛围色”设置。
- 大屏 UI 的焦点态应该比鼠标网页更夸张：放大、描边、明暗切换都可以同时出现。
- 设置页不要照搬它的大型单文件结构；当前项目更适合拆成数据驱动的设置分组。

### 不建议照搬

- `DKGThemeModifierSettingsView.xaml` 超过 1300 行，结构重复较多，维护成本高。
- UI 与具体主题路径、主题安装状态绑定过深，不适合我们的 Web/React 项目。
- 很多布局使用固定宽度，适配性弱。

## PS5reborn UI 分析

### 项目 UI 组成

| 区域 | 代表文件 | 说明 |
| --- | --- | --- |
| 主界面 | `Views/Main.xaml` | 横向游戏列表、顶部导航、登录/欢迎/商店/Plus/控制中心等主屏逻辑。 |
| 游戏详情 | `Views/GameDetails.xaml` | 背景、游戏封面、标题、截图、媒体行、预告片、奖杯/信息卡片。 |
| 游戏运行状态 | `Views/GameStatus.xaml` | 类 PS5 的游戏启动/运行遮罩，背景图缓慢缩放，底部控制按钮。 |
| 游戏卡片 | `DerivedStyles/ListGameItemStyle.xaml` | 横向封面列表项、焦点描边、封面覆盖层、来源图标、标题显示。 |
| 默认控件 | `DefaultControls/*.xaml` | Button、CheckBox、ComboBox、Slider、ScrollViewer 等基础控件的 PS5 化重写。 |
| 资源常量 | `Constants.xaml` | 开关、文案、颜色、笔刷、基础字体、PS5 边框/扫光样式。 |
| 配置项 | `options.yaml` | 启动视频、登录页、封面、详情媒体、控制中心、商店/Plus/欢迎面板等 UI 开关。 |

### 视觉语言

- 整体是深色主机 UI：黑底、深蓝/深灰渐变、白色文字、半透明遮罩。
- 大量使用真实图片/视频资源做沉浸背景，而不是纯 CSS/纯形状装饰。
- 字体主要是 `Segoe UI Light`，标题较轻、较大，形成接近主机系统的冷静感。
- 焦点态是视觉核心：旋转渐变描边、扫光、放大、透明度切换、图标白/深色反转。
- 页面信息层级偏“电视端远距阅读”：字号大、留白大、控件间距大、横向导航强。

### 主界面布局

- `Views/Main.xaml` 是一个完整 `ControlTemplate`，以 `Grid` 叠层承载所有画面。
- 背景层包含默认 PS5 背景、游戏背景图、覆盖遮罩、视频/微预告片相关层。
- 主内容以横向游戏封面列表为中心，额外插入 Store、Plus、Explore、More Games 等伪系统入口。
- 使用隐藏的 `CheckBoxEx`、`ContentControl` 和绑定代理管理状态，让 XAML 本身承担大量界面状态机职责。
- 支持手柄输入，例如 Guide 键打开控制中心，键盘 `C` 也能打开菜单。

### 游戏卡片与焦点态

- `DerivedStyles/ListGameItemStyle.xaml` 定义了列表项的核心观感。
- 默认只显示一定数量的列表项，数量由 `ItemCount` 控制。
- 卡片上叠加封面遮罩、来源图标、安装/隐藏状态、标题。
- 聚焦时显示 `PS5Border` 和 `PS5Cover` 两层效果：
  - `PS5Border`：旋转的渐变描边。
  - `PS5Cover`：从左上到右下的扫光动画。
- 这是当前项目最值得吸收的交互语言：媒体海报/卡片在遥控器焦点下应有明确“被选中”的物理感。

### 游戏详情页

- `Views/GameDetails.xaml` 使用大背景 + 顶部标题 + 左侧封面 + 详情/媒体区域的布局。
- 支持截图列表、截图全屏预览、预告片播放、微视频背景等沉浸媒体能力。
- 详情页仍然大量依赖动画：切入时位置移动、透明度渐显、截图焦点进入时黑色遮罩出现。
- 适合给当前项目的“影片/剧集详情页”做参考：背景图铺满、内容层半透明压暗、媒体截图/预告片作为二级行。

### 控制中心与系统菜单

- `Views/GameStatus.xaml` 和 `DefaultControls/Button.xaml` 体现了控制中心按钮的样式。
- 圆形图标按钮默认透明，聚焦后出现白色圆底、图标反色和旋转描边。
- 底部文字会根据当前聚焦按钮动态变化，适合电视端“图标 + 当前说明”的交互。
- `Views/MainMenu.xaml` 中菜单项宽 1300、高 80，左侧图标、右侧大字号文本，加载后淡入。

### 设置与可配置项

`options.yaml` 把 UI 开关拆得很细，值得直接参考它的分组方式：

- 启动体验：是否播放 PS5 intro、是否使用 30 周年视频、是否 4K、音量。
- 登录体验：按键进入、档案选择、欢迎文本、用户名。
- 封面列表：最大可见游戏数量、封面动画、方形大图标、来源图标、已安装标签。
- 自定义文案：媒体筛选名称、设置页标题、播放按钮后缀、媒体启动按钮文本。
- 详情媒体：截图、全屏截图、截图可视化、微描述、微视频背景、预告片延迟。
- 控制中心：隐藏各类电源按钮、隐藏无用按钮。
- 商店/Plus/欢迎面板：是否显示、是否可聚焦、各卡片文案。

### 资源规模

| 类型 | 数量/体积 | 说明 |
| --- | --- | --- |
| 图片 | 约 291 张 PNG/JPG | 背景、按钮、平台图标、奖杯、评级、商店/欢迎面板素材。 |
| 视频 | 6 个 MP4，约 127.6 MB | 启动视频、粒子循环、欢迎面板背景视频。 |
| 音频 | 6 个 MP3/WAV，约 4.7 MB | 背景音乐、导航音、确认音、通知音。 |

关键素材尺寸：

| 素材 | 尺寸 | 作用 |
| --- | --- | --- |
| `Images/PS5/Background/PS5Background.png` | 1920x1080 | 主背景。 |
| `Images/PS5/Background/PS5Games.png` | 1920x1080 | 游戏主页背景。 |
| `Images/PS5/Background/PS5Overlay.png` | 3840x2160 | 全屏暗化/氛围遮罩。 |
| `Images/PS5/ButtonsIcon/PS5Store.png` | 200x200 | 商店入口图标。 |
| `Images/PS5/Logo/PS5Logo.png` | 180x180 | PS5 标识。 |

### 可借鉴到“媒体控制中心”的 UI 方案

1. 首页可以采用“主机媒体大厅”结构：全屏背景图/视频 + 横向海报列表 + 顶部状态栏 + 底部焦点说明。
2. 海报卡片聚焦态建议使用“放大 + 亮描边 + 轻扫光”，比普通 hover 更适合遥控器/手柄。
3. 媒体详情页可参考 PS5reborn：背景图铺满，前景展示标题、来源、播放按钮、预告片/截图行、元数据卡片。
4. 控制中心可以做成底部浮层：播放控制、音量、投屏/下载/刷新、关机/退出等图标按钮横排。
5. 设置页分组可以借鉴 `options.yaml` 的模块化，但在实现上应改为数据配置驱动，避免超长模板。
6. 音效可作为可选增强：导航、确认、通知三类即可，不需要一开始引入大体积背景音乐。

### 需要规避的风险

- PS5reborn 大量硬编码 1920x1080、固定 Margin、固定 Width/Height；Web 项目需要响应式重写。
- XAML 中触发器和状态机高度耦合，不能直接迁移到 React/Vue，应抽象为组件状态。
- 大量动画和视频会带来性能压力，当前项目应先实现焦点态、遮罩、背景淡入，视频背景可延后。
- 资源命名与 PlayStation 品牌强绑定；如果做公开项目，应避免直接使用 PS5/PlayStation 商标和素材。
- 菜单和设置界面的单文件模板过长，维护体验不好；当前项目应拆成小组件。

## 推荐落地优先级

1. 先做海报/媒体卡片焦点态：放大、描边、扫光、标题浮现。
2. 再做详情页沉浸背景：背景图 + 暗化遮罩 + 主要操作按钮。
3. 然后做底部控制中心：图标按钮 + 焦点说明文字。
4. 最后再考虑启动动画、背景音乐、粒子/视频循环等氛围项。

## 文件关注清单

- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\Views\Main.xaml`
- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\Views\GameDetails.xaml`
- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\DerivedStyles\ListGameItemStyle.xaml`
- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\DefaultControls\Button.xaml`
- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\Views\GameStatus.xaml`
- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\Constants.xaml`
- `D:\Projects\媒体控制中心\参考\PS5reborn_saVantCZ\options.yaml`
- `D:\Projects\媒体控制中心\参考\DKG-Theme-Modifier-2.8.3\source\DKGThemeModifierSettingsView.xaml`
- `D:\Projects\媒体控制中心\参考\DKG-Theme-Modifier-2.8.3\source\Controls\PS5ish_StoreButton.xaml`
- `D:\Projects\媒体控制中心\参考\DKG-Theme-Modifier-2.8.3\source\Controls\PlayniteModernUI_Options.xaml`
