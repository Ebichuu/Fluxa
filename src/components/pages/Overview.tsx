import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, Clapperboard, Cpu, Download, HardDrive, MemoryStick, Network, Rss, Server, ShieldCheck, TriangleAlert } from 'lucide-react';
import {
  getEmbyOverview,
  getQbittorrentSummary,
  getSubscriptionCalendar,
  getSubscriptionItems,
  getSymediaSummary,
  getSystemMetrics,
  getTorraSummary
} from '../../services/api';
import type { EmbyOverview, EmbyRecentItem } from '../../types/emby';
import type { QbittorrentSummary, QbittorrentTask } from '../../types/qbittorrent';
import type { SymediaSummary } from '../../types/symedia';
import type { TorraSummary } from '../../types/torra';
import type { SystemMetricsResponse } from '../../types/operations';
import type { MediaCategory, SubscriptionItem } from '../../types/subscriptions';
import { localDateKey, monthsInDateRange } from '../../utils/dateRanges';
import { formatEta, formatPercent, formatSpeed, formatTimeAgo } from '../../utils/formatters';
import type { PageId } from '../layout/AppTopNav';
import { usePolling } from '../../hooks/usePolling';

interface OverviewProps {
  onNavigate: (page: PageId) => void;
}

const categoryLabels: Partial<Record<MediaCategory, string>> = {
  anime_jp: '日漫',
  anime_cn: '国漫',
  tv_cn: '国产剧',
  tv_asia: '日韩剧',
  tv_western: '欧美剧',
  tv_hk_tw: '港台剧',
  variety: '综艺',
  movie: '电影'
};

function subDetail(item: SubscriptionItem) {
  const parts = ['PT'];
  if (item.mediaCategory) parts.push(categoryLabels[item.mediaCategory] ?? item.mediaCategory);
  if (item.seasonName) parts.push(item.seasonName);
  if (item.progressText) parts.push(`进度 ${item.progressText}`);
  return parts.join(' · ');
}

function downloadDetail(task: QbittorrentTask) {
  if (task.status === 'completed') return `下载完成 · ${formatTimeAgo(task.completionOn)}`;
  if (task.status === 'paused') return `已暂停 · ${formatPercent(task.progress)}%`;
  if (task.status === 'stalled') return `${task.stateLabel} · ${formatPercent(task.progress)}% · ${formatSpeed(task.dlspeed)}`;
  return `${formatSpeed(task.dlspeed)} · ${formatEta(task.eta)}`;
}

function recentAddedDetail(item: EmbyRecentItem) {
  const label = item.type === 'Movie' ? '电影' : item.type === 'Series' ? '剧集' : '剧集更新';
  const date = item.dateCreated ? formatTimeAgo(item.dateCreated) : '';
  return date ? `${label} · ${date}` : label;
}

function formatCapacity(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

export function Overview({ onNavigate }: OverviewProps) {
  const [subs, setSubs] = useState<SubscriptionItem[]>([]);
  const [upcomingCount, setUpcomingCount] = useState<number | null>(null);
  const [qb, setQb] = useState<QbittorrentSummary | null>(null);
  const [emby, setEmby] = useState<EmbyOverview | null>(null);
  const [torra, setTorra] = useState<TorraSummary | null>(null);
  const [symedia, setSymedia] = useState<SymediaSummary | null>(null);
  const [metrics, setMetrics] = useState<SystemMetricsResponse | null>(null);

  const loadLive = async (signal: AbortSignal) => {
    const options = { signal };
    const [qbResult, embyResult, torraResult, symediaResult] = await Promise.allSettled([
      getQbittorrentSummary(options),
      getEmbyOverview(options),
      getTorraSummary(options),
      getSymediaSummary(options)
    ]);
    if (signal.aborted) return;
    if (qbResult.status === 'fulfilled') setQb(qbResult.value);
    if (embyResult.status === 'fulfilled') setEmby(embyResult.value);
    if (torraResult.status === 'fulfilled') setTorra(torraResult.value);
    if (symediaResult.status === 'fulfilled') setSymedia(symediaResult.value);
  };

  const loadMetrics = async (signal: AbortSignal) => {
    try {
      const value = await getSystemMetrics({ signal });
      if (!signal.aborted) setMetrics(value);
    } catch {
      // 保留上一次有效指标，短暂网络失败不伪装成系统无数据。
    }
  };

  usePolling(loadLive, 15000);
  usePolling(loadMetrics, 60000);

  useEffect(() => {
    const controller = new AbortController();

    getSubscriptionItems(true, { signal: controller.signal })
      .then((payload) => {
        if (!controller.signal.aborted && payload.configured && payload.subscriptions) setSubs(payload.subscriptions.items);
      })
      .catch(() => undefined);

    const now = new Date();
    const endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 3);
    Promise.all(monthsInDateRange(now, endDate).map(({ year, month }) =>
      getSubscriptionCalendar(year, month, 'all', { signal: controller.signal })
    ))
      .then((payloads) => {
        if (controller.signal.aborted) return;
        const calendars = payloads.filter((payload) => payload.configured && payload.calendar).map((payload) => payload.calendar!);
        if (calendars.length === 0) return;
        const start = localDateKey(now);
        const end = localDateKey(endDate);
        setUpcomingCount(
          calendars.flatMap((calendar) => calendar.entries).filter((entry) => entry.date >= start && entry.date <= end).length
        );
      })
      .catch(() => undefined);

    return () => controller.abort();
  }, []);

  const downloading = useMemo(
    () => (qb?.tasks ?? []).filter((task) => ['downloading', 'stalled', 'queued'].includes(task.status)).slice(0, 5),
    [qb]
  );
  const warnings = [
    !torra?.connected ? 'Torra 未连接' : '',
    !qb?.connected ? 'qB 未连接' : '',
    (qb?.counts.stalled ?? 0) > 0 ? `${qb?.counts.stalled} 个下载卡住` : '',
    !symedia?.connected ? 'Symedia 未连接' : '',
    (symedia?.totals.failedRecent ?? 0) > 0 ? `${symedia?.totals.failedRecent} 条近期入库失败` : '',
    !emby?.connected ? 'Emby 未连接' : ''
  ].filter(Boolean);
  const recentAdds = emby?.connected ? emby.recent ?? [] : [];
  const recentSubs = subs.slice(0, 5);
  const healthy = warnings.length === 0;

  const pipeline = [
    {
      label: '订阅',
      value: `${subs.length} 条`,
      detail: '我的订阅',
      state: subs.length > 0 ? 'ok' : 'idle'
    },
    {
      label: '正在下载',
      value: torra?.connected && qb?.connected ? `${torra.counts.active} / ${qb.counts.active}` : '待连接',
      detail: 'Torra 活跃订阅 / qB 活跃任务',
      state: torra?.connected && qb?.connected ? ((qb.counts.stalled ?? 0) > 0 ? 'warn' : 'ok') : 'idle'
    },
    {
      label: '进入 115',
      value: '等待任务记录',
      detail: '秒传明细尚未接入控制中心',
      state: 'idle'
    },
    {
      label: '整理与入库',
      value: symedia?.connected ? `${symedia.totals.today} 条` : '待连接',
      detail: emby?.connected ? `Emby 最近 ${recentAdds.length} 条` : '等待 Symedia / Emby',
      state: symedia?.connected && emby?.connected ? ((symedia.totals.failedRecent ?? 0) > 0 ? 'warn' : 'done') : 'idle'
    }
  ];

  return (
    <main className="work-page ops-page ops-page--overview">
      <section className="ops-hero">
        <div>
          <p className="ops-eyebrow">PT 主链</p>
          <h1>总览</h1>
          <p className="ops-page-subtitle">从订阅到入库，一眼看清进度。</p>
          <p className="ops-deck">这里汇总正在下载、等待整理和已经入库的内容；需要处理时再进入任务中心。</p>
        </div>
        <button className={healthy ? 'ops-command ops-command--ok' : 'ops-command ops-command--warn'} type="button" onClick={() => onNavigate('tasks')}>
          {healthy ? <ShieldCheck aria-hidden="true" size={18} /> : <TriangleAlert aria-hidden="true" size={18} />}
          <span>
            <small>当前状态</small>
            <strong>{healthy ? 'PT 主链运行中' : `${warnings.length} 项需要检查`}</strong>
          </span>
          <ArrowRight aria-hidden="true" size={16} />
        </button>
      </section>

      <section className="pipeline-rail" aria-label="媒体处理链路">
        {pipeline.map((step, index) => (
          <article className={`pipeline-node pipeline-node--${step.state}`} key={step.label}>
            <span className="pipeline-node__index">0{index + 1}</span>
            <div>
              <small>{step.label}</small>
              <strong>{step.value}</strong>
              <p>{step.detail}</p>
            </div>
          </article>
        ))}
      </section>

      <section className="ops-dashboard-grid">
        <article className="ops-panel ops-panel--primary">
          <header className="ops-panel__head">
            <div>
              <span className="ops-panel__icon"><Download aria-hidden="true" size={17} /></span>
              <div><small>下载任务</small><h2>正在下载</h2></div>
            </div>
            <button className="ops-link" type="button" onClick={() => onNavigate('tasks')}>查看任务中心 <ArrowRight size={14} /></button>
          </header>
          <div className="ops-list">
            {!qb?.connected && <div className="ops-empty">{qb?.error || 'qBittorrent 尚未连接，暂时没有下载证据。'}</div>}
            {qb?.connected && downloading.length === 0 && <div className="ops-empty">当前没有进行中的 PT 下载任务。</div>}
            {qb?.connected && downloading.map((task) => {
              const progress = formatPercent(task.progress);
              return (
                <div className={task.status === 'stalled' ? 'ops-list-row ops-list-row--warn' : 'ops-list-row'} key={task.hash || task.name}>
                  <div className="ops-list-row__copy"><strong>{task.name}</strong><small>{downloadDetail(task)}</small></div>
                  <span className="ops-data">{progress}%</span>
                  <div className="ops-progress" style={{ '--progress': `${progress}%` } as React.CSSProperties}><span /></div>
                </div>
              );
            })}
          </div>
        </article>

        <div className="ops-stack">
          <article className="ops-panel ops-status-panel">
            <header className="ops-panel__head ops-panel__head--compact">
              <div><span className="ops-panel__icon"><Server aria-hidden="true" size={16} /></span><h2>运行摘要</h2></div>
            </header>
            <dl className="ops-facts">
              <div><dt>Torra</dt><dd>{torra?.connected ? `${torra.counts.active} 条活跃订阅` : torra?.error || '未连接'}</dd></div>
              <div><dt>qB</dt><dd>{qb?.connected ? `${formatSpeed(qb.transfer.downloadSpeed)} · ${qb.counts.active} 活跃` : qb?.error || '未连接'}</dd></div>
              <div><dt>Symedia</dt><dd>{symedia?.connected ? `今日 ${symedia.totals.today} 条 · 近期失败 ${symedia.totals.failedRecent}` : symedia?.error || '未连接'}</dd></div>
              <div><dt>Emby</dt><dd>{emby?.connected ? `${new Intl.NumberFormat('zh-CN').format(emby.counts?.episodes ?? 0)} 集在库` : emby?.error || '未连接'}</dd></div>
            </dl>
          </article>

          <article className="ops-panel ops-system-metrics">
            <header className="ops-panel__head ops-panel__head--compact">
              <div><span className="ops-panel__icon"><Cpu aria-hidden="true" size={16} /></span><h2>NAS 运行负载</h2></div>
              <small>{metrics ? formatTimeAgo(metrics.checkedAt) : '指标暂不可用'}</small>
            </header>
            <div className="ops-metrics-grid">
              <div><Cpu size={14} /><span>CPU</span><strong>{metrics ? `${metrics.cpu.percent}%` : '—'}</strong></div>
              <div><MemoryStick size={14} /><span>内存</span><strong>{metrics ? `${metrics.memory.percent}%` : '—'}</strong></div>
              <div><HardDrive size={14} /><span>磁盘</span><strong>{metrics ? `${metrics.disk.percent}% · ${formatCapacity(metrics.disk.free)} 可用` : '—'}</strong></div>
              <div><Network size={14} /><span>网络</span><strong>{metrics ? `↓ ${formatSpeed(metrics.network.downBps)} · ↑ ${formatSpeed(metrics.network.upBps)}` : '—'}</strong></div>
            </div>
          </article>

          <button className="ops-panel ops-next-air" type="button" onClick={() => onNavigate('calendar')}>
            <Rss aria-hidden="true" size={18} />
            <span><small>未来 72 小时</small><strong>{upcomingCount === null ? '播出统计待连接' : `${upcomingCount} 集即将播出`}</strong></span>
            <ArrowRight aria-hidden="true" size={16} />
          </button>
        </div>
      </section>

      <section className="ops-lower-grid">
        <article className="ops-panel">
          <header className="ops-panel__head">
            <div><span className="ops-panel__icon ops-panel__icon--library"><Clapperboard aria-hidden="true" size={17} /></span><div><small>媒体库</small><h2>最近入库</h2></div></div>
            <button className="ops-link" type="button" onClick={() => onNavigate('hall')}>进入影院大厅 <ArrowRight size={14} /></button>
          </header>
          <div className="ops-compact-list">
            {recentAdds.length === 0 && <div className="ops-empty">Emby 暂无最近入库，或服务尚未连接。</div>}
            {recentAdds.slice(0, 5).map((item) => (
              <div key={item.id}><strong>{item.type === 'Episode' && item.seriesName ? `${item.seriesName} ${item.title}` : item.title}</strong><small>{recentAddedDetail(item)}</small></div>
            ))}
          </div>
        </article>

        <article className="ops-panel">
          <header className="ops-panel__head">
            <div><span className="ops-panel__icon"><Rss aria-hidden="true" size={17} /></span><div><small>订阅</small><h2>最近订阅</h2></div></div>
            <button className="ops-link" type="button" onClick={() => onNavigate('subscriptions')}>管理订阅 <ArrowRight size={14} /></button>
          </header>
          <div className="ops-compact-list">
            {recentSubs.length === 0 && <div className="ops-empty">还没有真实订阅记录。</div>}
            {recentSubs.map((item) => <div key={item.id ?? item.title}><strong>{item.title}</strong><small>{subDetail(item)}</small></div>)}
          </div>
        </article>
      </section>
    </main>
  );
}
