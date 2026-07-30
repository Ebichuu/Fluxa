import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, Braces, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Clock3, Copy, Download, ExternalLink, Filter, Pause, Play, RefreshCcw, Rss, Server, ShieldCheck, X } from 'lucide-react';
import { getActivityLogs, getTaskChainDetailV2, getTaskChainV2, getTaskSummaryV2, previewQbittorrentAction, runQbittorrentAction } from '../../services/api';
import type { QbittorrentAction, QbittorrentActionPreview } from '../../types/qbittorrent';
import type { PipelineFact, PipelineOutcomeState, TaskChainHealthState, TaskChainItem, TaskChainListItem, TaskChainResponse, TaskChainStage, TaskProblemGroup } from '../../types/taskChain';
import type { ActivityLogItem } from '../../types/operations';
import { usePolling } from '../../hooks/usePolling';
import { currentHistoryEntryIs, writeUrlQuery } from '../../app/urlState';
import { formatSpeed } from '../../utils/formatters';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import { statisticDisplayValue, statisticScopeText } from '../../utils/statistics';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import type { AppNavigate, TaskNavigationTarget } from '../layout/AppTopNav';
import { RelativeTime } from '../status/RelativeTime';

type FilterName = '处理中' | '需要处理' | '已可播放' | '无需处理';

const filters: FilterName[] = ['处理中', '需要处理', '已可播放', '无需处理'];
const taskDetailHistoryKind = 'tasks:detail';

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
  private_rss_request: '种子库操作'
};

const stageStatusLabel: Record<string, string> = {
  done: '已完成',
  active: '处理中',
  blocked: '已阻塞',
  waiting: '等待中',
  unknown: '暂未确认'
};

function isUnknownStage(stage: TaskChainStage) {
  const status = stage.status.toLowerCase();
  return stage.healthState === 'evidence_insufficient' || status === 'unknown' || status.endsWith('_unknown');
}

function stageStatusText(status: string) {
  const normalized = status.toLowerCase();
  return stageStatusLabel[normalized] || (normalized.endsWith('_unknown') ? '暂未确认' : '状态暂未确认');
}

function resolvedHealth(item: TaskChainListItem | TaskChainItem): TaskChainHealthState {
  return ({
    playable: 'normal',
    action_required: 'action_required',
    in_progress: 'waiting',
    waiting: 'waiting',
    protected: 'protected',
    evidence_insufficient: 'evidence_insufficient'
  } as const)[resolvedOutcomeState(item)];
}

const pipelineStageLabels: Record<PipelineFact['stage'], string> = {
  torra: 'Torra 获取目标',
  qb: 'qB 下载',
  cloud115: '115 秒传',
  symedia: 'Symedia 整理入库',
  strm: 'STRM 播放入口',
  emby: 'Emby 可播放'
};

function pipelineStageItems(item: TaskChainItem): TaskChainStage[] {
  return (item.pipelineFacts ?? []).map((fact) => {
    const staleFailure = fact.state === 'failed' && fact.isStale;
    const staleCurrentState = fact.isStale && (fact.state === 'active' || fact.state === 'waiting');
    const staleState = staleFailure || staleCurrentState;
    const status = staleState ? 'unknown' : ({
      succeeded: 'done',
      active: 'active',
      failed: 'blocked',
      protected: 'blocked',
      not_applicable: 'done',
      waiting: 'waiting',
      unknown: 'unknown'
    } as const)[fact.state];
    const healthState: TaskChainHealthState = staleState || fact.state === 'unknown'
      ? 'evidence_insufficient'
      : fact.state === 'failed'
        ? fact.plannedRetryAt ? 'waiting' : 'action_required'
        : fact.state === 'protected'
          ? 'protected'
          : fact.state === 'succeeded' || fact.state === 'not_applicable'
            ? 'normal'
            : 'waiting';
    const historyText = staleFailure
      ? `曾于 ${fact.eventAt || fact.observedAt || '未知时间'} 失败 · 当前状态暂未确认`
      : staleCurrentState
        ? '当前状态证据已过期 · 状态暂未确认'
      : fact.reasonText;
    return {
      stage: fact.stage,
      label: pipelineStageLabels[fact.stage],
      status,
      healthState,
      evidence: fact.evidence,
      observedAt: fact.eventAt || fact.observedAt,
      freshUntil: fact.freshUntil,
      source: fact.source,
      reasonCode: fact.reasonCode,
      reasonText: historyText,
      recommendedAction: '',
      retryEligible: fact.retryEligible,
      plannedRetryAt: fact.plannedRetryAt,
      actions: { preview: false, retry: false }
    };
  });
}

function stageClass(stage: TaskChainStage) {
  if (stage.healthState === 'action_required' || stage.status === 'blocked') return 'ops-task-chain__step is-stuck';
  if (isUnknownStage(stage)) return 'ops-task-chain__step is-unknown';
  if (stage.healthState === 'protected') return 'ops-task-chain__step is-protected';
  if (stage.status === 'done') return 'ops-task-chain__step is-done';
  if (stage.status === 'active' || stage.status === 'waiting') return 'ops-task-chain__step is-now';
  return 'ops-task-chain__step is-unknown';
}

function evidenceLabel(stage: TaskChainStage) {
  if (stage.evidence === 'verified') return '已确认';
  if (stage.evidence === 'inferred') return '系统判断';
  return '暂未确认';
}

function stageDisplayLabel(stage: TaskChainStage) {
  if (stage.stage in pipelineStageLabels) return pipelineStageLabels[stage.stage as PipelineFact['stage']];
  if (stage.stage === 'download') return 'qB 下载';
  if (stage.stage === 'cloud115') return '115 接管';
  if (stage.stage === 'library') return '整理与入库';
  return stage.label;
}

function currentDetail(item: TaskChainListItem | TaskChainItem) {
  if (item.pipelineOutcome?.reasonText) return item.pipelineOutcome.reasonText;
  if (item.resultText) return item.resultText;
  if (item.userReasonText || item.reasonText) return item.userReasonText || item.reasonText;
  return item.userReasonText || item.reasonText || '展开后查看六阶段事实';
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

function problemGroupTarget(group: TaskProblemGroup) {
  if (group.mediaType === 'movie') return '整部电影';
  return group.seasonNumber > 0 ? `第 ${group.seasonNumber} 季` : '剧集范围未确认';
}

function episodeRangeText(values: number[]) {
  const episodes = Array.from(new Set(values.filter((value) => value > 0))).sort((a, b) => a - b);
  if (episodes.length === 0) return '集数范围未确认';
  const ranges: Array<[number, number]> = [];
  for (const episode of episodes) {
    const latest = ranges[ranges.length - 1];
    if (latest && episode === latest[1] + 1) latest[1] = episode;
    else ranges.push([episode, episode]);
  }
  return ranges.map(([start, end]) => (
    start === end ? `E${String(start).padStart(2, '0')}` : `E${String(start).padStart(2, '0')}-E${String(end).padStart(2, '0')}`
  )).join('、');
}

function problemStageLabel(stage: string) {
  return ({
    torra: 'Torra 获取',
    qb: 'qB 下载',
    cloud115: '115 秒传',
    symedia: 'Symedia 入库',
    strm: 'STRM',
    emby: 'Emby'
  } as Record<string, string>)[stage] ?? '处理阶段';
}

function stageStatusIcon(stage: TaskChainStage) {
  if (stage.healthState === 'action_required' || stage.status === 'blocked') return <AlertTriangle aria-hidden="true" size={14} />;
  if (isUnknownStage(stage)) return <CircleHelp aria-hidden="true" size={14} />;
  if (stage.healthState === 'protected') return <ShieldCheck aria-hidden="true" size={14} />;
  if (stage.status === 'done') return <CheckCircle2 aria-hidden="true" size={14} />;
  return <Clock3 aria-hidden="true" size={14} />;
}

function outcomeStatesForFilter(filter: FilterName): PipelineOutcomeState[] {
  if (filter === '需要处理') return ['action_required'];
  if (filter === '已可播放') return ['playable'];
  if (filter === '无需处理') return ['waiting', 'protected', 'evidence_insufficient'];
  return ['in_progress'];
}

function resolvedOutcomeState(item: TaskChainListItem | TaskChainItem): PipelineOutcomeState {
  return item.outcomeState ?? item.pipelineOutcome?.state ?? 'evidence_insufficient';
}

function filterForOutcome(value: PipelineOutcomeState): FilterName {
  if (value === 'action_required') return '需要处理';
  if (value === 'in_progress') return '处理中';
  if (value === 'playable') return '已可播放';
  return '无需处理';
}

function outcomeStateLabel(value: PipelineOutcomeState) {
  return ({
    action_required: '需要处理',
    in_progress: '处理中',
    playable: '已可播放',
    waiting: '等待中',
    protected: '已保护',
    evidence_insufficient: '暂未确认'
  } as const)[value];
}

function confirmedMediaResult(item: TaskChainListItem | TaskChainItem) {
  return item.mediaResult?.state && item.mediaResult.state !== 'unknown'
    ? item.mediaResult
    : undefined;
}

function residualIssueCount(item: TaskChainListItem | TaskChainItem) {
  return (item.residualIssues ?? []).reduce(
    (total, issue) => total + Math.max(1, issue.resourceCount || 1),
    0
  );
}

function residualIssueLabel(item: TaskChainListItem | TaskChainItem) {
  const issues = item.residualIssues ?? [];
  const count = residualIssueCount(item);
  const label = issues.length > 0 && issues.every((issue) => issue.stage === 'qb')
    ? '残留下载'
    : '遗留问题';
  return `另有 ${count} 个${label}需处理`;
}

function focusedOutcome(items: Array<TaskChainListItem | TaskChainItem>): PipelineOutcomeState | null {
  const states = items.map(resolvedOutcomeState);
  if (states.includes('action_required')) return 'action_required';
  if (states.includes('in_progress')) return 'in_progress';
  if (states.length > 0 && states.every((state) => state === 'playable')) return 'playable';
  return states[0] ?? null;
}

function filterCounts(payload: { outcomeCounts?: TaskChainResponse['outcomeCounts'] }): Record<FilterName, number> {
  return {
    处理中: payload.outcomeCounts?.in_progress ?? 0,
    需要处理: payload.outcomeCounts?.action_required ?? 0,
    已可播放: payload.outcomeCounts?.playable ?? 0,
    无需处理: (payload.outcomeCounts?.waiting ?? 0)
      + (payload.outcomeCounts?.protected ?? 0)
      + (payload.outcomeCounts?.evidence_insufficient ?? 0)
  };
}

function shanghaiTodayKey() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value || '';
  return `${value('year')}-${value('month')}-${value('day')}`;
}

export function TasksCenter({ target, onClearTarget, onNavigate }: { target: TaskNavigationTarget | null; onClearTarget: () => void; onNavigate: AppNavigate }) {
  const qbActiveView = target?.qbActive === true;
  const focusActive = Boolean(target && (
    target.chainId || target.targetKey || target.subscriptionId || target.tmdbId || target.title
  ));
  const initialOutcome = target?.outcomeState;
  const initialFilter = initialOutcome
    ? filterForOutcome(initialOutcome)
    : target?.archivedDate
      ? '无需处理'
      : target?.advanced && target.identityStates?.length
        ? '无需处理'
        : '处理中';
  const [filter, setFilter] = useState<FilterName>(initialFilter);
  const [filterReady, setFilterReady] = useState(Boolean(qbActiveView || focusActive || initialOutcome || target?.advanced || target?.archivedDate));
  const [chain, setChain] = useState<TaskChainResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pageLimit, setPageLimit] = useState(20);
  const [details, setDetails] = useState<Record<string, { snapshotVersion: string; item: TaskChainItem }>>({});
  const [expandedProblemGroupId, setExpandedProblemGroupId] = useState('');
  const [expandedChainId, setExpandedChainId] = useState('');
  const [technicalChainId, setTechnicalChainId] = useState('');
  const [detailLoading, setDetailLoading] = useState('');
  const [pendingAction, setPendingAction] = useState<{ item: TaskChainItem; action: QbittorrentAction; preview: QbittorrentActionPreview } | null>(null);
  const [actionPreviewBusy, setActionPreviewBusy] = useState('');
  const [actionBusy, setActionBusy] = useState('');
  const [actionFeedback, setActionFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [activityCategory, setActivityCategory] = useState(() => new URLSearchParams(window.location.search).get('activityCategory') ?? '');
  // 默认重点视图；raw 显示全部原始记录。
  const [activityView, setActivityView] = useState<'important' | 'raw'>(() => (
    new URLSearchParams(window.location.search).get('activityView') === 'raw' ? 'raw' : 'important'
  ));
  const [activities, setActivities] = useState<ActivityLogItem[]>([]);
  const [activityError, setActivityError] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(Boolean(target?.advanced));
  const [completedDate, setCompletedDate] = useState(target?.completedDate ?? '');
  const [archivedDate, setArchivedDate] = useState(target?.archivedDate ?? '');
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const advancedOpenRef = useRef(Boolean(target?.advanced));
  const taskCardRefs = useRef(new Map<string, HTMLElement>());
  const mobileFilterTriggerRef = useRef<HTMLButtonElement | null>(null);
  const mobileFilterSheetRef = useRef<HTMLElement | null>(null);
  // 快照差值只在相同全局口径（无定位、无分页追加、筛选未变）且 version 变化时计算。
  const snapshotRef = useRef<{ scopeKey: string; version: string; counts: Record<FilterName, number> } | null>(null);
  const [snapshotDelta, setSnapshotDelta] = useState<{ text: string; at: string } | null>(null);
  const todayKey = shanghaiTodayKey();

  const loadChain = async (signal: AbortSignal, offset = 0, append = false, refresh = false, invalidateDetails = false) => {
    setLoading(true);
    setError('');
    try {
      const payload = await getTaskChainV2({
        outcomeStates: focusActive || archivedDate || qbActiveView ? undefined : outcomeStatesForFilter(filter),
        completedDate: focusActive ? undefined : completedDate || undefined,
        archivedDate: focusActive ? undefined : archivedDate || undefined,
        qbActive: qbActiveView,
        identityStates: advancedOpen ? target?.identityStates : undefined,
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
        if (focusActive && !target?.outcomeState) {
          const focusedState = focusedOutcome(payload.items);
          if (focusedState) {
            const focusedFilter = filterForOutcome(focusedState);
            setFilter((current) => current === focusedFilter ? current : focusedFilter);
          }
        }
        if (append) setPageLimit((current) => Math.max(current, offset + payload.items.length));
        const scopeKey = `${filter}:${completedDate}:${archivedDate}:${advancedOpen}:${focusActive}:${qbActiveView}`;
        const nextVersion = payload.version ?? '';
        const nextCounts = filterCounts(payload);
        if (!append && !focusActive && nextVersion && nextCounts) {
          const previous = snapshotRef.current;
          if (previous && previous.scopeKey === scopeKey && previous.version !== nextVersion) {
            const deltas = filters
              .map((label) => {
                const diff = nextCounts[label] - previous.counts[label];
                if (diff === 0) return '';
                return `${label}${diff > 0 ? '增加' : '减少'} ${Math.abs(diff)}`;
              })
              .filter(Boolean);
            const migrations = payload.ledger?.artifactMigrations ?? 0;
            if (migrations > 0) deltas.push(`本轮已整理 ${migrations} 个历史产物身份`);
            if (deltas.length) setSnapshotDelta({ text: `较上次：${deltas.join('、')}`, at: payload.generatedAt });
          }
          snapshotRef.current = { scopeKey, version: nextVersion, counts: nextCounts };
        }
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

  usePolling(loadChain, 30000, { enabled: filterReady, key: `${filter}:${completedDate}:${archivedDate}:${advancedOpen}:${JSON.stringify(target)}` });

  useEffect(() => {
    setPageLimit(20);
  }, [archivedDate, completedDate, filter, target]);

  useEffect(() => {
    const nextAdvancedOpen = Boolean(target?.advanced);
    advancedOpenRef.current = nextAdvancedOpen;
    setAdvancedOpen(nextAdvancedOpen);
    setCompletedDate(target?.completedDate ?? '');
    setArchivedDate(target?.archivedDate ?? '');
    if (!(target?.chainId || target?.targetKey || target?.subscriptionId || target?.tmdbId || target?.title)) {
      setExpandedProblemGroupId('');
      setExpandedChainId('');
      setTechnicalChainId('');
    }

    if (qbActiveView) {
      setFilterReady(true);
    } else if (target?.outcomeState) {
      setFilter(filterForOutcome(target.outcomeState));
      setFilterReady(true);
    } else if (focusActive) {
      setFilterReady(true);
    } else if (target?.archivedDate) {
      setFilter('无需处理');
      setFilterReady(true);
    } else if (target?.advanced && target.identityStates?.length) {
      setFilter('无需处理');
      setFilterReady(true);
    } else {
      const urlOutcome = new URLSearchParams(window.location.search).get('outcomeState') as PipelineOutcomeState | null;
      if (urlOutcome && ['waiting', 'in_progress', 'protected', 'action_required', 'playable', 'evidence_insufficient'].includes(urlOutcome)) {
        setFilter(filterForOutcome(urlOutcome));
        setFilterReady(true);
        return undefined;
      }
      const controller = new AbortController();
      setFilterReady(false);
      setLoading(true);
      getTaskSummaryV2({ signal: controller.signal })
        .then((summary) => {
          if (controller.signal.aborted) return;
          const nextCounts = filterCounts(summary);
          const nextFilter = nextCounts['需要处理'] > 0
            ? '需要处理'
            : nextCounts['处理中'] > 0
              ? '处理中'
              : nextCounts['已可播放'] > 0
                ? '已可播放'
                : '无需处理';
          setFilter(nextFilter);
          writeUrlQuery({ outcomeState: outcomeStatesForFilter(nextFilter), userState: null }, 'replace');
          setFilterReady(true);
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          setError(reason instanceof Error ? reason.message : '任务摘要读取失败');
          setFilterReady(true);
        });
      return () => controller.abort();
    }
  }, [target]);

  useEffect(() => {
    if (!mobileFiltersOpen) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const frame = window.requestAnimationFrame(() => {
      mobileFilterSheetRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMobileFiltersOpen(false);
        return;
      }
      if (event.key !== 'Tab' || !mobileFilterSheetRef.current) return;
      const focusable = Array.from(
        mobileFilterSheetRef.current.querySelectorAll<HTMLElement>(focusableSelector)
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!mobileFilterSheetRef.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const wideViewport = window.matchMedia('(min-width: 701px)');
    const handleWideViewport = () => {
      if (wideViewport.matches) setMobileFiltersOpen(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    wideViewport.addEventListener('change', handleWideViewport);
    return () => {
      window.cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
      wideViewport.removeEventListener('change', handleWideViewport);
      (previouslyFocused ?? mobileFilterTriggerRef.current)?.focus({ preventScroll: true });
    };
  }, [mobileFiltersOpen]);

  const loadActivities = async (signal: AbortSignal) => {
    try {
      const payload = await getActivityLogs(activityCategory, { signal, view: activityView });
      if (!signal.aborted) {
        setActivities(payload.logs);
        setActivityError('');
      }
    } catch {
      if (!signal.aborted) setActivityError('活动日志暂不可用');
    }
  };

  usePolling(loadActivities, 30000, {
    enabled: !qbActiveView,
    key: `${activityCategory}:${activityView}:${qbActiveView}`
  });

  const changeActivityCategory = (key: string) => {
    setActivityCategory(key);
    writeUrlQuery({ activityCategory: key || null }, 'replace');
  };

  const changeActivityView = (view: 'important' | 'raw') => {
    setActivityView(view);
    writeUrlQuery({ activityView: view === 'raw' ? 'raw' : null }, 'replace');
  };

  const items = chain?.items ?? [];
  const visible = items;
  const problemGroups = chain?.problemGroups ?? [];
  const groupedProblemView = Boolean(
    !qbActiveView && !focusActive && !archivedDate && filter === '需要处理' && problemGroups.length > 0
  );
  const focusedTaskId = focusActive ? items[0]?.chainId || items[0]?.id || null : null;
  const counts = useMemo<Record<FilterName, number>>(() => ({
    ...filterCounts(chain ?? {})
  }), [chain]);

  const secupload = chain?.services.torra.secupload115;
  const latestUploadRun = secupload?.latestBatch ?? secupload?.latestRun;
  const identityPending = (chain?.identityCounts?.unidentified ?? 0) + (chain?.identityCounts?.conflict ?? 0);
  // systemIssue 深链时优先展示对应问题；无深链时只展示非 normal 的问题。
  const systemIssues = (chain?.systemIssues ?? []).filter((issue) => (
    target?.systemIssue ? issue.id === target.systemIssue : issue.state !== 'normal' && issue.state !== 'unknown'
  ));
  const secuploadSummary = !chain
    ? '读取中'
    : !secupload?.readable
      ? '插件状态不可读'
      : (secupload.activeRuns ?? 0) > 0
        ? `${secupload.activeRuns} 个分类运行中`
        : latestUploadRun?.counts.success != null || latestUploadRun?.counts.failed != null
          ? `最近 ${'runCount' in latestUploadRun ? `${latestUploadRun.runCount} 个分类 · ` : ''}成功 ${latestUploadRun.counts.success ?? 0} · 失败 ${latestUploadRun.counts.failed ?? 0}`
          : '插件已连接';
  const evidenceNotice = [
    identityPending > 0 ? `${identityPending} 条记录尚未形成唯一媒体身份，当前不据此判断秒传积压。` : '',
    secupload?.readable && !secupload.perFileEvidence ? '本次秒传记录没有文件级详情。' : ''
  ].filter(Boolean).join(' ');

  const loadDetail = async (item: TaskChainListItem): Promise<TaskChainItem | undefined> => {
    const chainId = item.chainId || '';
    const snapshotVersion = chain?.version ?? '';
    if (!chainId) return undefined;
    if (details[chainId]?.snapshotVersion === snapshotVersion) return details[chainId].item;
    if (detailLoading === chainId) return undefined;
    setDetailLoading(chainId);
    try {
      const payload = await getTaskChainDetailV2(chainId);
      setDetails((current) => ({
        ...current,
        [chainId]: { snapshotVersion, item: payload.item }
      }));
      return payload.item;
    } catch (reason) {
      setActionFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '任务详情读取失败' });
      return undefined;
    } finally {
      setDetailLoading('');
    }
  };

  const clearFocusedTarget = () => {
    onClearTarget();
    setExpandedChainId('');
    setTechnicalChainId('');
    writeUrlQuery({
      chainId: null,
      targetKey: null,
      subscriptionId: null,
      tmdbId: null,
      title: null,
      seasonNumber: null,
      mediaType: null,
      qbActive: null,
      outcomeState: outcomeStatesForFilter(filter),
      userState: null,
      completedDate: completedDate || null,
      archivedDate: archivedDate || null
    }, 'replace');
  };

  const changeFilter = (name: FilterName) => {
    if (target) onClearTarget();
    setFilter(name);
    setExpandedProblemGroupId('');
    setExpandedChainId('');
    setTechnicalChainId('');
    setCompletedDate('');
    setArchivedDate('');
    advancedOpenRef.current = false;
    setAdvancedOpen(false);
    writeUrlQuery({
      outcomeState: outcomeStatesForFilter(name),
      userState: null,
      completedDate: null,
      archivedDate: null,
      advanced: null,
      identityState: null,
      chainId: null,
      targetKey: null,
      subscriptionId: null,
      tmdbId: null,
      title: null,
      seasonNumber: null,
      mediaType: null,
      qbActive: null
    }, 'replace');
  };

  const changeCompletedDate = (next: string) => {
    if (target) onClearTarget();
    setCompletedDate(next);
    setArchivedDate('');
    if (next) setFilter('已可播放');
    setExpandedChainId('');
    setTechnicalChainId('');
    writeUrlQuery({
      outcomeState: next ? ['playable'] : outcomeStatesForFilter(filter),
      userState: null,
      completedDate: next || null,
      archivedDate: null,
      chainId: null,
      targetKey: null,
      subscriptionId: null,
      tmdbId: null,
      title: null,
      seasonNumber: null,
      mediaType: null,
      qbActive: null
    }, 'replace');
  };

  const changeAdvancedVisibility = (next: boolean) => {
    advancedOpenRef.current = next;
    setAdvancedOpen(next);
    writeUrlQuery({ advanced: next }, 'replace');
  };

  const clearQbActiveView = () => {
    onClearTarget();
    writeUrlQuery({
      qbActive: null,
      outcomeState: outcomeStatesForFilter(filter),
      userState: null,
    }, 'replace');
  };

  const toggleDetail = (item: TaskChainListItem) => {
    const chainId = item.chainId || '';
    if (!chainId) return;
    const next = expandedChainId === chainId ? '' : chainId;
    setExpandedChainId(next);
    if (!next) {
      setTechnicalChainId('');
      if (currentHistoryEntryIs(taskDetailHistoryKind)) {
        window.history.back();
      } else if (focusActive) {
        clearFocusedTarget();
      } else {
        writeUrlQuery({ chainId: null, targetKey: null }, 'replace');
      }
      return;
    }
    writeUrlQuery({ chainId, targetKey: item.targetKey || null }, 'push', taskDetailHistoryKind);
    void loadDetail(item);
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
    setActionFeedback({ tone: 'success', message: '正在检查操作条件和影响范围…' });
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
    setActionFeedback({ tone: 'success', message: '操作已收到，正在等待 qBittorrent 返回执行结果…' });
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

  const executePrimaryAction = async (item: TaskChainListItem) => {
    const action = item.primaryAction;
    if (!action?.available || action.kind === 'none') return;
    if (action.kind === 'open_qb') {
      openTool(chain?.services.qb.webUrl || '');
      return;
    }
    if (action.kind === 'open_torra') {
      openTool(chain?.services.torra.webUrl || '');
      return;
    }
    if (action.kind === 'view_details') {
      toggleDetail(item);
      return;
    }
    if (action.kind === 'pause_download' || action.kind === 'resume_download') {
      const detail = await loadDetail(item);
      if (detail) await prepareQbAction(detail, action.kind === 'pause_download' ? 'pause' : 'resume');
      return;
    }
    toggleDetail(item);
  };

  return (
    <main className="work-page ops-page ops-page--tasks">
      <section className="ops-hero ops-hero--tasks ops-hero--compact">
        <div>
          <p className="ops-eyebrow">{qbActiveView ? '下载器快照' : '处理进度'}</p>
          <h1>{qbActiveView ? 'qB 当前活跃任务' : '任务中心'}</h1>
          <p className="ops-page-subtitle">
            {qbActiveView
              ? chain?.services.qb.connected
                ? `qB 当前活跃任务 ${chain.services.qb.active} 个 · ${chain.page?.total ?? items.length} 条媒体链`
                : 'qB 当前活跃任务未知'
              : `${counts['需要处理']} 项需要处理 · ${counts['处理中']} 个处理中`}
          </p>
        </div>
        {qbActiveView ? (
          <div className="ops-task-hero-status">
            <span>{chain?.services.qb.connected ? 'qB 当前快照' : chain ? 'qB 活跃任务未知' : '正在读取 qB 状态'}</span>
            {chain?.services.qb.connected && chain.services.qb.downloadSpeed > 0 && <strong>{formatSpeed(chain.services.qb.downloadSpeed)}</strong>}
            <small>{chain ? <>最近读取 <RelativeTime value={chain.generatedAt} /></> : '正在读取下载器当前快照'}</small>
          </div>
        ) : (
          <div className="ops-task-hero-status">
            <span>{counts['需要处理'] > 0 ? `${counts['需要处理']} 项需要处理` : counts['处理中'] > 0 ? '任务正在处理' : chain ? '当前没有需要介入的问题' : '正在读取任务状态'}</span>
            {chain?.services.qb.connected && chain.services.qb.downloadSpeed > 0 && <strong>{formatSpeed(chain.services.qb.downloadSpeed)}</strong>}
            <small>{chain ? <>{counts['已可播放']} 个已可播放 · {counts['无需处理']} 个无需处理 · <RelativeTime value={chain.generatedAt} /></> : '正在汇总任务结果'}</small>
          </div>
        )}
      </section>

      {!qbActiveView && (
        <section className="ops-task-summary" aria-label="任务状态摘要">
          <div><Download size={16} /><span>处理中</span><strong>{counts['处理中']}<em>个</em></strong></div>
          <div><AlertTriangle size={16} /><span>需要处理</span><strong>{counts['需要处理']}<em>项</em></strong></div>
          <div><Server size={16} /><span>已可播放</span><strong>{statisticDisplayValue(counts['已可播放'], chain?.statisticsMeta?.playable)}{chain?.statisticsMeta?.playable?.confirmation !== 'unknown' && <em>个</em>}</strong></div>
          <div><ShieldCheck size={16} /><span>无需处理</span><strong>{counts['无需处理']}<em>个</em></strong></div>
        </section>
      )}

      {!qbActiveView && chain && (
        <p className="ops-statistic-scope">已可播放统计：{statisticScopeText(chain.statisticsMeta?.playable, '当前唯一任务链')}</p>
      )}

      {!qbActiveView && (loading || snapshotDelta) && (
        <p className="ops-task-snapshot" role="status">
          {loading ? '数据更新中…' : snapshotDelta && <>
            {snapshotDelta.text} · 最近快照 <RelativeTime value={snapshotDelta.at} />
          </>}
        </p>
      )}

      {!qbActiveView && systemIssues.length > 0 && (
        <section aria-label="系统问题" className={target?.systemIssue ? 'ops-system-issues ops-system-issues--focused' : 'ops-system-issues'}>
          {systemIssues.map((issue) => {
            const stateLabel = issue.state === 'recovering' ? '正在自动恢复' : issue.state === 'action_required' ? '需要处理' : issue.state === 'normal' ? '正常' : '状态未知';
            return (
              <article className={`ops-system-issue ops-system-issue--${issue.state}`} key={issue.id}>
                <header>
                  <strong>Torra 秒传 · {stateLabel}</strong>
                  {(issue.failedTotal ?? 0) > 0 && <span>本批失败 {issue.failedTotal}</span>}
                </header>
                {issue.categories.map((category) => (
                  <div className="ops-system-issue__category" key={category.id}>
                    <strong>{category.label}{issue.state === 'recovering' ? ' 正在自动恢复' : ''}</strong>
                    {category.latest.failed != null && <span>本批失败 {category.latest.failed}</span>}
                    {category.recentFailedCounts.length > 0 && <span>近三批失败数：{category.recentFailedCounts.join(' → ')}</span>}
                    {issue.nextRunAt && issue.state === 'recovering' && <span>下次自动重试：<RelativeTime value={issue.nextRunAt} /></span>}
                    {category.retryPolicyText && <span>{category.retryPolicyText}</span>}
                    {(issue.files ?? []).some((file) => file.categoryId === category.id) && (
                      <ul className="ops-system-issue__files" aria-label={`${category.label}失败文件`}>
                        {(issue.files ?? []).filter((file) => file.categoryId === category.id).map((file) => (
                          <li key={file.ref}>
                            <strong title={file.displayName}>{file.displayName}</strong>
                            <span>{file.errorLabel}{file.retryCount == null ? ' · 重试次数暂未确认' : ` · 已重试 ${file.retryCount} 次`}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
                {issue.primaryAction && issue.state !== 'normal' && (
                  <span className="ops-system-issue__action">{issue.primaryAction.label}</span>
                )}
                {issue.evidenceLimitText && <small>{issue.evidenceLimitText}</small>}
              </article>
            );
          })}
        </section>
      )}

      {!qbActiveView && (identityPending > 0 || (secupload?.readable && !secupload.perFileEvidence)) && (
        <details
          className="ops-task-diagnostics"
          open={advancedOpen}
          onToggle={(event) => {
            const next = event.currentTarget.open;
            if (next !== advancedOpenRef.current) changeAdvancedVisibility(next);
          }}
        >
          <summary>高级诊断 · {identityPending + (secupload?.readable && !secupload.perFileEvidence ? 1 : 0)} 项</summary>
          <div>
            <strong>{identityPending > 0 ? `${identityPending} 条记录尚未完成身份整理` : 'Torra 秒传证据能力'}</strong>
            <span>{evidenceNotice}</span>
            <small>{secuploadSummary}{secupload?.lastRunAt && <> · 最近运行 <RelativeTime value={secupload.lastRunAt} /></>}</small>
          </div>
        </details>
      )}

      <section className="ops-panel ops-task-workbench">
        {qbActiveView && (
          <div className="ops-task-focus" role="status">
            <div>
              <strong>{chain?.services.qb.connected ? `qB 当前活跃任务 ${chain.services.qb.active} 个` : 'qB 当前活跃任务未知'}</strong>
              <span>{chain ? `已关联为 ${chain.page?.total ?? items.length} 条媒体链；同一目标的并发任务仍合并显示` : '正在读取下载器当前快照'}</span>
            </div>
            <button className="tool-link" type="button" onClick={clearQbActiveView}>查看全部任务</button>
          </div>
        )}
        {focusActive && (
          <div className="ops-task-focus" role="status">
            <div>
              <strong>正在查看{target?.title ? `《${target.title}》` : '目标剧集'}的任务</strong>
              <span>{items.length > 0 ? `已定位 ${items.length} 条唯一链路` : '订阅已保存，但暂未形成关联任务'}</span>
            </div>
            <button className="tool-link" type="button" onClick={clearFocusedTarget}>查看全部任务</button>
          </div>
        )}
        <header className="ops-task-toolbar">
          {!qbActiveView && (
            <div className="ops-mobile-filter-summary">
              <span><small>当前筛选</small><strong>{archivedDate ? `归档 ${archivedDate}` : `${filter}${completedDate ? ' · 今日可播放' : ''}`}{advancedOpen ? ' · 高级诊断' : ''}</strong></span>
              <button
                aria-controls="task-mobile-filter-sheet"
                aria-expanded={mobileFiltersOpen}
                className="ops-mobile-filter-button"
                ref={mobileFilterTriggerRef}
                type="button"
                onClick={() => setMobileFiltersOpen(true)}
              >
                <Filter aria-hidden="true" size={15} />筛选
              </button>
            </div>
          )}
          {!qbActiveView && (
            <div className="ops-task-tabs" role="tablist" aria-label="任务筛选">
              {filters.map((name) => (
                <button
                  aria-selected={filter === name}
                  className={filter === name ? 'ops-task-tab ops-task-tab--active' : 'ops-task-tab'}
                  key={name}
                  role="tab"
                  tabIndex={filter === name ? 0 : -1}
                  type="button"
                  onClick={() => changeFilter(name)}
                  onKeyDown={handleHorizontalTabKeyDown}
                >
                  {name}<span className={name === '需要处理' && counts[name] > 0 ? 'is-alert' : undefined}>{counts[name]}</span>
                </button>
              ))}
            </div>
          )}
          <div className="ops-task-toolbar__actions">
            <span>{chain ? groupedProblemView
              ? <>{problemGroups.length} 个问题组 · 涉及 {chain.problemGroupSummary?.actionRequiredResources ?? chain.page?.total ?? items.length} 个资源 · <RelativeTime value={chain.generatedAt} /></>
              : <>已显示 {items.length} / {chain.page?.total ?? chain.counts.total} 条 · <RelativeTime value={chain.generatedAt} /></>
              : qbActiveView ? '正在读取 qB 活跃任务' : '正在读取统一任务链'}</span>
            {!qbActiveView && <button className="tool-link ops-task-advanced-link" type="button" onClick={() => changeAdvancedVisibility(!advancedOpenRef.current)}><Braces aria-hidden="true" size={14} />高级诊断</button>}
            {!qbActiveView && <button aria-label="打开 RSS 种子库" className="ops-icon-button" title="RSS 种子库" type="button" onClick={() => onNavigate('rss-library')}><Rss aria-hidden="true" size={14} /></button>}
            <button aria-label="刷新任务链" aria-busy={loading} className="ops-icon-button" disabled={loading} title="刷新任务链" type="button" onClick={refreshChain}><RefreshCcw aria-hidden="true" size={16} /></button>
          </div>
        </header>

        {chain?.archiveSummary && (
          <div className="ops-task-archive-summary" aria-label={`${chain.archiveSummary.date} 归档统计`}>
            <span><small>归档文件</small><strong>{chain.archiveSummary.archivedFiles}</strong></span>
            <span><small>已关联文件</small><strong>{chain.archiveSummary.linkedFiles}</strong></span>
            <span><small>关联任务</small><strong>{chain.archiveSummary.linkedTasks}</strong></span>
            <span><small>未关联文件</small><strong>{chain.archiveSummary.unlinkedFiles}</strong></span>
          </div>
        )}

        {loading && !chain && <div className="ops-empty ops-task-empty">{qbActiveView ? '正在读取 qB 活跃任务…' : '正在汇总下载、整理和可播放状态…'}</div>}
        {!loading && error && <div className="ops-empty ops-task-empty">{error}</div>}
        {!loading && chain && visible.length === 0 && problemGroups.length === 0 && (
          <div className="ops-empty ops-task-empty">
            {qbActiveView
              ? '当前没有可验证的 qB 活跃任务。'
              : focusActive
              ? '订阅已保存，但暂未形成关联任务。任务产生后会显示在这里。'
              : archivedDate
                ? '这一天没有可关联到规范任务的归档文件；未关联文件仍计入上方统计。'
                : '这个筛选下暂时没有任务。'}
          </div>
        )}
        {actionFeedback && (
          <div className={`ops-task-action-feedback ops-task-action-feedback--${actionFeedback.tone}`} role="status">
            {actionFeedback.message}
          </div>
        )}

        {groupedProblemView && (
          <div className="ops-problem-group-list" aria-label="需要处理的问题组">
            {problemGroups.map((group) => {
              const expanded = expandedProblemGroupId === group.groupId;
              return (
                <article className="ops-problem-group" key={group.groupId}>
                  <button
                    aria-expanded={expanded}
                    className="ops-problem-group__toggle"
                    type="button"
                    onClick={() => setExpandedProblemGroupId((current) => current === group.groupId ? '' : group.groupId)}
                  >
                    <span className="ops-problem-group__signal" aria-hidden="true"><AlertTriangle size={16} /></span>
                    <span className="ops-problem-group__copy">
                      <strong>{group.title}{group.mediaType === 'tv' && group.seasonNumber > 0 ? ` · S${String(group.seasonNumber).padStart(2, '0')}` : ''}</strong>
                      <small>{group.reasonText || '当前任务需要处理'}</small>
                      <em>{problemStageLabel(group.stage)} · {problemGroupTarget(group)} · {episodeRangeText(group.episodeNumbers)}</em>
                    </span>
                    <span className="ops-problem-group__counts">
                      <strong>{group.resourceCount}</strong><small>个资源</small>
                      {group.identityUnconfirmedResources > 0 && <em>{group.identityUnconfirmedResources} 条身份未确认</em>}
                    </span>
                    {expanded ? <ChevronDown aria-hidden="true" size={17} /> : <ChevronRight aria-hidden="true" size={17} />}
                  </button>
                  {expanded && (
                    <div className="ops-problem-group__members">
                      {group.members.map((member) => (
                        <div className="ops-problem-group__member" key={member.chainId}>
                          <span>
                            <strong>{member.mediaType === 'movie' ? '整部电影' : member.episodeNumber > 0 ? `S${String(member.seasonNumber).padStart(2, '0')}E${String(member.episodeNumber).padStart(2, '0')}` : problemGroupTarget(group)}</strong>
                            <small>{member.resultText || member.userReasonText || member.reasonText || group.reasonText}</small>
                          </span>
                          <button
                            className="ops-action-button"
                            type="button"
                            onClick={() => onNavigate('tasks', {
                              chainId: member.chainId,
                              targetKey: member.targetKey,
                              title: member.title,
                              outcomeState: 'action_required'
                            })}
                          >
                            查看任务<ChevronRight aria-hidden="true" size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}

        {!groupedProblemView && <div className="ops-task-list">
          {visible.map((item) => {
            const chainId = item.chainId || item.id;
            const cachedDetail = details[chainId];
            const detail = cachedDetail?.snapshotVersion === (chain?.version ?? '') ? cachedDetail.item : undefined;
            const expanded = expandedChainId === chainId;
            const health = resolvedHealth(item);
            const outcomeState = resolvedOutcomeState(item);
            const mediaResult = confirmedMediaResult(item);
            const residualCount = residualIssueCount(item);
            const separatedResidual = Boolean(mediaResult && residualCount > 0);
            const showMediaResult = Boolean(mediaResult && (outcomeState !== 'action_required' || separatedResidual));
            const residualIssue = item.residualIssues?.[0];
            const stages = detail ? pipelineStageItems(detail) : [];
            const primaryAction = item.primaryAction;
            const detailsArePrimaryAction = Boolean(
              primaryAction?.available && primaryAction.kind === 'view_details'
            );
            return (
              <article
                className={`${outcomeState === 'action_required' && !separatedResidual ? 'ops-task-card ops-task-card--stuck' : 'ops-task-card'}${focusActive && chainId === focusedTaskId ? ' ops-task-card--focused' : ''}`}
                key={chainId}
                ref={(element) => {
                  if (element) taskCardRefs.current.set(chainId, element);
                  else taskCardRefs.current.delete(chainId);
                }}
                tabIndex={focusActive && chainId === focusedTaskId ? -1 : undefined}
              >
              <div className="ops-task-card__head">
                <div className="ops-task-card__status">
                  <span className={`ops-task-state ${showMediaResult ? 'ops-task-state--media-result' : `ops-task-state--${outcomeState.replace(/_/g, '-')}`}`}>
                    {showMediaResult ? mediaResult?.resultText : outcomeStateLabel(outcomeState)}
                  </span>
                </div>
                <div>
                  <h2>{item.title}</h2>
                  <p className="ops-task-card__result">
                    {showMediaResult ? mediaResult?.resultText : item.resultText || currentDetail(detail ?? item)}
                  </p>
                  {residualCount > 0 && <p className="ops-task-card__residual">{residualIssueLabel(item)}</p>}
                  <p className="ops-task-card__meta-line">
                    PT · {item.mediaType === 'movie' ? '电影' : item.mediaType === 'tv' ? `剧集${item.seasonNumber ? ` S${String(item.seasonNumber).padStart(2, '0')}` : ''}` : '未识别媒体'}
                  </p>
                  <div className="ops-task-card__identity">
                    <span>目标 <strong>{targetLabel(item)}</strong></span>
                    {(item.qbControl.active ?? item.concurrentDownloadCount ?? item.activeDownloadTasks ?? 0) > 1 && (
                      <span>并发下载 <strong>同一目标有 {item.qbControl.active ?? item.concurrentDownloadCount ?? item.activeDownloadTasks} 个 qB 任务同时下载</strong></span>
                    )}
                  </div>
                </div>
                <strong>已确认 {item.confirmedStageCount ?? 0}/6</strong>
              </div>

              {residualCount > 0 && (
                <div className="ops-task-guidance ops-task-guidance--residual" role="status">
                  <AlertTriangle aria-hidden="true" size={16} />
                  <div><strong>遗留问题</strong><span>{residualIssue?.reasonText || residualIssueLabel(item)}</span></div>
                  <div><strong>建议处理</strong><span>{primaryAction?.label || '展开查看对应阶段证据'}</span></div>
                </div>
              )}

              {outcomeState === 'action_required' && residualCount === 0 && (
                <div className="ops-task-guidance ops-task-guidance--action-required" role="alert">
                  {guidanceIcon(health)}
                  <div><strong>发生了什么</strong><span>{currentDetail(detail ?? item)}</span></div>
                  <div><strong>建议处理</strong><span>{primaryAction?.label || '查看当前处理进度'}</span></div>
                </div>
              )}

              {expanded && detailLoading === chainId && <div className="ops-empty ops-task-empty">正在读取完整处理进度…</div>}
              {expanded && detail && stages.length > 0 && <div className="ops-task-chain" aria-label="六阶段任务事实">
                {stages.map((stage, index) => (
                  <div className={stageClass(stage)} key={`${stage.stage}-${index}`}>
                    <div className="ops-task-chain__evidence">
                      <span>{stageStatusIcon(stage)}{String(index + 1).padStart(2, '0')} · {evidenceLabel(stage)}</span>
                      <em>{stageStatusText(stage.status)}</em>
                    </div>
                    <strong>{stageDisplayLabel(stage)}</strong>
                    <small>{stage.userReasonText || stage.reasonText || stageStatusText(stage.status)}</small>
                    {stage.recommendedAction && <small className="ops-task-chain__next">下一步：{stage.recommendedAction}</small>}
                    {stage.healthState === 'action_required' && !stage.actions.preview && !stage.actions.retry && (
                      <small className="ops-task-chain__unavailable">当前没有可在 Fluxa 内安全执行的重试，请查看证据或打开原工具。</small>
                    )}
                    <div className="ops-task-chain__meta">
                      <span>{stage.source || '未接入来源'}</span>
                      <RelativeTime value={stage.observedAt} fallback="读取时间未知" />
                    </div>
                  </div>
                ))}
              </div>}

              {expanded && detail && stages.length === 0 && (
                <div className="ops-empty ops-task-empty">六阶段事实尚未返回</div>
              )}

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
                        ['相关文件', detail.artifactKeys?.join('\n') || '无'],
                        ['阶段代码', detail.stages?.map((stage) => `${stageDisplayLabel(stage)}：${stage.reasonCode || stage.status || '无'}`).join('\n') || '无'],
                        ['阶段原始原因', detail.stages?.map((stage) => `${stageDisplayLabel(stage)}：${stage.technicalReasonText || '无'}`).join('\n') || '无']
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

              <div className="ops-task-card__foot">
                <span>{item.relatedRecords && item.relatedRecords > 1 ? `已合并 ${item.relatedRecords} 条来源记录` : item.playableAt ? <>可播放于 <RelativeTime value={item.playableAt} /></> : '唯一资源链路'}</span>
                <div className="ops-task-card__actions">
                  <button
                    className={detailsArePrimaryAction ? 'ops-action-button ops-action-button--primary' : 'ops-action-button'}
                    disabled={detailLoading === chainId}
                    type="button"
                    onClick={() => toggleDetail(item)}
                  >
                    {detailLoading === chainId ? '读取中' : expanded ? '收起详情' : '查看详情'}
                  </button>
                  {primaryAction?.available && primaryAction.kind !== 'none' && primaryAction.kind !== 'view_details' && (
                    <button
                      className="ops-action-button ops-action-button--primary"
                      disabled={Boolean(actionBusy) || Boolean(actionPreviewBusy) || detailLoading === chainId}
                      type="button"
                      onClick={() => void executePrimaryAction(item)}
                    >
                      {primaryAction.kind === 'pause_download' ? <Pause size={14} /> : primaryAction.kind === 'resume_download' ? <Play size={14} /> : <ExternalLink size={14} />}
                      {primaryAction.label}
                    </button>
                  )}
                </div>
              </div>

              {expanded && detail?.suggestion && !primaryAction?.available && (
                <footer className="ops-task-card__foot">
                  <span>更多操作</span>
                  <div className="ops-task-card__actions">
                    <button className="ops-action-button" disabled={!detail.suggestion.url || Boolean(actionBusy)} type="button" onClick={() => openTool(detail.suggestion!.url)}>
                      <ExternalLink size={14} />{detail.suggestion.label}
                    </button>
                  </div>
                </footer>
              )}
              </article>
            );
          })}
        </div>}
        {!groupedProblemView && chain?.page?.hasMore && (
          <div className="ops-task-more">
            <span>已显示 {items.length} / {chain.page.total} 条</span>
            <button className="ops-action-button" disabled={loading} type="button" onClick={loadMore}>{loading ? '读取中' : '加载更多'}</button>
          </div>
        )}
      </section>

      {!qbActiveView && <section className="ops-panel ops-activity-log">
        <header className="ops-task-toolbar">
          <div><small>操作记录</small><h2>最近活动</h2></div>
          <span className="ops-activity-view-toggle">
            <button className={activityView === 'important' ? 'tool-link is-active' : 'tool-link'} type="button" onClick={() => changeActivityView('important')}>重点</button>
            <button className={activityView === 'raw' ? 'tool-link is-active' : 'tool-link'} type="button" onClick={() => changeActivityView('raw')}>全部原始记录</button>
          </span>
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
              onClick={() => changeActivityCategory(item.key)}
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
                <strong>{item.message || activityActionLabels[item.action] || '系统操作'}</strong>
                <small>
                  {activityCategoryLabels[item.category] || '系统'} · {activityActionLabels[item.action] || '系统操作'}
                  {(item.repeatCount ?? 0) > 1 && ` · 重复 ${item.repeatCount} 次`}
                </small>
                {advancedOpen && (typeof item.meta?.code === 'string' || typeof item.meta?.request_id === 'string') && (
                  <small className="ops-activity-item__technical">
                    技术字段：{typeof item.meta?.code === 'string' ? item.meta.code : '无'}
                    {typeof item.meta?.request_id === 'string' && ` · 请求 ${item.meta.request_id}`}
                  </small>
                )}
              </div>
              <time>{item.time}</time>
            </article>
          ))}
        </div>
      </section>}

      {!qbActiveView && mobileFiltersOpen && (
        <div className="ops-filter-sheet-backdrop" onPointerDown={(event) => {
          if (event.target === event.currentTarget) setMobileFiltersOpen(false);
        }}>
          <section
            aria-labelledby="task-mobile-filter-title"
            aria-modal="true"
            className="ops-filter-sheet"
            id="task-mobile-filter-sheet"
            ref={mobileFilterSheetRef}
            role="dialog"
            tabIndex={-1}
          >
            <div aria-hidden="true" className="ops-filter-sheet__handle" />
            <header className="ops-filter-sheet__header">
              <div><small>任务中心</small><h2 id="task-mobile-filter-title">筛选任务</h2></div>
              <button aria-label="关闭筛选" className="ops-filter-sheet__close" type="button" onClick={() => setMobileFiltersOpen(false)}><X aria-hidden="true" size={18} /></button>
            </header>
            <div className="ops-filter-sheet__body">
              <fieldset className="ops-filter-sheet__group">
                <legend>处理状态</legend>
                <div className="ops-filter-sheet__options">
                  {filters.map((name) => (
                    <button aria-pressed={filter === name} className={filter === name ? 'is-active' : undefined} key={name} type="button" onClick={() => changeFilter(name)}>
                      <span>{name}</span><strong>{counts[name]}</strong>
                    </button>
                  ))}
                </div>
              </fieldset>
              <fieldset className="ops-filter-sheet__group">
                <legend>可播放时间</legend>
                <div className="ops-filter-sheet__options ops-filter-sheet__options--two">
                  <button aria-pressed={!completedDate} className={!completedDate ? 'is-active' : undefined} type="button" onClick={() => changeCompletedDate('')}>全部时间</button>
                  <button aria-pressed={completedDate === todayKey} className={completedDate === todayKey ? 'is-active' : undefined} type="button" onClick={() => changeCompletedDate(todayKey)}>今日可播放</button>
                </div>
              </fieldset>
              <fieldset className="ops-filter-sheet__group">
                <legend>高级项</legend>
                <button
                  aria-pressed={advancedOpen}
                  className={advancedOpen ? 'ops-filter-sheet__switch is-active' : 'ops-filter-sheet__switch'}
                  type="button"
                  onClick={() => changeAdvancedVisibility(!advancedOpenRef.current)}
                >
                  <span><strong>高级诊断</strong><small>显示身份整理与 Torra 证据能力</small></span>
                  <i aria-hidden="true" />
                </button>
              </fieldset>
            </div>
            <footer className="ops-filter-sheet__footer">
              <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => setMobileFiltersOpen(false)}>完成</button>
            </footer>
          </section>
        </div>
      )}

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
