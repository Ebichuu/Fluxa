# Mineradio 快速移植方案（内嵌法）

**日期**: 2026-07-07  
**策略**: 直接内嵌 Mineradio 核心代码，通过 React 包装层集成

---

## 一、方案对比

### 原方案：逐模块移植
- ⏱️ 时间：40-52 小时
- 💰 成本：$126-163
- 🎯 优点：代码结构清晰，TypeScript 类型安全
- ❌ 缺点：耗时长，成本高

### 新方案：直接内嵌（推荐）
- ⏱️ 时间：2-3 小时
- 💰 成本：$15-25
- 🎯 优点：快速、低成本、保真度 100%
- ⚠️ 缺点：代码可维护性稍差，但功能完整

**成本对比**：新方案节省 **$100-140** 和 **38-50 小时**

---

## 二、技术架构

### 核心思路
```
React Component (MineradioEmbed.tsx)
    ↓
内嵌 <script> 执行 Mineradio 原生 JS
    ↓
通过 window.MineradioAPI 暴露接口
    ↓
React props → 注入数据 → Mineradio 舞台
```

### 数据流
```
用户数据 (items/libraries)
    ↓
React props
    ↓
window.MineradioAPI.updateData()
    ↓
Mineradio 原生代码
    ↓
Three.js 渲染
```

---

## 三、详细实施步骤

### Step 1: 提取 Mineradio 核心代码（30 分钟）

从 `mineradio-source.html` 提取以下部分：

#### 1.1 提取 CSS（约 2000 行）
**位置**: `<style>` 标签内容（约 line 19-2100）

**需要保留**:
- Three.js 相关样式
- 库卡片样式
- 控制面板样式
- 交互样式

**需要删除**:
- 音乐播放器 UI 样式
- 播放控制按钮样式
- 进度条样式
- 歌词面板样式
- 用户系统样式

**关键选择器**（保留）:
```css
#canvas-container
#custom-bg
#album-bg
.library-shelf-*
.poster-rail
.visual-*
.glass-*
```

**关键选择器**（删除）:
```css
#player-*
#playlist-*
#lyric-*
#progress-*
.user-*
.search-*
```

---

#### 1.2 提取 HTML 结构（约 100 行）
**位置**: `<body>` 内容（约 line 2100-2200）

**需要保留**:
```html
<div id="canvas-container"></div>
<div id="custom-bg"></div>
<div id="album-bg"></div>
<!-- 库卡片相关 DOM -->
<!-- 控制面板相关 DOM -->
```

**需要删除**:
```html
<div id="player-controls"></div>
<div id="playlist"></div>
<div id="lyric-panel"></div>
<div id="search-area"></div>
<div id="user-panel"></div>
```

---

#### 1.3 提取 JavaScript 核心（约 20,000 行）
**位置**: `<script>` 标签内容（约 line 2200-26879）

**需要保留的模块**:

##### A. Three.js 初始化（line 3717-3783）
```javascript
// 场景、相机、渲染器
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(...);
const renderer = new THREE.WebGLRenderer(...);
```

##### B. Shader 系统（line 5858-6800）
```javascript
// vertex shader
const vertexShader = `...`;

// fragment shader
const fragmentShader = `...`;

// 5 种预设配置
const presets = {
  SILK: {...},
  TUNNEL: {...},
  ORBIT: {...},
  VOID: {...},
  VINYL: {...}
};
```

##### C. 粒子系统（line 12100-12750）
```javascript
// 粒子几何体
const particleGeometry = new THREE.BufferGeometry();
const particleCount = 50000;
const positions = new Float32Array(particleCount * 3);
// ...
```

##### D. 库卡片系统（line 12756-14500）
```javascript
// 库卡片几何体
function createLibraryCard(library) {
  const geometry = new THREE.PlaneGeometry(2.05, 1.025);
  const material = new THREE.ShaderMaterial({...});
  // ...
}
```

##### E. 封面纹理系统（line 15200-16800）
```javascript
// 封面加载
function loadCoverTexture(url) {
  const loader = new THREE.TextureLoader();
  return loader.load(url, (texture) => {
    // 边缘检测、深度计算
  });
}
```

##### F. 涟漪系统（line 17500-18300）
```javascript
// DataTexture 涟漪
const rippleTexture = new THREE.DataTexture(...);
function updateRipples(deltaTime) {
  // 传播算法
}
```

##### G. 浮空粒子层（line 18800-19400）
```javascript
// 额外的粒子层
const floatingParticles = new THREE.Points(...);
```

##### H. 交互系统（line 21200-23200）
```javascript
// 鼠标移动
canvas.addEventListener('mousemove', (e) => {...});

// 滚轮缩放
canvas.addEventListener('wheel', (e) => {...});

// Raycaster 碰撞检测
const raycaster = new THREE.Raycaster();
function checkHover() {...}
```

##### I. 动画循环（line 24000-24500）
```javascript
function animate() {
  requestAnimationFrame(animate);
  
  // 更新 uniforms
  material.uniforms.uTime.value = clock.getElapsedTime();
  
  // 更新粒子
  updateParticles();
  
  // 更新涟漪
  updateRipples();
  
  // 渲染
  renderer.render(scene, camera);
}
```

**需要删除的模块**:
- 音频播放器逻辑（line 8000-10000）
- 音频分析（FFT/Tempo）（line 10000-11000）
- 播放列表管理（line 11000-12000）
- 用户系统逻辑（line 24500-25500）
- 搜索功能（line 25500-26000）

---

### Step 2: 创建 MineradioEmbed.tsx（45 分钟）

```typescript
/**
 * MineradioEmbed - 内嵌 Mineradio 核心代码
 * 
 * 策略：
 * 1. 直接执行 Mineradio 原生 JS
 * 2. 通过 window.MineradioAPI 暴露接口
 * 3. React props → 原生代码数据注入
 */

import { useEffect, useRef } from 'react';
import type { MediaItem, MediaLibrary } from '../../types/media';
import type { VisualFxSettings } from '../../types/visualFx';

interface MineradioEmbedProps {
  activeItem: MediaItem | null;
  activeLibraryId?: string;
  items: MediaItem[];
  libraries: MediaLibrary[];
  resetSignal: number;
  visualFx: VisualFxSettings;
  onCoverError?: (itemId: string) => void;
  onSelectLibrary?: (libraryId: string) => void;
  onSelectItem?: (index: number) => void;
}

// 全局 API 接口定义
interface MineradioAPI {
  updateData: (data: {
    items: MediaItem[];
    libraries: MediaLibrary[];
    activeItem: MediaItem | null;
    activeLibraryId?: string;
  }) => void;
  updateVisualFx: (fx: VisualFxSettings) => void;
  reset: () => void;
  destroy: () => void;
}

declare global {
  interface Window {
    MineradioAPI?: MineradioAPI;
  }
}

export function MineradioEmbed(props: MineradioEmbedProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const initializedRef = useRef(false);

  // 初始化 Mineradio 核心代码
  useEffect(() => {
    if (!containerRef.current || initializedRef.current) return;

    // 注入样式
    const styleEl = document.createElement('style');
    styleEl.textContent = MINERADIO_CSS; // 从 Step 1.1 提取的 CSS
    document.head.appendChild(styleEl);

    // 创建 DOM 结构
    containerRef.current.innerHTML = MINERADIO_HTML; // 从 Step 1.2 提取的 HTML

    // 执行 Mineradio 核心 JS
    const scriptEl = document.createElement('script');
    scriptEl.textContent = MINERADIO_JS; // 从 Step 1.3 提取的 JS
    document.body.appendChild(scriptEl);

    initializedRef.current = true;

    // 清理
    return () => {
      if (window.MineradioAPI) {
        window.MineradioAPI.destroy();
      }
      document.head.removeChild(styleEl);
      document.body.removeChild(scriptEl);
    };
  }, []);

  // 同步 React 数据到 Mineradio
  useEffect(() => {
    if (!window.MineradioAPI) return;

    window.MineradioAPI.updateData({
      items: props.items,
      libraries: props.libraries,
      activeItem: props.activeItem,
      activeLibraryId: props.activeLibraryId
    });
  }, [props.items, props.libraries, props.activeItem, props.activeLibraryId]);

  // 同步视觉效果参数
  useEffect(() => {
    if (!window.MineradioAPI) return;

    window.MineradioAPI.updateVisualFx(props.visualFx);
  }, [props.visualFx]);

  // 响应重置信号
  useEffect(() => {
    if (!window.MineradioAPI || props.resetSignal === 0) return;

    window.MineradioAPI.reset();
  }, [props.resetSignal]);

  return (
    <div
      ref={containerRef}
      className="mineradio-embed"
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 3
      }}
    />
  );
}

// ========== 从 Step 1 提取的代码 ==========

const MINERADIO_CSS = `
  /* 从 mineradio-source.html 提取的 CSS */
  /* 约 2000 行，已删除音乐播放器相关样式 */
`;

const MINERADIO_HTML = `
  <!-- 从 mineradio-source.html 提取的 HTML -->
  <!-- 约 100 行，已删除音乐播放器 DOM -->
`;

const MINERADIO_JS = `
  /* 从 mineradio-source.html 提取的 JS */
  /* 约 20,000 行，已删除音乐播放器逻辑 */
  
  // 在最后暴露全局 API
  window.MineradioAPI = {
    updateData: function(data) {
      // 更新 items/libraries 到 Mineradio 内部
      // 触发库卡片重新渲染
    },
    
    updateVisualFx: function(fx) {
      // 更新 shader uniforms
      // 切换视觉预设
    },
    
    reset: function() {
      // 重置相机位置
      // 清除所有动画
    },
    
    destroy: function() {
      // 清理 Three.js 资源
      // 移除事件监听器
    }
  };
`;
```

---

### Step 3: 提取和清理代码（60 分钟）

#### 3.1 CSS 提取脚本

创建辅助脚本识别和分离 CSS：

```javascript
// extract-css.js
const fs = require('fs');

const html = fs.readFileSync('mineradio-source.html', 'utf8');

// 提取 <style> 标签内容
const cssMatch = html.match(/<style>([\s\S]*?)<\/style>/);
const fullCSS = cssMatch[1];

// 音乐播放器相关选择器（需要删除）
const playerSelectors = [
  '#player-', '#playlist-', '#lyric-', '#progress-',
  '.user-', '.search-', '.login-', '.favorite-'
];

// 过滤 CSS
const lines = fullCSS.split('\n');
const filtered = [];
let skip = false;

for (const line of lines) {
  // 检查是否是播放器相关选择器
  if (playerSelectors.some(sel => line.includes(sel))) {
    skip = true;
  }
  
  // 检查块结束
  if (line.includes('}')) {
    if (!skip) filtered.push(line);
    skip = false;
    continue;
  }
  
  if (!skip) filtered.push(line);
}

const cleanCSS = filtered.join('\n');

fs.writeFileSync('mineradio-clean.css', cleanCSS);
console.log(`Original: ${fullCSS.length} chars`);
console.log(`Cleaned: ${cleanCSS.length} chars`);
console.log(`Removed: ${fullCSS.length - cleanCSS.length} chars`);
```

#### 3.2 JavaScript 提取脚本

```javascript
// extract-js.js
const fs = require('fs');

const html = fs.readFileSync('mineradio-source.html', 'utf8');

// 提取 <script> 标签内容
const jsMatch = html.match(/<script[^>]*>([\s\S]*?)<\/script>/g);
const scripts = jsMatch.map(s => s.replace(/<\/?script[^>]*>/g, ''));

// 合并所有脚本
const fullJS = scripts.join('\n\n');

// 识别需要删除的函数和变量
const toRemove = [
  // 音频相关
  'playAudio', 'pauseAudio', 'stopAudio', 'seekAudio',
  'audioContext', 'audioAnalyser', 'audioSource',
  
  // 播放列表
  'updatePlaylist', 'addToPlaylist', 'removeFromPlaylist',
  
  // 用户系统
  'login', 'logout', 'register', 'getUserInfo',
  
  // 搜索
  'searchMusic', 'searchOnline', 'fetchSearchResults'
];

// 简单的函数删除（需要手动验证）
let cleanJS = fullJS;

// 这里需要更精细的 AST 解析
// 暂时使用正则标记需要审查的部分
toRemove.forEach(name => {
  const regex = new RegExp(`(function ${name}|const ${name}|let ${name}|var ${name}).*?(?=function|const|let|var|$)`, 'gs');
  cleanJS = cleanJS.replace(regex, (match) => {
    return `/* REMOVED: ${name} */\n`;
  });
});

fs.writeFileSync('mineradio-clean.js', cleanJS);
console.log('JS extraction complete. Please manually review.');
```

#### 3.3 手动审查（重要）

运行上述脚本后，**必须手动审查**：

1. 打开 `mineradio-clean.js`
2. 搜索所有 `/* REMOVED: */` 标记
3. 确认删除的函数没有被保留的代码调用
4. 修复依赖关系
5. 测试运行

---

### Step 4: 实现 window.MineradioAPI（30 分钟）

在清理后的 JS 末尾添加：

```javascript
// ========== React 集成接口 ==========

(function() {
  // 内部状态
  let currentData = {
    items: [],
    libraries: [],
    activeItem: null,
    activeLibraryId: null
  };

  // 暴露给 React 的 API
  window.MineradioAPI = {
    /**
     * 更新数据
     */
    updateData: function(data) {
      currentData = { ...currentData, ...data };
      
      // 触发库卡片更新
      if (typeof updateLibraryShelf === 'function') {
        updateLibraryShelf(currentData.libraries);
      }
      
      // 触发当前项更新
      if (typeof setActiveItem === 'function' && currentData.activeItem) {
        setActiveItem(currentData.activeItem);
      }
      
      // 触发封面更新
      if (typeof updateCoverTexture === 'function' && currentData.activeItem) {
        updateCoverTexture(currentData.activeItem.cover);
      }
    },

    /**
     * 更新视觉效果
     */
    updateVisualFx: function(fx) {
      // 切换预设
      if (fx.preset && typeof switchPreset === 'function') {
        switchPreset(fx.preset);
      }
      
      // 更新 shader uniforms
      if (particleMaterial && particleMaterial.uniforms) {
        particleMaterial.uniforms.uIntensity.value = fx.intensity || 1.0;
        particleMaterial.uniforms.uDepth.value = fx.depth || 1.0;
        // ... 其他参数
      }
    },

    /**
     * 重置舞台
     */
    reset: function() {
      // 重置相机
      if (camera) {
        camera.position.set(0, 0, 10);
        camera.lookAt(0, 0, 0);
      }
      
      // 清除动画
      if (typeof clearAllTweens === 'function') {
        clearAllTweens();
      }
      
      // 重置粒子
      if (typeof resetParticles === 'function') {
        resetParticles();
      }
    },

    /**
     * 销毁资源
     */
    destroy: function() {
      // 停止动画循环
      if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
      }
      
      // 清理 Three.js
      if (renderer) {
        renderer.dispose();
      }
      
      if (scene) {
        scene.traverse((obj) => {
          if (obj.geometry) obj.geometry.dispose();
          if (obj.material) {
            if (Array.isArray(obj.material)) {
              obj.material.forEach(m => m.dispose());
            } else {
              obj.material.dispose();
            }
          }
        });
      }
      
      // 移除事件监听器
      if (canvas) {
        canvas.removeEventListener('mousemove', onMouseMove);
        canvas.removeEventListener('wheel', onWheel);
        canvas.removeEventListener('click', onClick);
      }
      
      window.removeEventListener('resize', onResize);
    }
  };
  
  // 通知 React 组件已就绪
  window.dispatchEvent(new CustomEvent('mineradio-ready'));
})();
```

---

### Step 5: 集成到 MediaHall.tsx（15 分钟）

```typescript
// src/components/media-hall/MediaHall.tsx

import { MineradioEmbed } from './MineradioEmbed';

// ... 其他导入和代码

export function MediaHall() {
  // ... 现有状态和逻辑

  return (
    <div className="media-hall">
      {/* 替换原来的 MineradioStage */}
      <MineradioEmbed
        activeItem={activeItem}
        activeLibraryId={activeLibraryId}
        items={items}
        libraries={libraries}
        resetSignal={resetSignal}
        visualFx={visualFx}
        onCoverError={handleCoverError}
        onSelectLibrary={handleSelectLibrary}
        onSelectItem={handleSelectItem}
      />
      
      {/* 其他组件保持不变 */}
      <PosterRail />
      <VisualControlPanel />
    </div>
  );
}
```

---

### Step 6: 测试和调试（30 分钟）

#### 测试清单
- [ ] Three.js 场景正常渲染
- [ ] 5 种视觉预设正常切换
- [ ] 库卡片显示正确
- [ ] 鼠标悬停交互正常
- [ ] 滚轮缩放功能正常
- [ ] 封面加载和显示正确
- [ ] 涟漪效果正常
- [ ] 浮空粒子层显示
- [ ] 性能达标（60fps）
- [ ] 无控制台错误

#### 常见问题排查

**问题 1**: `window.MineradioAPI is undefined`
- 检查 JS 是否正确注入
- 检查 `mineradio-ready` 事件是否触发

**问题 2**: 黑屏或白屏
- 检查 CSS 是否正确注入
- 检查 DOM 结构是否创建
- 检查 WebGL 上下文是否初始化

**问题 3**: 库卡片不显示
- 检查 `updateLibraryShelf` 函数是否存在
- 检查数据格式是否匹配

**问题 4**: 交互无响应
- 检查事件监听器是否绑定
- 检查 canvas 是否正确获取

---

## 四、时间和成本预估

### 详细时间表

| 步骤 | 内容 | 预估时间 |
|-----|------|---------|
| Step 1 | 提取核心代码 | 30 min |
| Step 2 | 创建 MineradioEmbed.tsx | 45 min |
| Step 3 | 清理和过滤代码 | 60 min |
| Step 4 | 实现 window.MineradioAPI | 30 min |
| Step 5 | 集成到 MediaHall | 15 min |
| Step 6 | 测试和调试 | 30 min |
| **总计** | | **3.5 小时** |

### 成本预估
- **AI 对话轮次**: 约 50-70 轮
- **预估成本**: $20-30
- **节省成本**: $106-133（vs 逐模块移植的 $126-163）

---

## 五、优缺点分析

### 优点 ✅
1. **快速**: 3.5 小时 vs 40-52 小时
2. **低成本**: $20-30 vs $126-163
3. **高保真**: 100% 还原原版效果
4. **低风险**: 不破坏原有逻辑
5. **易维护**: 有问题可直接参考原版

### 缺点 ⚠️
1. **代码可读性稍差**: 大量原生 JS 在字符串中
2. **TypeScript 覆盖不完整**: 内嵌部分无类型检查
3. **调试稍困难**: 错误信息可能不够清晰
4. **未来扩展受限**: 添加新功能需要修改内嵌代码

### 权衡建议
- **短期目标**: 使用内嵌法快速上线
- **长期优化**: 后续逐步重构为 TypeScript 模块（如有需要）

---

## 六、执行决策

### 立即执行条件
如果你：
- ✅ 需要快速看到完整效果
- ✅ 额度有限（$20-30 可接受）
- ✅ 时间有限（3.5 小时可接受）
- ✅ 接受代码可维护性稍差

→ **立即执行内嵌方案**

### 延后执行条件
如果你：
- 需要完美的 TypeScript 类型安全
- 对代码结构有严格要求
- 有充足的时间和额度
- 需要频繁修改和扩展

→ **考虑逐模块移植方案**

---

## 七、下一步行动

**请选择：**

1. **立即开始执行内嵌方案**
   - 我会立即开始 Step 1（提取核心代码）
   
2. **查看提取的代码后再决定**
   - 我先提取一部分给你预览
   
3. **调整方案**
   - 告诉我需要修改什么

---

**方案状态**: ⏸️ 等待用户确认

**推荐**: 选择 1，立即开始执行（节省 $106+ 和 38+ 小时）
