import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, Braces, CheckCircle2, CircleHelp, Clock3, Copy, Download, ExternalLink, HardDrive, Pause, Play, RefreshCcw, Rss, Server, ShieldCheck } from 'lucide-react';
import { getActivityLogs, getTaskChainDetailV2, getTaskChainV2, previewQbittorrentAction, runQbittorrentAction } from '../../services/api';
import type { QbittorrentAction, QbittorrentActionPreview } from '../../types/qbittorrent';
import type { TaskChainHealthState, TaskChainItem, TaskChainListItem, TaskChainResponse, TaskChainStage, TaskChainState } from '../../types/taskChain';
import type { ActivityLogItem } from '../../types/operations';
import { usePolling } from '../../hooks/usePolling';
import { formatSpeed, formatTimeAgo } from '../../utils/formatters';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import type { TaskNavigationTarget } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';

type FilterName = '全部' | '需要处理' | '证据不足' | '等待' | '正常保护' | '正常';

const filters: FilterName[] = ['全部', '需要处理', '证据不足', '等待', '正常保护', '正常'];

const activityFilters = [
  { key: '', label: '全部' },
  { key: 'subscription', label: '订阅' },
  { key: 'torra_sync', label: 'Torra 同步' },
  { key: 'push', label: 'Torra 推送' },
  { key: 'qbittorrent', label: 'qB' },
  { key: 'system', label: '系统' }
] as const;

const activityCategoryLabels: Record<string, string> = {
  subscription: '订阅',
  torra_sync: 'Torra 同步',
  push: 'Torra 推送',
  qbittorrent: 'qBittorrent',
  operation: '操作',
  system: '系统'
};

const activityActionLabels: Record<string, string> = {
  torra_sync_preview: '同步预览',
  torra_sync_import: '导入订阅',
  torra_sync_run: '状态同步',
  torra_push_v2: '订阅推送',
  private_rss_request: 'RSS 请求'
};

const stateLabel: Record<TaskChainState, string> = {
  active: '进行中',
  blocked: '卡住',
  completed: '已入库',
  waiting: '等待中'
};

const stageStatusLabel: Record<string, string> = {
  done: '已完成',
  active: '处理中',
  blocked: '已阻塞',
  waiting: '等待中',
  unknown: '证据不足'
};

function resolvedHealth(item: TaskChainListItem | TaskChainItem): TaskChainHealthState {
  if (item.healthState) return item.healthState;
  if (item.state === 'blocked') return 'action_required';
  if (item.confidence === 'unlinked') return 'evidence_insufficient';
  if (item.state === 'active' || item.state === 'waiting') return 'waiting';
  return 'normal';
}

function stageItems(item: TaskChainListItem | TaskChainItem): TaskChainStage[] {
  if (item.stages?.length) return item.stages;
  return (item.steps ?? []).map((step) => ({
    stage: step.key,
    label: step.label,
    status: step.status,
    healthState: step.status === 'done' ? 'normal' : step.status === 'blocked' ? 'action_required' : step.status === 'unknown' ? 'evidence_insufficient' : 'waiting',
    evidence: step.evidence,
    observedAt: step.timestamp,
    freshUntil: '',
    source: step.source,
    reasonCode: '',
    reasonText: step.detail,
    recommendedAction: '',
    retryEligible: false,
    plannedRetryAt: '',
    actions: { preview: false, retry: false }
  }));
}

function stageClass(stage: TaskChainStage) {
  if (stage.healthState === 'action_required' || stage.status === 'blocked') return 'ops-task-chain__step is-stuck';
  if (stage.healthState === 'evidence_insufficient' || stage.status === 'unknown') return 'ops-task-chain__step is-unknown';
  if (stage.healthState === 'protected') return 'ops-task-chain__step is-protected';
  if (stage.status === 'done') return 'ops-task-chain__step is-done';
  if (stage.status === 'active' || stage.status === 'waiting') return 'ops-task-chain__step is-now';
  return 'ops-task-chain__step is-unknown';
}

function evidenceLabel(stage: TaskChainStage) {
  if (stage.evidence === 'verified') return '已验证';
  if (stage.evidence === 'inferred') return '推断证据';
  return '证据不足';
}

function stageDisplayLabel(stage: TaskChainStage) {
  if (stage.stage === 'download') return 'qB 下载';
  if (stage.stage === 'cloud115') return '115 接管';
  if (stage.stage === 'library') return '整理与入库';
  return stage.label;
}

function currentDetail(item: TaskChainListItem | TaskChainItem) {
  if (item.reasonText) return item.reasonText;
  if (!item.stages?.length && !item.steps?.length) return '展开后查看完整阶段证据';
  const stages = stageItems(item);
  const current = stages.find((stage) => stage.stage === item.currentStep)
    ?? stages.find((stage) => stage.status === 'blocked' || stage.status === 'active')
    ?? stages.at(-1);
  return item.reasonText || current?.reasonText || '等待下一步证据';
}

function recommendedAction(item: TaskChainListItem | TaskChainItem, health: TaskChainHealthState) {
  if (item.recommendedAction) return item.recommendedAction;
  const current = stageItems(item).find((stage) => stage.stage === item.currentStep)
    ?? stageItems(item).find((stage) => stage.recommendedAction);
  if (current?.recommendedAction) return current.recommendedAction;
  if (health === 'action_required') return '查看当前阶段证据并处理阻塞';
  if (health === 'evidence_insufficient') return '刷新来源后重新检查';
  if (health === 'waiting') return '等待当前阶段完成';
  if (health === 'protected') return '已保留低分源文件，可进入存储清理';
  return '';
}

function guidanceIcon(health: TaskChainHealthState) {
  if (health === 'action_required') return <AlertTriangle aria-hidden="true" size={16} />;
  if (health === 'evidence_insufficient') return <CircleHelp aria-hidden="true" size={16} />;
  if (health === 'protected') return <ShieldCheck aria-hidden="true" size={16} />;
  return <Clock3 aria-hidden="true" size={16} />;
}

function targetLabel(item: TaskChainListItem | TaskChainItem) {
  const episode = item.targetKey?.match(/:episode:(\d+)/)?.[1];
  const season = item.targetKey?.match(/:season:(\d+)/)?.[1] || (item.seasonNumber > 0 ? String(item.seasonNumber) : '');
  if (episode && season) return `S${season.padStart(2, '0')}E${episode.padStart(2, '0')}`;
  if (season) return `S${season.padStart(2, '0')}`;
  return item.mediaType === 'movie' ? '整部电影' : '整部剧集';
}

function stageStatusIcon(stage: TaskChainStage) {
  if (stage.healthState === 'action_required' || stage.status === 'blocked') return <AlertTriangle aria-hidden="true" size={14} />;
  if (stage.healthState === 'evidence_insufficient' || stage.status === 'unknown') return <CircleHelp aria-hidden="true" size={14} />;
  if (stage.healthState === 'protected') return <ShieldCheck aria-hidden="true" size={14} />;
  if (stage.status === 'done') return <CheckCircle2 aria-hidden="true" size={14} />;
  return <Clock3 aria-hidden="true" size={14} />;
}

function healthForFilter(filter: FilterName): TaskChainHealthState | undefined {
  if (filter === '需要处理') return 'action_required';
  if (filter === '证据不足') return 'evidence_insufficient';
  if (filter === '等待') return 'waiting';
  if (filter === '正常保护') return 'protected';
  if (filter === '正常') return 'normal';
  return undefined;
}

export function TasksCenter({ target, onClearTarget }: { target: TaskNavigationTarget | null; onClearTarget: () => void }) {
  const [filter, setFilter] = useState<FilterName>('全部');
  const [chain, setChain] = useState<TaskChainResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pageLimit, setPageLimit] = useState(20);
  const [details, setDetails] = useState<Record<string, { snapshotVersion: string; item: TaskChainItem }>>({});
  const [expandedChainId, setExpandedChainId] = useState('');
  const [technicalChainId, setTechnicalChainId] = useState('');
  const [detailLoading, setDetailLoading] = useState('');
  const [pendingAction, setPendingAction] = useState<{ item: TaskChainItem; action: QbittorrentAction; preview: QbittorrentActionPreview } | null>(null);
  const [actionPreviewBusy, setActionPreviewBusy] = useState('');
  const [actionBusy, setActionBusy] = useState('');
  const [actionFeedback, setActionFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [activityCategory, setActivityCategory] = useState('');
  const [activities, setActivities] = useState<ActivityLogItem[]>([]);
  const [activityError, setActivityError] = useState('');
  const taskCardRefs = useRef(new Map<string, HTMLElement>());

  const focusActive = Boolean(target);
  const loadChain = async (signal: AbortSignal, offset = 0, append = false, refresh = false, invalidateDetails = false) => {
    setLoading(true);
    setError('');
    try {
      const payload = await getTaskChainV2({
        healthState: focusActive ? undefined : healthForFilter(filter),
        chainId: target?.chainId,
        targetKey: target?.targetKey,
        subscriptionId: target?.subscriptionId,
        tmdbId: target?.tmdbId,
        title: target?.title,
        seasonNumber: target?.seasonNumber ?? undefined,
        offset,
        limit: append ? 20 : pageLimit,
        refresh
      }, { signal });
      if (!signal.aborted) {
        if (append) setPageLimit((current) => Math.max(current, offset + payload.items.length));
        setChain((current) => append && current ? {
          ...payload,
          items: [
            ...current.items,
            ...payload.items.filter((item) => !current.items.some((existing) => existing.chainId === item.chainId))
          ]
        } : payload);
        if (invalidateDetails) setDetails({});
      }
    } catch (reason) {
      if (!signal.aborted) setError(reason instanceof Error ? reason.message : '任务链读取失败');
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  };

  const refreshChain = () => void loadChain(new AbortController().signal, 0, false, true, true);
  const loadMore = () => {
    const offset = chain?.page?.nextOffset;
    if (offset == null || loading) return;
    void loadChain(new AbortController().signal, offset, true);
  };

  usePolling(loadChain, 30000, { key: `${filter}:${JSON.stringify(target)}` });

  useEffect(() => {
    setPageLimit(20);
  }, [filter, target]);

  const loadActivities = async (signal: AbortSignal) => {
    try {
      const payload = await getActivityLogs(activityCategory, { signal });
      if (!signal.aborted) {
        setActivities(payload.logs);
        setActivityError('');
      }
    } catch {
      if (!signal.aborted) setActivityError('活动日志暂不可用');
    }
  };

  usePolling(loadActivities, 30000, { key: activityCategory });

  const items = chain?.items ?? [];
  const visible = items;
  const focusedTaskId = focusActive ? items[0]?.chainId || items[0]?.id || null : null;
  const counts = useMemo<Record<FilterName, number>>(() => ({
    全部: chain?.counts.total ?? 0,
    需要处理: chain?.healthCounts?.action_required ?? 0,
    证据不足: chain?.healthCounts?.evidence_insufficient ?? 0,
    等待: chain?.healthCounts?.waiting ?? 0,
    正常保护: chain?.healthCounts?.protected ?? 0,
    正常: chain?.healthCounts?.normal ?? 0
  }), [chain]);

  const completed115 = chain?.stageCounts?.cloud115?.done ?? 0;

  const loadDetail = async (item: TaskChainListItem) => {
    const chainId = item.chainId || '';
    const snapshotVersion = chain?.version ?? '';
    if (!chainId || details[chainId]?.snapshotVersion === snapshotVersion || detailLoading === chainId) return;
    setDetailLoading(chainId);
    try {
      const payload = await getTaskChainDetailV2(chainId);
      setDetails((current) => ({
        ...current,
        [chainId]: { snapshotVersion, item: payload.item }
      }));
    } catch (reason) {
      setActionFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '任务详情读取失败' });
    } finally {
      setDetailLoading('');
    }
  };

  const toggleDetail = (item: TaskChainListItem) => {
    const chainId = item.chainId || '';
    if (!chainId) return;
    const next = expandedChainId === chainId ? '' : chainId;
    setExpandedChainId(next);
    if (!next) setTechnicalChainId('');
    if (next) void loadDetail(item);
  };

  const copyTechnicalValue = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setActionFeedback({ tone: 'success', message: `已复制${label}` });
    } catch {
      setActionFeedback({ tone: 'error', message: `${label}复制失败，请检查浏览器剪贴板权限` });
    }
  };

  useEffect(() => {
    if (!expandedChainId || detailLoading === expandedChainId) return;
    const item = items.find((candidate) => candidate.chainId === expandedChainId);
    if (!item || details[expandedChainId]?.snapshotVersion === (chain?.version ?? '')) return;
    void loadDetail(item);
  }, [chain?.version, detailLoading, details, expandedChainId]);

  useEffect(() => {
    if (!target || !focusedTaskId) return;
    const card = taskCardRefs.current.get(focusedTaskId);
    if (!card) return;
    const item = items[0];
    if (item?.chainId) {
      setExpandedChainId(item.chainId);
      void loadDetail(item);
    }
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const frame = requestAnimationFrame(() => {
      card.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center', inline: 'nearest' });
      card.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusedTaskId, target]);
  const openTool = (url: string) => {
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  };

  const prepareQbAction = async (item: TaskChainItem, action: QbittorrentAction) => {
    setActionPreviewBusy(item.id);
    setActionFeedback(null);
    try {
      const preview = await previewQbittorrentAction({
        action,
        hashes: item.sourceIds.qbHashes,
        taskId: item.id,
        title: item.title
      });
      if (!preview.allowed) {
        setActionFeedback({
          tone: preview.reasonCode === 'QB_ALREADY_TARGET_STATE' ? 'success' : 'error',
          message: preview.reasonText
        });
        refreshChain();
        return;
      }
      setPendingAction({ item, action, preview });
    } catch (reason) {
      setActionFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'qBittorrent 操作预览失败' });
    } finally {
      setActionPreviewBusy('');
    }
  };

  const confirmQbAction = async () => {
    if (!pendingAction) return;
    const { item, action } = pendingAction;
    setActionBusy(item.id);
    setActionFeedback(null);
    try {
      const result = await runQbittorrentAction({
        action,
        hashes: item.sourceIds.qbHashes,
        taskId: item.id,
        title: item.title,
        idempotencyKey: pendingAction.preview.idempotencyKey
      });
      setActionFeedback({
        tone: result.confirmed ? 'success' : 'error',
        message: result.confirmed
          ? `${action === 'pause' ? '已暂停' : '已恢复'} ${result.succeeded} 个下载${result.skipped ? `，跳过 ${result.skipped} 个` : ''}`
          : '动作已提交，但最新状态尚未确认，请查看刷新后的任务链'
      });
      setPendingAction(null);
      refreshChain();
    } catch (reason) {
      setActionFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'qBittorrent 操作失败' });
      setPendingAction(null);
      refreshChain();
    } finally {
      setActionBusy('');
    }
  };

  return (
    <main className="work-page ops-page ops-page--tasks">
      <section className="ops-hero ops-hero--tasks">
        <div>
          <p className="ops-eyebrow">处理进度</p>
          <h1>任务中心</h1>
          <p className="ops-page-subtitle">媒体任务，现在进行到哪一步。</p>
          <p className="ops-deck">订阅、下载、进入 115 和整理入库集中显示；匹配依据和原工具入口放在任务详情中。</p>
        </div>
        <div className="ops-task-hero-status">
          <span>{counts['需要处理'] > 0 ? `${counts['需要处理']} 项需要处理` : counts['证据不足'] > 0 ? '部分任务证据不足' : counts['等待'] > 0 ? '任务正在处理' : chain ? '当前任务状态正常' : '正在读取任务状态'}</span>
          <strong>{chain?.services.qb.connected ? formatSpeed(chain.services.qb.downloadSpeed) : '下载器待连接'}</strong>
          <small>{chain ? `${chain.counts.active} 进行中 · ${chain.counts.blocked} 阻塞 · ${formatTimeAgo(chain.generatedAt)}` : '正在汇总任务证据'}</small>
        </div>
      </section>

      <section className="ops-task-summary" aria-label="任务状态摘要">
        <div><Rss size={16} /><span>已保存订阅</span><strong>{chain?.originCounts?.subscription ?? 0} 条主干</strong></div>
        <div><Download size={16} /><span>正在下载</span><strong>{chain ? `${chain.services.torra.total} 个订阅 · ${chain.services.qb.active} 个活跃下载` : '读取中'}</strong></div>
        <div><HardDrive size={16} /><span>已进入 115</span><strong>{completed115} 条有秒传或接管记录</strong></div>
        <div><Server size={16} /><span>整理与入库</span><strong>{chain ? `${chain.counts.completed} 个已完成` : '读取中'}</strong></div>
      </section>

      <section className="ops-panel ops-task-workbench">
        {focusActive && (
          <div className="ops-task-focus" role="status">
            <div>
              <strong>正在查看{target?.title ? `《${target.title}》` : '目标剧集'}的任务</strong>
              <span>{items.length > 0 ? `已定位 ${items.length} 条唯一链路` : '订阅已保存，但暂未形成关联任务'}</span>
            </div>
            <button className="tool-link" type="button" onClick={onClearTarget}>查看全部任务</button>
          </div>
        )}
        <header className="ops-task-toolbar">
          <div className="ops-task-tabs" role="tablist" aria-label="任务筛选">
            {filters.map((name) => (
              <button
                aria-selected={filter === name}
                className={filter === name ? 'ops-task-tab ops-task-tab--active' : 'ops-task-tab'}
                key={name}
                role="tab"
                tabIndex={filter === name ? 0 : -1}
                type="button"
                onClick={() => {
                  if (focusActive) onClearTarget();
                  setFilter(name);
                }}
                onKeyDown={handleHorizontalTabKeyDown}
              >
                {name}<span className={['需要处理', '证据不足'].includes(name) && counts[name] > 0 ? 'is-alert' : undefined}>{counts[name]}</span>
              </button>
            ))}
          </div>
          <div className="ops-task-toolbar__actions">
            <span>{chain ? `已显示 ${items.length} / ${chain.page?.total ?? chain.counts.total} 条 · ${formatTimeAgo(chain.generatedAt)}` : '正在读取统一任务链'}</span>
            <button aria-label="刷新任务链" aria-busy={loading} className="ops-icon-button" disabled={loading} title="刷新任务链" type="button" onClick={refreshChain}><RefreshCcw aria-hidden="true" size={16} /></button>
          </div>
        </header>

        {loading && !chain && <div className="ops-empty ops-task-empty">正在汇总下载、整理和入库状态…</div>}
        {!loading && error && <div className="ops-empty ops-task-empty">{error}</div>}
        {!loading && chain && visible.length === 0 && (
          <div className="ops-empty ops-task-empty">
            {focusActive ? '订阅已保存，但暂未形成关联任务。任务产生后会显示在这里。' : '这个筛选下暂时没有任务。'}
          </div>
        )}
        {actionFeedback && (
          <div className={`ops-task-action-feedback ops-task-action-feedback--${actionFeedback.tone}`} role="status">
            {actionFeedback.message}
          </div>
        )}

        <div className="ops-task-list">
          {visible.map((item) => {
            const chainId = item.chainId || item.id;
            const cachedDetail = details[chainId];
            const detail = cachedDetail?.snapshotVersion === (chain?.version ?? '') ? cachedDetail.item : undefined;
            const expanded = expandedChainId === chainId;
            const health = resolvedHealth(item);
            const stages = detail ? stageItems(detail) : [];
            const nextAction = recommendedAction(detail ?? item, health);
            return (
              <article
                className={`${health === 'action_required' ? 'ops-task-card ops-task-card--stuck' : 'ops-task-card'}${focusActive && chainId === focusedTaskId ? ' ops-task-card--focused' : ''}`}
                key={chainId}
                ref={(element) => {
                  if (element) taskCardRefs.current.set(chainId, element);
                  else taskCardRefs.current.delete(chainId);
                }}
                tabIndex={focusActive && chainId === focusedTaskId ? -1 : undefined}
              >
              <div className="ops-task-card__head">
                <div className="ops-task-card__status">
                  <HealthBadge state={health} />
                  <span className="ops-task-state">{stateLabel[item.state]}</span>
                </div>
                <div>
                  <h2>{item.title}</h2>
                  <p>
                    PT · {item.mediaType === 'movie' ? '电影' : item.mediaType === 'tv' ? `剧集${item.seasonNumber ? ` S${String(item.seasonNumber).padStart(2, '0')}` : ''}` : '未识别媒体'}
                    {' · '}{item.confidence === 'strong' ? '已精确匹配' : item.confidence === 'fallback' ? '按标题推测' : '尚未接到链路'}
                  </p>
                  <div className="ops-task-card__identity">
                    <span>目标 <strong>{targetLabel(item)}</strong></span>
                  </div>
                </div>
                <strong>{item.progress}%</strong>
              </div>

              {health !== 'normal' && (
                <div className={`ops-task-guidance ops-task-guidance--${health.replace('_', '-')}`} role={health === 'action_required' ? 'alert' : 'status'}>
                  {guidanceIcon(health)}
                  <div><strong>为什么</strong><span>{currentDetail(detail ?? item)}</span></div>
                  {nextAction && <div><strong>下一步</strong><span>{nextAction}</span></div>}
                </div>
              )}

              {expanded && detailLoading === chainId && <div className="ops-empty ops-task-empty">正在读取完整阶段证据…</div>}
              {expanded && detail && <div className="ops-task-chain" aria-label="任务证据链">
                {stages.map((stage, index) => (
                  <div className={stageClass(stage)} key={`${stage.stage}-${index}`}>
                    <div className="ops-task-chain__evidence">
                      <span>{stageStatusIcon(stage)}{String(index + 1).padStart(2, '0')} · {evidenceLabel(stage)}</span>
                      <em>{stageStatusLabel[stage.status] || stage.status}</em>
                    </div>
                    <strong>{stageDisplayLabel(stage)}</strong>
                    <small>{stage.reasonText || stageStatusLabel[stage.status] || '暂无阶段说明'}</small>
                    {stage.recommendedAction && <small className="ops-task-chain__next">下一步：{stage.recommendedAction}</small>}
                    {stage.healthState === 'action_required' && !stage.actions.preview && !stage.actions.retry && (
                      <small className="ops-task-chain__unavailable">当前没有可在 Fluxa 内安全执行的重试，请查看证据或打开原工具。</small>
                    )}
                    <div className="ops-task-chain__meta">
                      <span title={stage.reasonCode}>{stage.source || '未接入来源'}</span>
                      <time dateTime={stage.observedAt} title={stage.freshUntil ? `证据有效至 ${stage.freshUntil}` : undefined}>
                        {stage.observedAt ? formatTimeAgo(stage.observedAt) : '读取时间未知'}
                      </time>
                    </div>
                  </div>
                ))}
              </div>}

              {expanded && detail && (
                <div className="ops-task-technical">
                  <button
                    aria-expanded={technicalChainId === chainId}
                    className="ops-action-button"
                    type="button"
                    onClick={() => setTechnicalChainId((current) => current === chainId ? '' : chainId)}
                  >
                    <Braces aria-hidden="true" size={14} />技术详情
                  </button>
                  {technicalChainId === chainId && (
                    <dl aria-label={`${detail.title}技术详情`} className="ops-task-technical__list">
                      {[
                        ['媒体身份', detail.mediaKey || '未建立'],
                        ['目标身份', detail.targetKey || '未建立'],
                        ['链路身份', detail.chainId || detail.id],
                        ['订阅来源', detail.sourceIds.subscriptionIds?.join('\n') || detail.sourceIds.subscriptionId || '无'],
                        ['Torra 来源', detail.sourceIds.torraIds?.join('\n') || detail.sourceIds.torraId || '无'],
                        ['qB 任务', detail.sourceIds.qbHashes.join('\n') || '无'],
                        ['入库记录', detail.sourceIds.symediaIds.join('\n') || '无'],
                        ['相关文件', detail.artifactKeys?.join('\n') || '无']
                      ].map(([label, value]) => (
                        <div key={label}>
                          <dt>{label}</dt>
                          <dd><code>{value}</code></dd>
                          {value !== '无' && value !== '未建立' && (
                            <button
                              aria-label={`复制${label}`}
                              className="ops-icon-button ops-task-technical__copy"
                              title={`复制${label}`}
                              type="button"
                              onClick={() => void copyTechnicalValue(label, value)}
                            >
                              <Copy aria-hidden="true" size={13} />
                            </button>
                          )}
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              )}

              <div className="ops-task-progress" aria-label={`链路进度 ${item.progress}%`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.progress}>
                <span style={{ '--progress': `${item.progress}%` } as React.CSSProperties} />
              </div>

              <div className="ops-task-card__foot">
                <span>{item.relatedRecords && item.relatedRecords > 1 ? `已合并 ${item.relatedRecords} 条来源记录` : '唯一资源链路'}</span>
                <div className="ops-task-card__actions">
                  <button className="ops-action-button" disabled={detailLoading === chainId} type="button" onClick={() => toggleDetail(item)}>
                    {detailLoading === chainId ? '读取中' : expanded ? '收起详情' : '查看详情'}
                  </button>
                </div>
              </div>

              {expanded && detail && (detail.suggestion || detail.embyIndexed || detail.qbControl.total > 0) && (
                <footer className="ops-task-card__foot">
                  <span>{detail.embyIndexed ? 'Emby 已索引' : currentDetail(detail)}</span>
                  <div className="ops-task-card__actions">
                    {detail.qbControl.total > 0 && (
                      <button
                        className="ops-action-button ops-action-button--primary"
                        disabled={Boolean(actionBusy) || Boolean(actionPreviewBusy)}
                        type="button"
                        onClick={() => void prepareQbAction(detail, detail.qbControl.canPause ? 'pause' : 'resume')}
                      >
                        {detail.qbControl.canPause ? <Pause size={14} /> : <Play size={14} />}
                        {actionPreviewBusy === detail.id ? '正在预览' : actionBusy === detail.id ? '正在执行' : detail.qbControl.canPause ? '暂停下载' : '恢复下载'}
                      </button>
                    )}
                    {detail.suggestion && (
                      <button className="ops-action-button" disabled={!detail.suggestion.url || Boolean(actionBusy)} type="button" onClick={() => openTool(detail.suggestion!.url)}>
                        <ExternalLink size={14} />{detail.suggestion.label}
                      </button>
                    )}
                  </div>
                </footer>
              )}
              </article>
            );
          })}
        </div>
        {chain?.page?.hasMore && (
          <div className="ops-task-more">
            <span>已显示 {items.length} / {chain.page.total} 条</span>
            <button className="ops-action-button" disabled={loading} type="button" onClick={loadMore}>{loading ? '读取中' : '加载更多'}</button>
          </div>
        )}
      </section>

      <section className="ops-panel ops-activity-log">
        <header className="ops-task-toolbar">
          <div><small>操作记录</small><h2>最近活动</h2></div>
          <span>只读 · 最近 100 条</span>
        </header>
        <div className="ops-activity-filters" role="tablist" aria-label="活动类型">
          {activityFilters.map((item) => (
            <button
              aria-selected={activityCategory === item.key}
              className={activityCategory === item.key ? 'ops-task-tab ops-task-tab--active' : 'ops-task-tab'}
              key={item.key || 'all'}
              role="tab"
              tabIndex={activityCategory === item.key ? 0 : -1}
              type="button"
              onClick={() => setActivityCategory(item.key)}
              onKeyDown={handleHorizontalTabKeyDown}
            >
              {item.label}
            </button>
          ))}
        </div>
        {activityError && <div className="ops-empty">{activityError}</div>}
        {!activityError && activities.length === 0 && <div className="ops-empty">当前分类还没有活动记录。</div>}
        <div className="ops-activity-list">
          {activities.map((item, index) => (
            <article className={`ops-activity-item is-${item.status}`} key={`${item.ts}-${item.action}-${index}`}>
              <span><Activity size={13} /></span>
              <div>
                <strong>{item.message || activityActionLabels[item.action] || item.action}</strong>
                <small>
                  {activityCategoryLabels[item.category] || item.category} · {activityActionLabels[item.action] || item.action}
                  {typeof item.meta?.code === 'string' && ` · ${item.meta.code}`}
                  {typeof item.meta?.request_id === 'string' && ` · 请求 ${item.meta.request_id}`}
                </small>
              </div>
              <time>{item.time}</time>
            </article>
          ))}
        </div>
      </section>

      <ConfirmDialog
        busy={Boolean(actionBusy)}
        describedBy="qb-action-description"
        labelledBy="qb-action-title"
        open={Boolean(pendingAction)}
        onClose={() => setPendingAction(null)}
      >
        {pendingAction && (
          <>
            <span className="ops-confirm-dialog__signal">下载任务 · {pendingAction.action === 'pause' ? '暂停' : '恢复'}</span>
            <h2 id="qb-action-title">
              {pendingAction.action === 'pause' ? '暂停' : '恢复'} {pendingAction.preview.affected.eligible} 个关联下载？
            </h2>
            <p id="qb-action-description">
              {pendingAction.preview.reasonText}。
              操作完成后会重新读取真实状态并写入活动日志。
            </p>
            <div className="ops-confirm-dialog__meta">
              <span>媒体任务</span><strong>{pendingAction.item.title}</strong>
              <span>将要修改</span><strong>{pendingAction.preview.affected.eligible} 个</strong>
              <span>保持原状</span><strong>{pendingAction.preview.affected.skipped} 个</strong>
              <span>操作冷却</span><strong>{pendingAction.preview.cooldownSeconds > 0 ? `${pendingAction.preview.cooldownSeconds} 秒` : '无'}</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" disabled={Boolean(actionBusy)} type="button" onClick={() => setPendingAction(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus disabled={Boolean(actionBusy)} type="button" onClick={confirmQbAction}>
                {pendingAction.action === 'pause' ? <Pause size={14} /> : <Play size={14} />}
                {actionBusy ? '正在提交' : `确认${pendingAction.action === 'pause' ? '暂停' : '恢复'}`}
              </button>
            </div>
          </>
        )}
      </ConfirmDialog>
    </main>
  );
}
