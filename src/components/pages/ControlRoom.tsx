import { useMemo, useState, type ReactNode } from 'react';
import { Clapperboard, Download, ExternalLink, HeartPulse, RefreshCcw, Rss, ShieldCheck, Wrench } from 'lucide-react';
import { getEmbyOverview, getEmbyRefreshStatus, getIntegrationSummary, getQbittorrentSummary, getSubscriptionCapabilities, getSymediaSummary, getTorraSummary, triggerEmbyRefresh } from '../../services/api';
import type { EmbyOverview, EmbyRefreshStatus } from '../../types/emby';
import type { QbittorrentSummary } from '../../types/qbittorrent';
import type { SymediaSummary } from '../../types/symedia';
import type { TorraSummary } from '../../types/torra';
import type { IntegrationSummary } from '../../types/integrations';
import type { SubscriptionCapabilitiesResponse } from '../../types/subscriptions';
import { usePolling } from '../../hooks/usePolling';
import { formatSpeed, formatTimeAgo } from '../../utils/formatters';
import { ConfirmDialog } from '../layout/ConfirmDialog';

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
  const [integrations, setIntegrations] = useState<IntegrationSummary | null>(null);
  const [subscriptionCapabilities, setSubscriptionCapabilities] = useState<SubscriptionCapabilitiesResponse | null>(null);
  const [speedHistory, setSpeedHistory] = useState<number[]>(Array.from({ length: 20 }, () => 0));
  const [embyRefresh, setEmbyRefresh] = useState<EmbyRefreshStatus | null>(null);
  const [embyRefreshBusy, setEmbyRefreshBusy] = useState(false);
  const [embyRefreshConfirm, setEmbyRefreshConfirm] = useState(false);
  const [embyRefreshFeedback, setEmbyRefreshFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [servicesRefreshBusy, setServicesRefreshBusy] = useState(false);
  const [servicesRefreshFeedback, setServicesRefreshFeedback] = useState('');

  const refreshAll = async (signal: AbortSignal, reportResult = false) => {
    const options = { signal };
    const [torraResult, qbResult, symediaResult, embyResult, integrationsResult, capabilitiesResult] = await Promise.allSettled([
      getTorraSummary(options),
      getQbittorrentSummary(options),
      getSymediaSummary(options),
      getEmbyOverview(options),
      getIntegrationSummary(false, options),
      getSubscriptionCapabilities(options)
    ]);
    if (signal.aborted) return;
    if (torraResult.status === 'fulfilled') setTorra(torraResult.value);
    if (symediaResult.status === 'fulfilled') setSymedia(symediaResult.value);
    if (embyResult.status === 'fulfilled') setEmby(embyResult.value);
    if (integrationsResult.status === 'fulfilled') setIntegrations(integrationsResult.value);
    if (capabilitiesResult.status === 'fulfilled') setSubscriptionCapabilities(capabilitiesResult.value);
    if (qbResult.status === 'fulfilled') {
      setQb(qbResult.value);
      setSpeedHistory((current) => [...current.slice(-19), qbResult.value.transfer.downloadSpeed]);
    }
    if (reportResult) {
      const failedCount = [torraResult, qbResult, symediaResult, embyResult, integrationsResult, capabilitiesResult]
        .filter((result) => result.status === 'rejected').length;
      setServicesRefreshFeedback(failedCount > 0 ? `刷新完成，${failedCount} 项服务暂不可用` : '服务状态已更新');
    }
  };

  const loadEmbyRefresh = async (signal: AbortSignal) => {
    try {
      const status = await getEmbyRefreshStatus({ signal });
      if (!signal.aborted) setEmbyRefresh(status);
    } catch {
      if (!signal.aborted) setEmbyRefresh(null);
    }
  };

  const refreshServices = async () => {
    if (servicesRefreshBusy) return;
    setServicesRefreshBusy(true);
    setServicesRefreshFeedback('');
    try {
      await refreshAll(new AbortController().signal, true);
    } finally {
      setServicesRefreshBusy(false);
    }
  };
  const refreshEmbyStatus = () => loadEmbyRefresh(new AbortController().signal);

  const refreshSelectedService = async () => {
    await refreshServices();
    if (focusedService === 'emby') await refreshEmbyStatus();
  };

  usePolling(refreshAll, 15000);

  usePolling(loadEmbyRefresh, 30000, { enabled: focusedService === 'emby' });

  const confirmEmbyRefresh = async () => {
    setEmbyRefreshBusy(true);
    setEmbyRefreshFeedback(null);
    try {
      const result = await triggerEmbyRefresh();
      setEmbyRefreshFeedback({ tone: 'success', message: result.message });
      setEmbyRefreshConfirm(false);
      void refreshEmbyStatus();
      void refreshServices();
    } catch (reason) {
      setEmbyRefreshFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'Emby 刷新请求失败' });
      setEmbyRefreshConfirm(false);
      void refreshEmbyStatus();
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
        metric: symedia?.connected ? String(symedia.totals.processedToday ?? symedia.totals.today) : '—', metricLabel: `今日处理 / 累计 ${new Intl.NumberFormat('zh-CN').format(symedia?.totals.records ?? 0)}`,
        checked: symedia?.connected && latestTransfer ? `最近：${latestTransfer.title}` : symedia ? `检查于 ${formatTimeAgo(symedia.lastCheckedAt)}` : '等待首次检查',
        meta: symedia?.connected ? [
          `成功归档 ${symedia.totals.archivedToday ?? 0} · 正常保护 ${symedia.totals.protectedToday ?? 0}`,
          symedia.totals.failedRecent > 0 ? `真实失败 ${symedia.totals.failedRecent}` : latestTransfer?.seasonEpisode || '近期没有真实归档故障'
        ] : [symedia?.error || '等待 Symedia 连接'],
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
  const configuredCount = services.filter((service) => service.state !== 'idle').length;
  const unconfiguredCount = services.length - configuredCount;
  const points = sparkPoints(speedHistory);
  const moviePilot = integrations?.services.find((service) => service.id === 'moviepilot');
  const moviePilotStatus: { label: string; tone: 'loading' | 'idle' | 'warn' | 'ok' | 'configured' } = !integrations
    ? { label: '读取中', tone: 'loading' }
    : !moviePilot?.configured
      ? { label: '未配置', tone: 'idle' }
      : moviePilot.connected === true
        ? { label: '可用', tone: 'ok' }
        : moviePilot.connected === false
          ? { label: '需检查', tone: 'warn' }
          : { label: '已配置', tone: 'configured' };
  const schedulerStatus = !subscriptionCapabilities
    ? { label: '证据不足', detail: '调度状态尚未读取', tone: 'loading' as const }
    : !subscriptionCapabilities.scheduler.configured || !subscriptionCapabilities.scheduler.enabled
      ? { label: '已关闭', detail: '自动追更调度当前不运行', tone: 'idle' as const }
      : subscriptionCapabilities.scheduler.running
        ? { label: '运行中', detail: subscriptionCapabilities.scheduler.lastRunAt ? `上次执行 ${formatTimeAgo(subscriptionCapabilities.scheduler.lastRunAt)}` : '已确认后台运行', tone: 'ok' as const }
        : { label: '未运行', detail: subscriptionCapabilities.scheduler.lastError || '已开启，但没有读到后台运行证据', tone: 'warn' as const };
  const torraPushLabel = subscriptionCapabilities?.torraPush.enabled ? '已开启' : '已关闭';

  return (
    <main className="work-page ops-page ops-page--control">
      <section className="ops-hero ops-hero--control">
        <div>
          <p className="ops-eyebrow">服务状态</p>
          <h1>控制室</h1>
          <p className="ops-page-subtitle">查看各项服务是否正常。</p>
          <p className="ops-deck">遇到下载或入库问题时，先在这里找到需要处理的服务，再进入原工具查看详情。</p>
        </div>
        <div className="ops-hero-actions">
          <div className={warningCount || unconfiguredCount ? 'ops-system-score ops-system-score--warn' : 'ops-system-score'}>
            <small>核心服务</small><strong>{onlineCount} / 4 在线</strong><span>{warningCount ? `${warningCount} 项需检查` : unconfiguredCount ? `${unconfiguredCount} 项未配置` : '全部服务证据可用'}</span>
          </div>
          <button aria-label="刷新全部服务" aria-busy={servicesRefreshBusy} className="ops-icon-button" disabled={servicesRefreshBusy} title="刷新全部服务" type="button" onClick={() => void refreshServices()}><RefreshCcw aria-hidden="true" size={18} /></button>
          {servicesRefreshFeedback && <small aria-live="polite">{servicesRefreshFeedback}</small>}
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
            <span>{selected.order} · 核心服务</span>
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
              <header><span>整理 → 媒体库</span><strong>{embyRefresh?.reason || '正在读取索引记录'}</strong></header>
              <div><small>Symedia 最新入库</small><span>{embyRefresh?.latestSymediaAt ? formatTimeAgo(embyRefresh.latestSymediaAt) : '证据不足'}</span></div>
              <div><small>Emby 最新索引</small><span>{embyRefresh?.latestEmbyAt ? formatTimeAgo(embyRefresh.latestEmbyAt) : '证据不足'}</span></div>
            </div>
          )}
          {selected.id === 'emby' && embyRefreshFeedback && (
            <div className={`ops-inspector-feedback ops-inspector-feedback--${embyRefreshFeedback.tone}`} role="status">{embyRefreshFeedback.message}</div>
          )}
          <div className={selected.id === 'emby' ? 'ops-inspector__actions ops-inspector__actions--three' : 'ops-inspector__actions'}>
            <button className="ops-action-button" disabled={servicesRefreshBusy} type="button" onClick={() => void refreshSelectedService()}><HeartPulse size={15} />{servicesRefreshBusy ? '检查中' : '重新检查'}</button>
            {selected.id === 'emby' && (
              <button
                className="ops-action-button ops-action-button--primary"
                disabled={servicesRefreshBusy || embyRefresh?.state !== 'ready' || !embyRefresh.canRefresh || embyRefreshBusy}
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
          <details className="ops-inspector__diagnostics">
            <summary>高级诊断</summary>
            <dl>
              <div><dt>证据来源</dt><dd>{selected.name}</dd></div>
              <div><dt>配置状态</dt><dd>{selected.state === 'idle' ? '未配置' : '已配置'}</dd></div>
              <div><dt>连接证据</dt><dd>{selected.state === 'ok' || selected.state === 'warn' ? '已获取' : '不可用'}</dd></div>
              <div><dt>业务影响</dt><dd>{selected.state === 'ok' ? '当前未发现服务级异常' : selected.state === 'warn' ? '部分任务需继续观察' : selected.state === 'down' ? '相关任务可能无法继续' : '相关能力不可验证'}</dd></div>
            </dl>
            <p>服务地址和凭据只在设置页编辑；这里不回显 Token、Cookie 或密码。</p>
          </details>
        </aside>
      </section>

      <section className="ops-control-foot">
        <span className={`ops-control-foot__backup ops-control-foot__backup--${schedulerStatus.tone}`}><ShieldCheck aria-hidden="true" size={12} />追更调度 · {schedulerStatus.label}</span>
        <strong>{schedulerStatus.detail}</strong>
        <span className={`ops-control-foot__backup ops-control-foot__backup--${subscriptionCapabilities?.torraPush.enabled ? 'ok' : 'idle'}`}>
          <Rss aria-hidden="true" size={12} />Torra 推送 · {torraPushLabel}
        </span>
        <span className={`ops-control-foot__backup ops-control-foot__backup--${moviePilotStatus.tone}`}>
          <Rss aria-hidden="true" size={12} />MoviePilot 备用 · {moviePilotStatus.label}
        </span>
        <p>连接正常只代表服务可读，不代表整条影音链路已完成；最终结果以任务中心和 Emby 索引证据为准。</p>
      </section>

      <ConfirmDialog
        busy={embyRefreshBusy}
        describedBy="emby-refresh-description"
        labelledBy="emby-refresh-title"
        open={embyRefreshConfirm}
        onClose={() => setEmbyRefreshConfirm(false)}
      >
            <span className="ops-confirm-dialog__signal">媒体库 · 刷新索引</span>
            <h2 id="emby-refresh-title">触发 Emby 全库扫描？</h2>
            <p id="emby-refresh-description">Symedia 出现了比 Emby 索引更晚的成功入库记录。确认后只提交后台扫描请求，页面不会等待扫描完成。</p>
            <div className="ops-confirm-dialog__meta">
              <span>Symedia 证据</span><strong>{embyRefresh?.latestSymediaAt ? formatTimeAgo(embyRefresh.latestSymediaAt) : '未知'}</strong>
              <span>保护规则</span><strong>手动确认 · 10 分钟冷却</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" disabled={embyRefreshBusy} type="button" onClick={() => setEmbyRefreshConfirm(false)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus disabled={embyRefreshBusy || embyRefresh?.state !== 'ready' || !embyRefresh.canRefresh} type="button" onClick={confirmEmbyRefresh}>
                <RefreshCcw size={14} />{embyRefreshBusy ? '正在提交' : '确认刷新'}
              </button>
            </div>
      </ConfirmDialog>
    </main>
  );
}
