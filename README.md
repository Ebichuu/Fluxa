# Fluxa v2

面向 fnOS / NAS 家庭影音链路的轻量总控入口。

> 旧版本 v1 已完整归档于 `archive/v1` 分支及 `v1-archive` 标签。v2 彻底推倒重写，聚焦真实日常排障与可控操作。

## 定位与原则

Fluxa **不替代**你现有的 **MoviePilot Neo**、**Torra**、**qBittorrent**、**Symedia (SA)** 或 **Emby**，也不私自改写 SA 规则或触碰 qB 保种文件。它只解决三件事：

1. **看清一部片到底卡在哪**：点开任意电影/剧集，一屏查看 5 步真实链路（`订阅与选种 → qB 下载与保种 → 115 上传 → SA 整理/STRM → Emby 条目`）。拿不到上游直接证据就标 **未确认**；区分 SA 识别失败与 SA 权重规则正常拒绝；区分 Emby 已有条目与实际播放。
2. **订阅与选种可控、可解释**：
   - 聚合 MoviePilot Neo 与 Torra 订阅，明确标记重复订阅与 `追更 / 收藏` 意图；
   - 榜单发现严格区分 **新剧 / 老剧新一季（仅订最新季）/ 纯老片或已完结老剧**；
   - 本站官组按 **`(来源站 + 该站官组)`** 判定，精准识别并拦截跨站转载组。
3. **异常能处理，操作有边界**：
   - 首页即 **追更日历 + 进度面板 + 异常待办**，桌面与手机双端自适应；
   - 仅提供针对单个任务的定向重试或直达原软件按钮，不做黑盒“一键修复”；
   - 涉及停止做种等高风险动作，明确列出种子名、保种状态与路径后二次确认。

## 快速部署（Docker Compose / fnOS）

```bash
cp .env.example .env
docker compose up -d
```

访问 `http://<NAS_IP>:8987`，首次打开创建管理员账号，进入「设置」填写服务连接并一键测通即可使用（无需重启容器）。

## 本地开发

```bash
npm ci
python -m pip install -r services/fluxa-api/requirements.txt
npm run dev
```

## 测试与构建

```bash
npm run build
cd services/fluxa-api && python -m unittest discover -s tests -t . -v
```
