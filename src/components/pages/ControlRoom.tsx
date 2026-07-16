import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Clapperboard, Download, ExternalLink, HeartPulse, RefreshCcw, Rss, Wrench } from 'lucide-react';
import { getEmbyOverview, getEmbyRefreshStatus, getQbittorrentSummary, getSymediaSummary, getTorraSummary, triggerEmbyRefresh } from '../../services/api';
import type { EmbyOverview, EmbyRefreshStatus } from '../../types/emby';
import type { QbittorrentSummary } from '../../types/qbittorrent';
import type { SymediaSummary } from '../../types/symedia';
import type { TorraSummary } from '../../types/torra';
import { formatSpeed, formatTimeAgo } from '../../utils/formatters';

type ServiceState = 'ok' | 'warn' | 'down' | 'idle';

interface ServiceModel {
  id: 'torra' | 'qb' | 'symedia' | 'emby';
  order: string;
  name: string;
  role: string;
  state: ServiceState;
  stateLabel: string;
  metric: string;
  metricLabel: string;
  checked: string;
  meta: string[];
  icon: ReactNode;
  toolUrl: string;
  spark?: boolean;
}

function sparkPoints(values: number[]) {
  const max = Math.max(1, ...values);
  return values.map((value, index) => `${index * (200 / Math.max(1, values.length - 1))},${34 - (value / max) * 30}`).join(' ');
}

export function ControlRoom() {
  const [focusedService, setFocusedService] = useState<ServiceModel['id']>('torra');
  const [qb, setQb] = useState<QbittorrentSummary | null>(null);
  const [emby, setEmby] = useState<EmbyOverview | null>(null);
  const [torra, setTorra] = useState<TorraSummary | null>(null);
  const [symedia, setSymedia] = useState<SymediaSummary | null>(null);
  const [speedHistory, setSpeedHistory] = useState<number[]>(Array.from({ length: 20 }, () => 0));
  const [embyRefresh, setEmbyRefresh] = useState<EmbyRefreshStatus | null>(null);
  const [embyRefreshBusy, setEmbyRefreshBusy] = useState(false);
  const [embyRefreshConfirm, setEmbyRefreshConfirm] = useState(false);
  const [embyRefreshFeedback, setEmbyRefreshFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);

  const loadQb = () => {
    getQbittorrentSummary()
      .then((summary) => {
        setQb(summary);
        setSpeedHistory((current) => [...current.slice(-19), summary.transfer.downloadSpeed]);
      })
      .catch(() => {
        setQb(null);
        setSpeedHistory((current) => [...current.slice(-19), 0]);
      });
  };
  const loadEmby = () => getEmbyOverview().then(setEmby).catch(() => setEmby(null));
  const loadTorra = () => getTorraSummary().then(setTorra).catch(() => setTorra(null));
  const loadSymedia = () => getSymediaSummary().then(setSymedia).catch(() => setSymedia(null));
  const loadEmbyRefresh = () => getEmbyRefreshStatus().then(setEmbyRefresh).catch(() => setEmbyRefresh(null));
  const refreshAll = () => {
    loadTorra();
    loadQb();
    loadSymedia();
    loadEmby();
  };

  useEffect(() => {
    refreshAll();
    const timer = window.setInterval(refreshAll, 15000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (focusedService !== 'emby') return;
    loadEmbyRefresh();
    const timer = window.setInterval(loadEmbyRefresh, 30000);
    return () => window.clearInterval(timer);
  }, [focusedService]);

  const confirmEmbyRefresh = async () => {
    setEmbyRefreshBusy(true);
    setEmbyRefreshFeedback(null);
    try {
      const result = await triggerEmbyRefresh();
      setEmbyRefreshFeedback({ tone: 'success', message: result.message });
      setEmbyRefreshConfirm(false);
      loadEmbyRefresh();
      loadEmby();
    } catch (reason) {
      setEmbyRefreshFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'Emby 刷新请求失败' });
      setEmbyRefreshConfirm(false);
      loadEmbyRefresh();
    } finally {
      setEmbyRefreshBusy(false);
    }
  };

  const services = useMemo<ServiceModel[]>(() => {
    const torraState: ServiceState = torra?.connected ? 'ok' : torra?.configured ? 'down' : 'idle';
    const qbState: ServiceState = qb?.connected ? (qb.counts.stalled > 0 ? 'warn' : 'ok') : qb?.configured ? 'down' : 'idle';
    const symediaState: ServiceState = symedia?.connected
      ? symedia.totals.failedRecent > 0 ? 'warn' : 'ok'
      : symedia?.configured ? 'down' : 'idle';
    const embyState: ServiceState = emby?.connected ? 'ok' : emby?.configured ? 'down' : 'idle';
    const latestTransfer = symedia?.latest?.[0];
    const recentEntry = emby?.recent?.[0];

    return [
      {
        id: 'torra', order: '01', name: 'Torra', role: 'PT 搜索、匹配与下载编排', state: torraState,
        stateLabel: torra?.connected ? '在线' : torra?.configured ? '连接失败' : '未配置',
        metric: torra?.connected ? String(torra.counts.active) : '—', metricLabel: `活跃订阅 / 总计 ${torra?.counts.total ?? 0}`,
        checked: torra ? `检查于 ${formatTimeAgo(torra.lastCheckedAt)}` : '等待首次检查',
        meta: torra?.connected ? [`已完结 ${torra.counts.completed}`, torra.counts.running ? `${torra.counts.running} 个搜索进行中` : '当前无搜索任务'] : [torra?.error || '等待 Torra 连接'],
        icon: <Rss aria-hidden="true" size={20} />, toolUrl: torra?.webUrl || ''
      },
      {
        id: 'qb', order: '02', name: 'qBittorrent', role: 'PT 下载、做种与任务状态', state: qbState,
        stateLabel: qb?.connected ? (qb.counts.stalled > 0 ? '有卡住任务' : '在线') : qb?.configured ? '连接失败' : '未配置',
        metric: qb?.connected ? formatSpeed(qb.transfer.downloadSpeed) : '—', metricLabel: `${qb?.counts.active ?? 0} 活跃 / ${qb?.counts.total ?? 0} 总任务`,
        checked: qb ? `检查于 ${formatTimeAgo(qb.lastCheckedAt)}` : '等待首次检查',
        meta: qb?.connected ? [`上传 ${formatSpeed(qb.transfer.uploadSpeed)}`, `卡住 ${qb.counts.stalled}`] : [qb?.error || '等待 qB 连接'],
        icon: <Download aria-hidden="true" size={20} />, toolUrl: qb?.webUrl || '', spark: true
      },
      {
        id: 'symedia', order: '03', name: 'Symedia', role: '识别、整理、STRM 与归档', state: symediaState,
        stateLabel: symedia?.connected ? (symedia.totals.failedRecent > 0 ? '近期有失败' : '在线') : symedia?.configured ? '连接失败' : '未配置',
        metric: symedia?.connected ? String(symedia.totals.today) : '—', metricLabel: `今日入库 / 累计 ${new Intl.NumberFormat('zh-CN').format(symedia?.totals.records ?? 0)}`,
        checked: symedia?.connected && latestTransfer ? `最近：${latestTransfer.title}` : symedia ? `检查于 ${formatTimeAgo(symedia.lastCheckedAt)}` : '等待首次检查',
        meta: symedia?.connected ? [`近期失败 ${symedia.totals.failedRecent}`, latestTransfer?.seasonEpisode || '等待下一条入库'] : [symedia?.error || '等待 Symedia 连接'],
        icon: <Wrench aria-hidden="true" size={20} />, toolUrl: symedia?.webUrl || ''
      },
      {
        id: 'emby', order: '04', name: 'Emby', role: '最终媒体索引与播放', state: embyState,
        stateLabel: emby?.connected ? '在线' : emby?.configured ? '连接失败' : '未配置',
        metric: emby?.connected ? new Intl.NumberFormat('zh-CN').format(emby.counts?.episodes ?? 0) : '—', metricLabel: `${new Intl.NumberFormat('zh-CN').format(emby?.counts?.movies ?? 0)} 电影 / ${new Intl.NumberFormat('zh-CN').format(emby?.counts?.series ?? 0)} 剧集`,
        checked: recentEntry ? `最近：${recentEntry.seriesName ? `${recentEntry.seriesName} ${recentEntry.title}` : recentEntry.title}` : '暂无最近入库',
        meta: emby?.connected ? ['媒体库已连接', `最近记录 ${emby.recent?.length ?? 0} 条`] : [emby?.error || '等待 Emby 连接'],
        icon: <Clapperboard aria-hidden="true" size={20} />, toolUrl: emby?.serverUrl || ''
      }
    ];
  }, [qb, emby, torra, symedia]);

  const selected = services.find((service) => service.id === focusedService) ?? services[0];
  const onlineCount = services.filter((service) => service.state === 'ok' || service.state === 'warn').length;
  const warningCount = services.filter((service) => service.state === 'warn' || service.state === 'down').length;
  const points = sparkPoints(speedHistory);

  return (
    <main className="work-page ops-page ops-page--control">
      <section className="ops-hero ops-hero--control">
        <div>
          <p className="ops-eyebrow">CONTROL ROOM / CORE SERVICES</p>
          <h1>中控做判断，四个核心服务负责执行。</h1>
          <p className="ops-deck">先看 PT 主链是否完整，再进入原工具处理具体任务。</p>
        </div>
        <div className="ops-hero-actions">
          <div className={warningCount ? 'ops-system-score ops-system-score--warn' : 'ops-system-score'}>
            <small>核心服务</small><strong>{onlineCount} / 4 在线</strong><span>{warningCount ? `${warningCount} 项需检查` : '链路状态正常'}</span>
          </div>
          <button className="ops-icon-button" aria-label="刷新全部服务" type="button" onClick={refreshAll}><RefreshCcw size={18} /></button>
        </div>
      </section>

      <section className="ops-control-layout">
        <div className="ops-service-grid">
          {services.map((service) => (
            <button
              className={selected.id === service.id ? `ops-service-card ops-service-card--${service.state} ops-service-card--selected` : `ops-service-card ops-service-card--${service.state}`}
              key={service.id}
              type="button"
              onClick={() => setFocusedService(service.id)}
            >
              <span className="ops-service-card__order">{service.order}</span>
              <span className="ops-service-card__icon">{service.icon}</span>
              <span className="ops-service-card__state"><i />{service.stateLabel}</span>
              <span className="ops-service-card__copy"><strong>{service.name}</strong><small>{service.role}</small></span>
              <span className="ops-service-card__metric"><strong>{service.metric}</strong><small>{service.metricLabel}</small></span>
              {service.spark && (
                <svg aria-hidden="true" className="ops-service-card__spark" preserveAspectRatio="none" viewBox="0 0 200 34">
                  <polygon points={`0,34 ${points} 200,34`} /><polyline points={points} />
                </svg>
              )}
            </button>
          ))}
        </div>

        <aside className="ops-inspector">
          <header>
            <span>{selected.order} / CORE NODE</span>
            <i className={`ops-inspector__signal ops-inspector__signal--${selected.state}`} />
          </header>
          <div className="ops-inspector__title"><span>{selected.icon}</span><div><small>{selected.role}</small><h2>{selected.name}</h2></div></div>
          <div className="ops-inspector__metric"><strong>{selected.metric}</strong><span>{selected.metricLabel}</span></div>
          <dl className="ops-inspector__facts">
            <div><dt>连接状态</dt><dd>{selected.stateLabel}</dd></div>
            <div><dt>最近检查</dt><dd>{selected.checked}</dd></div>
            {selected.meta.map((item, index) => <div key={`${selected.id}-${item}`}><dt>{index === 0 ? '运行信息' : '补充信息'}</dt><dd>{item}</dd></div>)}
          </dl>
          {selected.id === 'emby' && (
            <div className={`ops-emby-refresh-evidence ops-emby-refresh-evidence--${embyRefresh?.state || 'loading'}`}>
              <header><span>SYMEDIA → EMBY</span><strong>{embyRefresh?.reason || '正在读取索引证据'}</strong></header>
              <div><small>Symedia 最新入库</small><span>{embyRefresh?.latestSymediaAt ? formatTimeAgo(embyRefresh.latestSymediaAt) : '证据不足'}</span></div>
              <div><small>Emby 最新索引</small><span>{embyRefresh?.latestEmbyAt ? formatTimeAgo(embyRefresh.latestEmbyAt) : '证据不足'}</span></div>
            </div>
          )}
          {selected.id === 'emby' && embyRefreshFeedback && (
            <div className={`ops-inspector-feedback ops-inspector-feedback--${embyRefreshFeedback.tone}`} role="status">{embyRefreshFeedback.message}</div>
          )}
          <div className={selected.id === 'emby' ? 'ops-inspector__actions ops-inspector__actions--three' : 'ops-inspector__actions'}>
            <button className="ops-action-button" type="button" onClick={() => { refreshAll(); if (selected.id === 'emby') loadEmbyRefresh(); }}><HeartPulse size={15} />重新检查</button>
            {selected.id === 'emby' && (
              <button
                className="ops-action-button ops-action-button--primary"
                disabled={embyRefresh?.state !== 'ready' || !embyRefresh.canRefresh || embyRefreshBusy}
                type="button"
                onClick={() => setEmbyRefreshConfirm(true)}
              >
                <RefreshCcw size={15} />{embyRefreshBusy ? '正在提交' : '刷新媒体库'}
              </button>
            )}
            <button
              className={selected.id === 'emby' ? 'ops-action-button' : 'ops-action-button ops-action-button--primary'}
              disabled={!selected.toolUrl}
              type="button"
              onClick={() => selected.toolUrl && window.open(selected.toolUrl, '_blank', 'noopener,noreferrer')}
            >
              <ExternalLink size={15} />打开原工具
            </button>
          </div>
          <p className="ops-inspector__note">{selected.id === 'emby' ? '刷新只在 Symedia 有较新入库证据时启用；提交后不等待后台扫描完成。' : '这里只提供只读状态、连接检查和原工具入口。高风险文件操作不会放进控制室。'}</p>
        </aside>
      </section>

      <section className="ops-control-foot">
        <span>内置订阅中枢</span>
        <strong>PT-only 默认策略</strong>
        <p>自动云盘兜底保持关闭；115、Resource Gateway 与 Refind 在辅助通道接入后单独展示。</p>
      </section>

      {embyRefreshConfirm && (
        <div className="ops-confirm-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !embyRefreshBusy) setEmbyRefreshConfirm(false);
        }}>
          <section aria-labelledby="emby-refresh-title" aria-modal="true" className="ops-confirm-dialog" role="dialog">
            <span className="ops-confirm-dialog__signal">EMBY / LIBRARY REFRESH</span>
            <h2 id="emby-refresh-title">触发 Emby 全库扫描？</h2>
            <p>Symedia 出现了比 Emby 索引更晚的成功入库记录。确认后只提交后台扫描请求，页面不会等待扫描完成。</p>
            <div className="ops-confirm-dialog__meta">
              <span>Symedia 证据</span><strong>{embyRefresh?.latestSymediaAt ? formatTimeAgo(embyRefresh.latestSymediaAt) : '未知'}</strong>
              <span>保护规则</span><strong>手动确认 · 10 分钟冷却</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" disabled={embyRefreshBusy} type="button" onClick={() => setEmbyRefreshConfirm(false)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" disabled={embyRefreshBusy || embyRefresh?.state !== 'ready' || !embyRefresh.canRefresh} type="button" onClick={confirmEmbyRefresh} autoFocus>
                <RefreshCcw size={14} />{embyRefreshBusy ? '正在提交' : '确认刷新'}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
