import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Download,
  Edit3,
  Plus,
  PanelRightOpen,
  RefreshCcw,
  Search,
  Send,
  ServerCog,
  Trash2,
  X
} from 'lucide-react';
import {
  backfillRssIdentities,
  deleteRssSource,
  getAutomationAction,
  getRssSeedItem,
  getRssSeedItems,
  getRssMatches,
  getRssSources,
  runRssMatcher,
  saveRssSource,
  startRssMatchAnalysis,
  startRssMatchDownload,
  testRssSource
} from '../../services/api';
import { writeUrlQuery, type UrlHistoryMode } from '../../app/urlState';
import type { AutomationAction, RssIdentityStatus, RssLibrarySummary, RssMatch, RssSeedItem, RssSource, RssSourceInput } from '../../types/rssSeedLibrary';
import {
  classifyRssResourceScope,
  countRssResourceScopes,
  rssMatchMethodLabel,
  rssResourceScopeLabel,
  rssResourceScopeSummaryText
} from '../../types/rssSeedLibrary';
import { formatTimeAgo } from '../../utils/formatters';
import { createIdempotencyKey } from '../../utils/idempotency';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import { RelativeTime } from '../status/RelativeTime';
import type { AppNavigate } from '../layout/AppTopNav';

type WindowFilter = '' | '1h' | '24h' | '7d';
const RSS_INTERVAL_PRESETS = [1, 3, 5] as const;
const rssPageSize = 50;

interface RssLibraryUrlState {
  query: string;
  sourceId: string;
  identityStatus: RssIdentityStatus;
  windowFilter: WindowFilter;
  offset: number;
}

function readRssLibraryUrlState(location: Location = window.location): RssLibraryUrlState {
  const params = new URLSearchParams(location.search);
  const windowValue = params.get('window');
  const identityValue = params.get('identityStatus');
  const parsedOffset = Number(params.get('offset'));
  return {
    query: params.get('q') ?? '',
    sourceId: params.get('sourceId') ?? '',
    identityStatus: ['identified', 'conflict', 'unidentified'].includes(identityValue ?? '')
      ? identityValue as RssIdentityStatus
      : '',
    windowFilter: windowValue === null
      ? '24h'
      : windowValue === 'all'
        ? ''
        : ['1h', '24h', '7d'].includes(windowValue)
          ? windowValue as WindowFilter
          : '24h',
    offset: Number.isInteger(parsedOffset) && parsedOffset >= 0
      ? Math.floor(parsedOffset / rssPageSize) * rssPageSize
      : 0
  };
}

function writeRssLibraryUrlState(state: RssLibraryUrlState, mode: UrlHistoryMode = 'replace') {
  writeUrlQuery({
    q: state.query || null,
    sourceId: state.sourceId || null,
    identityStatus: state.identityStatus || null,
    window: state.windowFilter === '24h' ? null : state.windowFilter || 'all',
    offset: state.offset > 0 ? state.offset : null
  }, mode);
}

function isPresetInterval(value: number) {
  return RSS_INTERVAL_PRESETS.some((preset) => preset === value);
}

function isValidInterval(value: number) {
  return Number.isInteger(value) && value >= 1 && value <= 1440;
}

const emptySummary: RssLibrarySummary = {
  enabled: false,
  sources: 0,
  activeSources: 0,
  errorSources: 0,
  items: 0,
  lastSuccessAt: ''
};

const defaultForm: RssSourceInput = {
  name: '',
  feedUrl: '',
  enabled: true,
  intervalMinutes: 5,
  retentionDays: 7,
  allowHttp: false
};

function sizeLabel(bytes: number) {
  if (!bytes) return '大小未知';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function episodeLabel(item: RssSeedItem) {
  if (item.mediaType !== 'tv') return '电影';
  const season = item.seasonNumber == null ? '' : `S${String(item.seasonNumber).padStart(2, '0')}`;
  const episodes = item.episodeStart == null
    ? ''
    : item.episodeEnd && item.episodeEnd !== item.episodeStart
      ? `E${String(item.episodeStart).padStart(2, '0')}-${String(item.episodeEnd).padStart(2, '0')}`
      : `E${String(item.episodeStart).padStart(2, '0')}`;
  return `${season}${episodes}` || '剧集';
}

function identityLabel(status: RssSeedItem['identityStatus']) {
  if (status === 'identified') return '已识别';
  if (status === 'conflict') return '候选冲突';
  return '未识别';
}

function identitySourceLabel(value: string) {
  const labels: Record<string, string> = {
    rss_field: 'RSS 结构化字段',
    rss_description: 'RSS 简介',
    rss_link: 'RSS 公开链接',
    subscription_match: 'Fluxa 追更唯一匹配',
    torra_subscription_match: 'Torra 订阅唯一匹配'
  };
  return value.split(',').filter(Boolean).map((source) => labels[source] || source).join('、') || '暂无可靠来源';
}

function identityConfidenceLabel(value: string) {
  const labels: Record<string, string> = {
    strong: '高（结构化证据）',
    explicit: '高（公开明确证据）',
    fallback: '保守匹配',
    conflict: '存在冲突'
  };
  return labels[value] || value || '暂无';
}

function exactTimeLabel(value: string) {
  if (!value) return '未知';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `北京时间 ${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(parsed)}`;
}

function identityBackfillLabel(summary: RssLibrarySummary) {
  if (!summary.identityBackfillRan || summary.identityBackfillStatus === 'not_run') {
    return `身份回填尚未运行；当前 ${summary.items} 条种子尚不能证明识别链路已处理。`;
  }
  if (summary.identityBackfillStatus === 'failed') {
    return `身份回填最近运行失败；已扫描 ${summary.lastIdentityBackfillScanned ?? 0} 条，剩余 ${summary.lastIdentityBackfillRemaining ?? summary.items} 条。`;
  }
  return [
    `最近回填 ${summary.lastIdentityBackfillAt ? formatTimeAgo(summary.lastIdentityBackfillAt) : '时间未知'}`,
    `扫描 ${summary.lastIdentityBackfillScanned ?? 0} 条`,
    `识别 ${summary.lastIdentityBackfillIdentified ?? 0} 条`,
    `冲突 ${summary.lastIdentityBackfillConflicts ?? 0} 条`,
    `未变化 ${summary.lastIdentityBackfillUnchanged ?? 0} 条`,
    `剩余 ${summary.lastIdentityBackfillRemaining ?? summary.items} 条`
  ].join(' · ');
}

function matcherLabel(summary: RssLibrarySummary) {
  if (!summary.matcherRan || summary.matcherStatus === 'not_run') return `匹配器尚未运行；当前 ${summary.items} 条种子还没有匹配结果。`;
  if (summary.matcherStatus === 'failed') return `匹配器最近运行失败；上次扫描 ${summary.lastMatchScanned ?? 0} 条，未将结果视为成功。`;
  return `最近匹配 ${summary.lastMatchAt ? formatTimeAgo(summary.lastMatchAt) : '时间未知'} · 扫描 ${summary.lastMatchScanned ?? 0} 条 · 命中 ${summary.lastMatchCreated ?? 0} 条`;
}

function matchActionLabel(action: AutomationAction | undefined, matchStatus: RssMatch['status']) {
  if (!action) {
    if (matchStatus === 'expired') return '匹配记录已过期，无需处理';
    if (matchStatus === 'ignored') return '已检查，没有更合适的版本';
    if (matchStatus === 'confirmed') return '已处理完成';
    if (matchStatus === 'triggered') return '正在恢复上次检查结果';
    return '等待检查';
  }
  if (action.status === 'failed') return action.error?.message || '操作失败，请稍后重试';
  if (action.status === 'cancelled') return '操作已取消';
  if (action.status !== 'succeeded') {
    return action.type === 'rewash-download' ? 'Torra 正在接收下载任务' : 'Torra 正在检查可用版本';
  }
  if (action.type === 'rewash-download') return 'Torra 已接收下载任务，后续进度可在任务中心查看';
  const selectedCount = action.result?.selectedCount ?? 0;
  return selectedCount > 0 ? `检查完成，发现 ${selectedCount} 个更合适的版本` : '检查完成，当前没有更合适的版本';
}

function seedProcessingStateLabel(match: RssMatch | undefined, action: AutomationAction | undefined) {
  if (!match) return '未处理';
  if (action?.type === 'rewash-download' || match.status === 'confirmed') return 'Torra 已接收';
  if (action && !['succeeded', 'failed', 'cancelled'].includes(action.status)) return 'Torra 分析中';
  return '已建立匹配';
}

function seedPriorityReason(item: RssSeedItem, scope: ReturnType<typeof classifyRssResourceScope>) {
  const identityKnown = item.identityStatus === 'identified';
  if (identityKnown && scope === 'explicit_episode') return '精确身份优先 · 明确季集优先';
  if (identityKnown) return '精确身份优先';
  if (scope === 'explicit_episode') return '明确季集优先';
  return '暂无优先证据；最终下载推荐只来自 Torra 分析评分';
}

export function RssSeedLibraryPage({ onNavigate }: { onNavigate: AppNavigate }) {
  const [initialUrlState] = useState(readRssLibraryUrlState);
  const [sources, setSources] = useState<RssSource[]>([]);
  const [summary, setSummary] = useState<RssLibrarySummary>(emptySummary);
  const [items, setItems] = useState<RssSeedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [matches, setMatches] = useState<RssMatch[]>([]);
  const [matchesTotal, setMatchesTotal] = useState(0);
  const [matchesOffset, setMatchesOffset] = useState(0);
  const [matchesLoading, setMatchesLoading] = useState(false);
  const [matchActions, setMatchActions] = useState<Record<string, AutomationAction>>({});
  const [matchPollTimedOut, setMatchPollTimedOut] = useState<Record<string, boolean>>({});
  const [matchBusy, setMatchBusy] = useState('');
  const [downloadTarget, setDownloadTarget] = useState<{ match: RssMatch; analysis: AutomationAction } | null>(null);
  const [query, setQuery] = useState(initialUrlState.query);
  const [sourceId, setSourceId] = useState(initialUrlState.sourceId);
  const [identityStatus, setIdentityStatus] = useState<RssIdentityStatus>(initialUrlState.identityStatus);
  const [windowFilter, setWindowFilter] = useState<WindowFilter>(initialUrlState.windowFilter);
  const [offset, setOffset] = useState(initialUrlState.offset);
  const [urlRevision, setUrlRevision] = useState(0);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState<{ tone: 'ok' | 'error'; message: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<RssSource | null>(null);
  const [form, setForm] = useState<RssSourceInput>(defaultForm);
  const [saving, setSaving] = useState(false);
  const [testingSourceId, setTestingSourceId] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<RssSource | null>(null);
  const [detailItem, setDetailItem] = useState<RssSeedItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [identityBackfillBusy, setIdentityBackfillBusy] = useState(false);
  const [matcherRunBusy, setMatcherRunBusy] = useState(false);
  const itemsRequestRef = useRef<AbortController | null>(null);
  const matchesRequestRef = useRef<AbortController | null>(null);
  const matchPollRefs = useRef(new Map<string, AbortController>());
  const detailRequestRef = useRef<AbortController | null>(null);
  const pageSize = rssPageSize;

  const syncUrlState = (patch: Partial<RssLibraryUrlState>) => {
    writeRssLibraryUrlState({
      query,
      sourceId,
      identityStatus,
      windowFilter,
      offset,
      ...patch
    });
  };

  const loadSources = () => getRssSources().then((payload) => {
    setSources(payload.items);
    setSummary(payload.summary);
  });

  const loadItems = async (input: Partial<RssLibraryUrlState> = {}) => {
    itemsRequestRef.current?.abort();
    const controller = new AbortController();
    itemsRequestRef.current = controller;
    setItemsLoading(true);
    const requestedState: RssLibraryUrlState = {
      query: input.query ?? query,
      sourceId: input.sourceId ?? sourceId,
      identityStatus: input.identityStatus ?? identityStatus,
      windowFilter: input.windowFilter ?? windowFilter,
      offset: input.offset ?? offset
    };
    try {
      const payload = await getRssSeedItems(
        {
          query: requestedState.query,
          sourceId: requestedState.sourceId,
          window: requestedState.windowFilter,
          identityStatus: requestedState.identityStatus,
          limit: pageSize,
          offset: requestedState.offset
        },
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      setItems(payload.items);
      setTotal(payload.total);
      setOffset(payload.offset);
      writeRssLibraryUrlState({ ...requestedState, offset: payload.offset });
    } catch (reason) {
      if (!controller.signal.aborted) {
        setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '种子库读取失败' });
      }
    } finally {
      if (!controller.signal.aborted) setItemsLoading(false);
    }
  };

  const loadMatches = async (nextOffset = matchesOffset): Promise<Record<string, AutomationAction>> => {
    matchesRequestRef.current?.abort();
    const controller = new AbortController();
    matchesRequestRef.current = controller;
    setMatchesLoading(true);
    try {
      const payload = await getRssMatches({ limit: 10, offset: nextOffset }, { signal: controller.signal });
      if (controller.signal.aborted) return {};
      setMatches(payload.items);
      setMatchesTotal(payload.total);
      setMatchesOffset(payload.offset);
      const linkedActions = await Promise.all(payload.items.map(async (match) => {
        if (!match.triggerActionId) return null;
        try {
          return { matchId: match.id, action: await getAutomationAction(match.triggerActionId, { signal: controller.signal }) };
        } catch {
          return null;
        }
      }));
      const refreshedActions = linkedActions.reduce<Record<string, AutomationAction>>((next, entry) => {
        if (entry) next[entry.matchId] = entry.action;
        return next;
      }, {});
      if (!controller.signal.aborted) {
        setMatchActions((current) => ({ ...current, ...refreshedActions }));
        setMatchPollTimedOut((current) => linkedActions.reduce((next, entry) => {
          if (entry && ['succeeded', 'failed', 'cancelled'].includes(entry.action.status)) delete next[entry.matchId];
          return next;
        }, { ...current }));
      }
      return refreshedActions;
    } catch (reason) {
      if (!controller.signal.aborted) setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配读取失败' });
      return {};
    } finally {
      if (!controller.signal.aborted) setMatchesLoading(false);
    }
  };

  const refresh = async () => {
    setLoading(true);
    setFeedback(null);
    try {
        await Promise.all([loadSources(), loadItems(), loadMatches(0)]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '种子库读取失败' });
    } finally {
      setLoading(false);
    }
  };

  const runIdentityBackfill = async () => {
    setIdentityBackfillBusy(true);
    try {
      const result = await backfillRssIdentities(50);
      setFeedback({
        tone: 'ok',
        message: `身份回填完成：本次扫描 ${result.scanned} 条，识别 ${result.identified} 条，冲突 ${result.conflicts} 条，未变化 ${result.unchanged} 条，剩余 ${result.remaining} 条`
      });
      await Promise.all([loadSources(), loadItems({ offset: 0 })]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 身份回填失败' });
    } finally {
      setIdentityBackfillBusy(false);
    }
  };

  const runMatcher = async () => {
    setMatcherRunBusy(true);
    try {
      const result = await runRssMatcher(200);
      setFeedback({
        tone: 'ok',
        message: `匹配器完成：扫描 ${result.scanned} 条，新增 ${result.created} 条候选，仍有 ${result.remaining} 条待处理`
      });
      await Promise.all([loadSources(), loadItems({ offset: 0 }), loadMatches(0)]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 历史匹配失败' });
    } finally {
      setMatcherRunBusy(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, windowFilter, identityStatus, urlRevision]);

  useEffect(() => {
    const restoreUrlState = () => {
      const next = readRssLibraryUrlState();
      setQuery(next.query);
      setSourceId(next.sourceId);
      setIdentityStatus(next.identityStatus);
      setWindowFilter(next.windowFilter);
      setOffset(next.offset);
      setUrlRevision((current) => current + 1);
    };
    window.addEventListener('popstate', restoreUrlState);
    return () => window.removeEventListener('popstate', restoreUrlState);
  }, []);

  useEffect(() => () => {
    itemsRequestRef.current?.abort();
    matchesRequestRef.current?.abort();
    matchPollRefs.current.forEach((controller) => controller.abort());
    matchPollRefs.current.clear();
    detailRequestRef.current?.abort();
  }, []);

  const openItemDetail = async (item: RssSeedItem) => {
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setDetailItem(item);
    setDetailError('');
    setDetailLoading(true);
    try {
      const detail = await getRssSeedItem(item.id, { signal: controller.signal });
      if (!controller.signal.aborted) setDetailItem(detail);
    } catch (reason) {
      if (!controller.signal.aborted) setDetailError(reason instanceof Error ? reason.message : '种子详情读取失败');
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  };

  const closeItemDetail = () => {
    detailRequestRef.current?.abort();
    setDetailItem(null);
    setDetailError('');
    setDetailLoading(false);
  };

  const pollMatchAction = async (matchId: string, actionId: string) => {
    matchPollRefs.current.get(matchId)?.abort();
    const controller = new AbortController();
    matchPollRefs.current.set(matchId, controller);
    setMatchPollTimedOut((current) => {
      const next = { ...current };
      delete next[matchId];
      return next;
    });
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const action = await getAutomationAction(actionId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setMatchActions((current) => ({ ...current, [matchId]: action }));
        if (['succeeded', 'failed', 'cancelled'].includes(action.status)) {
          setMatchPollTimedOut((current) => {
            const next = { ...current };
            delete next[matchId];
            return next;
          });
          void loadMatches(matchesOffset);
          return;
        }
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 1500);
          controller.signal.addEventListener('abort', () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
      }
      if (!controller.signal.aborted) {
        const refreshed = await loadMatches(matchesOffset);
        if (refreshed[matchId]?.id === actionId && ['succeeded', 'failed', 'cancelled'].includes(refreshed[matchId].status)) return;
        setMatchPollTimedOut((current) => ({ ...current, [matchId]: true }));
        if (!controller.signal.aborted) setFeedback({ tone: 'error', message: '状态确认已超时，匹配记录已刷新；任务可能仍在后台执行，可稍后再次确认。' });
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        const refreshed = await loadMatches(matchesOffset);
        if (refreshed[matchId]?.id === actionId && ['succeeded', 'failed', 'cancelled'].includes(refreshed[matchId].status)) return;
        setMatchPollTimedOut((current) => ({ ...current, [matchId]: true }));
        if (!controller.signal.aborted) setFeedback({ tone: 'error', message: `${reason instanceof Error ? reason.message : 'RSS 匹配动作读取失败'}；匹配记录已刷新，可稍后再次确认。` });
      }
    } finally {
      if (matchPollRefs.current.get(matchId) === controller) matchPollRefs.current.delete(matchId);
    }
  };

  const analyzeMatch = (match: RssMatch) => {
    setMatchBusy(`analysis:${match.id}`);
    startRssMatchAnalysis(match.id, createIdempotencyKey())
      .then((action) => {
        setMatchActions((current) => ({ ...current, [match.id]: action }));
        void pollMatchAction(match.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配分析提交失败' }))
      .finally(() => setMatchBusy(''));
  };

  const confirmMatchDownload = () => {
    if (!downloadTarget) return;
    const { match, analysis } = downloadTarget;
    setDownloadTarget(null);
    setMatchBusy(`download:${match.id}`);
    startRssMatchDownload(match.id, {
      confirm: true,
      idempotencyKey: createIdempotencyKey(),
      analysisActionId: analysis.id
    })
      .then((action) => {
        setMatchActions((current) => ({ ...current, [match.id]: action }));
        setFeedback({ tone: 'ok', message: '已交给 Torra 处理，Torra 正在接收任务。' });
        void pollMatchAction(match.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 候选下载提交失败' }))
      .finally(() => setMatchBusy(''));
  };

  const timeline = items;
  const itemScopeCounts = useMemo(
    () => countRssResourceScopes(items.map((item) => classifyRssResourceScope(item))),
    [items]
  );
  const matchByItemId = useMemo(() => {
    const index = new Map<string, RssMatch>();
    matches.forEach((match) => {
      if (match.itemId && !index.has(match.itemId)) index.set(match.itemId, match);
    });
    return index;
  }, [matches]);

  const searchedSources = useMemo(() => {
    const scoped = sourceId ? sources.filter((source) => source.id === sourceId) : sources;
    return {
      searched: scoped.filter((source) => source.enabled && source.feedConfigured && !source.lastError),
      unavailable: scoped.filter((source) => source.enabled && Boolean(source.lastError)),
      inactive: scoped.filter((source) => (!source.enabled || !source.feedConfigured) && !source.lastError)
    };
  }, [sourceId, sources]);

  const sourceSearchSummary = useMemo(() => {
    const parts: string[] = [];
    if (searchedSources.searched.length) parts.push(`已搜索：${searchedSources.searched.map((source) => source.name).join('、')}`);
    if (searchedSources.unavailable.length) parts.push(`暂时不可用：${searchedSources.unavailable.map((source) => source.name).join('、')}`);
    if (searchedSources.inactive.length) parts.push(`未启用或未配置：${searchedSources.inactive.map((source) => source.name).join('、')}`);
    return parts.join('；');
  }, [searchedSources]);

  const openCreate = () => {
    setEditing(null);
    setForm(defaultForm);
    setFormOpen(true);
    setFeedback(null);
  };

  const openEdit = (source: RssSource) => {
    setEditing(source);
    setForm({
      name: source.name,
      feedUrl: '',
      enabled: source.enabled,
      intervalMinutes: source.intervalMinutes,
      retentionDays: source.retentionDays as 3 | 7 | 14,
      allowHttp: source.allowHttp
    });
    setFormOpen(true);
    setFeedback(null);
  };

  const submitSource = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const payload = { ...form };
      if (editing && !payload.feedUrl) delete payload.feedUrl;
      await saveRssSource(payload, editing?.id);
      setFormOpen(false);
      setEditing(null);
      setForm(defaultForm);
      setFeedback({ tone: 'ok', message: editing ? '来源设置已保存' : 'RSS 来源已加入种子库' });
      await loadSources();
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 来源保存失败' });
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (source: RssSource) => {
    if (testingSourceId) return;
    setTestingSourceId(source.id);
    setFeedback(null);
    try {
      const action = await testRssSource(source.id);
      setFeedback({
        tone: action.status === 'succeeded' ? 'ok' : 'error',
        message: action.status === 'succeeded'
          ? `RSS 可读取，识别到 ${action.result?.items ?? 0} 条内容`
          : action.result?.message || 'RSS 测试失败'
      });
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 测试失败' });
    } finally {
      setTestingSourceId('');
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      await deleteRssSource(deleteTarget.id);
      const nextSourceId = sourceId === deleteTarget.id ? '' : sourceId;
      if (nextSourceId !== sourceId) setSourceId(nextSourceId);
      setOffset(0);
      syncUrlState({ sourceId: nextSourceId, offset: 0 });
      setDeleteTarget(null);
      setFeedback({ tone: 'ok', message: '来源和对应本地索引已删除' });
      await Promise.all([loadSources(), loadItems({ sourceId: nextSourceId, offset: 0 })]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '来源删除失败' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="work-page ops-page rss-library-page">
      <section className="ops-hero ops-hero--compact rss-library-hero">
        <div>
          <p className="ops-eyebrow">PT 本地索引</p>
          <h1>种子库</h1>
          <p className="ops-page-subtitle">在本地汇总和筛选最近发布的种子。</p>
          <p className="ops-deck">集中保存最近发布的 PT RSS 内容，在本地完成搜索和筛选，再由 Torra 判断是否需要下载。</p>
        </div>
        <button aria-busy={loading} className="ops-action-button" disabled={loading} type="button" onClick={refresh}>
          <RefreshCcw aria-hidden="true" className={loading ? 'rss-spin' : ''} size={15} />
          {loading ? '正在刷新' : `刷新 · RSS ${summary.enabled ? '已开启' : '已关闭'}`}
        </button>
      </section>

      <section className="rss-ledger-strip" aria-label="种子库状态">
        <div><span>本地种子</span><strong>{summary.items}</strong></div>
        <div><span>RSS 来源</span><strong className="rss-source-count">已启用 {summary.activeSources}/{summary.sources} · <em className={summary.errorSources ? 'rss-value--warn' : ''}>健康 {Math.max(0, summary.sources - summary.errorSources)}/{summary.sources}</em></strong></div>
        <div><span>异常来源</span><strong className={summary.errorSources ? 'rss-value--warn' : ''}>{summary.errorSources}</strong></div>
        <div><span>最近收集</span><strong>{summary.lastSuccessAt ? <RelativeTime value={summary.lastSuccessAt} /> : '尚未收集'}</strong></div>
      </section>

      {feedback && (
        <div className={feedback.tone === 'error' ? 'rss-feedback rss-feedback--error' : 'rss-feedback'} role="status">
          {feedback.tone === 'error' ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
          {feedback.message}
        </div>
      )}

      <section className="rss-library-layout">
        <div className="rss-index-panel">
          <div className="rss-toolbar">
            <form
              className="rss-search"
              onSubmit={(event) => {
                event.preventDefault();
                const nextQuery = query.trim();
                setQuery(nextQuery);
                setOffset(0);
                syncUrlState({ query: nextQuery, offset: 0 });
                void loadItems({ query: nextQuery, offset: 0 });
              }}
            >
              <Search aria-hidden="true" size={16} />
              <input aria-label="搜索本地种子" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="片名、制作组、HDR、2160P…" />
              {query && <button aria-label="清空搜索" title="清空搜索" type="button" onClick={() => { setQuery(''); setOffset(0); syncUrlState({ query: '', offset: 0 }); void loadItems({ query: '', offset: 0 }); }}><X aria-hidden="true" size={14} /></button>}
            </form>
            <div className="rss-window-tabs" aria-label="更新时间范围">
              {([
                ['', '全部'], ['1h', '1 小时'], ['24h', '24 小时'], ['7d', '7 天']
              ] as Array<[WindowFilter, string]>).map(([value, label]) => (
                <button className={windowFilter === value ? 'rss-window-tab rss-window-tab--active' : 'rss-window-tab'} key={label} type="button" onClick={() => { setWindowFilter(value); setOffset(0); syncUrlState({ windowFilter: value, offset: 0 }); }}>{label}</button>
              ))}
            </div>
          </div>

          <div className="rss-index-head">
            <span>{loading || itemsLoading ? '正在读取本地索引' : `找到 ${total} 条内容`}</span>
            <div className="rss-index-filters">
              <select aria-label="按 RSS 来源筛选" value={sourceId} onChange={(event) => { const next = event.target.value; setSourceId(next); setOffset(0); syncUrlState({ sourceId: next, offset: 0 }); }}>
                <option value="">全部来源</option>
                {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
              </select>
            </div>
          </div>

          {!loading && !itemsLoading && itemScopeCounts.total > 0 && (
            <p className="rss-scope-summary" role="status">
              本页 {rssResourceScopeSummaryText(itemScopeCounts)}
            </p>
          )}

          <div className="rss-collection-summary" role="status">
            <span className={summary.enabled && summary.errorSources === 0 ? 'rss-source-light' : summary.errorSources ? 'rss-source-light rss-source-light--error' : 'rss-source-light rss-source-light--off'} />
            <div>
              <strong>{!summary.enabled ? 'RSS 收集当前未开启' : summary.errorSources ? '部分 RSS 来源暂时不可用' : 'RSS 正在正常收集'}</strong>
              <span>{!summary.enabled ? '已保存的本地种子仍可搜索，开启后才会继续收集新内容。' : summary.errorSources ? '可用来源仍会继续收集；展开“RSS 来源与设置”可查看异常来源。' : '新种子会自动保存，并继续尝试匹配你的追更作品。'}</span>
            </div>
          </div>

          <div className="rss-timeline">
            {!loading && timeline.length === 0 && (
              <div className="ops-empty rss-empty">
                <Database aria-hidden="true" size={22} />
                <strong>{query.trim() ? `没有找到“${query.trim()}”` : sources.length ? '当前范围内没有种子' : '还没有添加 RSS 来源'}</strong>
                <span>{sources.length ? (sourceSearchSummary || '当前没有可搜索的 RSS 来源。') : '先添加 RSS 来源；开启收集后，新发布内容会保存在这里。'}</span>
              </div>
            )}
            {timeline.map((item) => {
              const scope = classifyRssResourceScope(item);
              const itemMatch = matchByItemId.get(item.id);
              const itemAction = itemMatch ? matchActions[itemMatch.id] : undefined;
              return (
              <article className="rss-seed-row" key={item.id}>
                <div className="rss-seed-time"><span /> <RelativeTime value={item.publishedAt || item.lastSeenAt} /></div>
                <div className="rss-seed-body">
                  <div className="rss-seed-card-head">
                    <span>{item.sourceName}</span>
                    <RelativeTime value={item.publishedAt || item.lastSeenAt} />
                    <span className={`rss-identity-chip rss-identity-chip--${item.identityStatus}`}>{identityLabel(item.identityStatus)}</span>
                  </div>
                  <h2>{item.title}</h2>
                  <div className="rss-seed-desktop-meta">
                    <div className="rss-seed-meta">
                      <span>{episodeLabel(item)}</span>
                      <span>{rssResourceScopeLabel(scope)}</span>
                      <span>{sizeLabel(item.sizeBytes)}</span>
                    </div>
                    <div className="rss-version-line">
                      {item.versionSummary
                        ? item.versionSummary.split(' · ').map((value) => <span key={value}>{value}</span>)
                        : <span className="rss-version-muted">等待版本信息</span>}
                    </div>
                  </div>
                  <details className="rss-seed-technical">
                    <summary>技术信息</summary>
                    <dl>
                      <div><dt>类型与范围</dt><dd>{episodeLabel(item)} · {rssResourceScopeLabel(scope)}</dd></div>
                      <div><dt>大小</dt><dd>{sizeLabel(item.sizeBytes)}</dd></div>
                      <div><dt>版本</dt><dd>{item.versionSummary || '等待版本信息'}</dd></div>
                      <div><dt>来源地址</dt><dd>{item.sourceDomain}</dd></div>
                      <div><dt>匹配原因</dt><dd>{rssMatchMethodLabel(item.matchMethod, item.matchConfidence)}</dd></div>
                      <div><dt>官种</dt><dd>无法判断</dd></div>
                      <div><dt>下载 / 入库 / 重复</dt><dd>尚未确认</dd></div>
                      <div><dt>当前处理状态</dt><dd>{seedProcessingStateLabel(itemMatch, itemAction)}</dd></div>
                      <div><dt>优先检查理由</dt><dd>{seedPriorityReason(item, scope)}</dd></div>
                    </dl>
                    <button className="rss-seed-open" type="button" onClick={() => void openItemDetail(item)}><PanelRightOpen aria-hidden="true" size={13} />查看识别证据</button>
                  </details>
                </div>
                <div className="rss-seed-state">
                  <span className="state-chip">{seedProcessingStateLabel(itemMatch, itemAction)}</span>
                  <span className={`rss-identity-chip rss-identity-chip--${item.identityStatus}`}>{identityLabel(item.identityStatus)}</span>
                  <small>{item.sourceDomain}</small>
                  <button className="rss-seed-open" type="button" onClick={() => void openItemDetail(item)}>
                    <PanelRightOpen aria-hidden="true" size={13} />详情
                  </button>
                </div>
              </article>
              );
            })}
          </div>
          {total > pageSize && (
            <nav className="rss-pagination" aria-label="种子库分页">
              <button
                aria-label="上一页"
                disabled={itemsLoading || offset <= 0}
                title="上一页"
                type="button"
                onClick={() => void loadItems({ offset: Math.max(0, offset - pageSize) })}
              >
                <ChevronLeft aria-hidden="true" size={15} />
              </button>
              <span>第 {Math.floor(offset / pageSize) + 1} / {Math.ceil(total / pageSize)} 页</span>
              <button
                aria-label="下一页"
                disabled={itemsLoading || offset + pageSize >= total}
                title="下一页"
                type="button"
                onClick={() => void loadItems({ offset: offset + pageSize })}
              >
                <ChevronRight aria-hidden="true" size={15} />
              </button>
            </nav>
          )}
          <section className="rss-match-panel" aria-label="RSS 候选匹配">
            <header className="rss-match-panel__head">
              <div><strong>已匹配到追更作品</strong><small>{matchesTotal ? `最近 ${matchesTotal} 条匹配记录` : '新种子会自动尝试匹配，不需要手动扫描'}</small></div>
              <button className="ops-link" disabled={matchesLoading} type="button" onClick={() => void loadMatches(matchesOffset)}><RefreshCcw size={13} />刷新</button>
            </header>
            {matchesLoading && <small className="sub-detail__hint">正在查看是否有种子匹配到追更作品…</small>}
            {!matchesLoading && matches.length === 0 && <div className="rss-match-empty"><CheckCircle2 size={15} /><span><strong>{summary.enabled && summary.errorSources === 0 ? 'RSS 正常收集，但暂未匹配到追更作品' : '暂未匹配到追更作品'}</strong><small>{summary.enabled ? '可用来源会继续自动检查；现在无需处理。' : '开启收集后，新种子会自动尝试匹配。'}</small></span></div>}
            <div className="rss-match-list">
              {matches.map((match) => {
                const seed = items.find((item) => item.id === match.itemId);
                const action = matchActions[match.id];
                const pollTimedOut = Boolean(matchPollTimedOut[match.id]);
                const actionRunning = action && !pollTimedOut && !['succeeded', 'failed', 'cancelled'].includes(action.status);
                const selectedCount = action?.type === 'rewash-analysis' && action.status === 'succeeded' ? action.result?.selectedCount ?? 0 : 0;
                const isSubmitting = matchBusy.endsWith(`:${match.id}`);
                const canAnalyze = match.status === 'candidate' || action?.status === 'failed' || action?.status === 'cancelled';
                const downloadFailed = action?.type === 'rewash-download' && ['failed', 'cancelled'].includes(action.status);
                const downloadConfirmed = !downloadFailed && (action?.type === 'rewash-download' || match.status === 'confirmed');
                return (
                  <article className="rss-match-row" key={match.id}>
                    <div className="rss-match-row__content">
                      <strong>{match.itemTitle || seed?.title || match.subscriptionTitle || '已匹配到一条追更内容'}</strong>
                      <small>{match.subscriptionTitle || match.episodeLabel || '已关联到追更作品'}</small>
                      <span className={action?.status === 'failed' || pollTimedOut ? 'rss-match-status rss-match-status--error' : 'rss-match-status'}>{pollTimedOut ? '状态确认超时，已刷新匹配记录' : matchActionLabel(action, match.status)}</span>
                    </div>
                    {pollTimedOut && action ? (
                      <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => {
                        setFeedback({ tone: 'ok', message: '正在重新确认 Torra 动作状态…' });
                        void pollMatchAction(match.id, action.id);
                      }}>
                        <RefreshCcw size={13} />再次确认
                      </button>
                    ) : action && selectedCount > 0 ? (
                      <button className="ops-action-button ops-action-button--primary" disabled={isSubmitting} type="button" onClick={() => setDownloadTarget({ match, analysis: action })}>
                        <Download size={13} />{isSubmitting ? '正在提交' : '交给 Torra 处理'}
                      </button>
                    ) : downloadFailed ? (
                      <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => onNavigate('tasks', { outcomeState: 'action_required' })}>
                        前往任务中心
                      </button>
                    ) : downloadConfirmed ? (
                      <button className="ops-action-button" disabled type="button">已交给 Torra</button>
                    ) : (
                      <button className={canAnalyze ? 'ops-action-button ops-action-button--primary' : 'ops-action-button'} disabled={!canAnalyze || Boolean(actionRunning) || isSubmitting || action?.status === 'succeeded'} type="button" onClick={() => analyzeMatch(match)}>
                        <Send size={13} />{isSubmitting ? '正在提交' : actionRunning ? '正在检查' : canAnalyze ? '检查可用版本' : '无需处理'}
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
            {matchesTotal > 10 && (
              <nav className="rss-pagination rss-pagination--compact" aria-label="RSS 匹配分页">
                <button aria-label="上一页匹配" title="上一页匹配" disabled={matchesLoading || matchesOffset <= 0} type="button" onClick={() => void loadMatches(Math.max(0, matchesOffset - 10))}><ChevronLeft aria-hidden="true" size={14} /></button>
                <span>第 {Math.floor(matchesOffset / 10) + 1} / {Math.ceil(matchesTotal / 10)} 页</span>
                <button aria-label="下一页匹配" title="下一页匹配" disabled={matchesLoading || matchesOffset + 10 >= matchesTotal} type="button" onClick={() => void loadMatches(matchesOffset + 10)}><ChevronRight aria-hidden="true" size={14} /></button>
              </nav>
            )}
          </section>

          <details className="rss-diagnostics">
            <summary><ServerCog size={15} /><span><strong>高级诊断</strong><small>身份识别、历史扫描和匹配器信息</small></span></summary>
            <div className="rss-diagnostics__body">
              <div className="rss-diagnostics__actions">
                <button className="ops-action-button ops-action-button--primary" disabled={identityBackfillBusy || matcherRunBusy} type="button" onClick={() => void runIdentityBackfill()}>
                  <RefreshCcw aria-hidden="true" size={13} />{identityBackfillBusy ? '正在回填' : '补齐身份'}
                </button>
                <button className="ops-link" disabled={identityBackfillBusy || matcherRunBusy} type="button" onClick={() => void runMatcher()}>
                  <Send aria-hidden="true" size={13} />{matcherRunBusy ? '正在匹配' : '运行一批匹配'}
                </button>
                <select aria-label="按身份状态筛选" value={identityStatus} onChange={(event) => { const next = event.target.value as RssIdentityStatus; setIdentityStatus(next); setOffset(0); syncUrlState({ identityStatus: next, offset: 0 }); }}>
                  <option value="">全部身份</option>
                  <option value="identified">已识别</option>
                  <option value="conflict">候选冲突</option>
                  <option value="unidentified">未识别</option>
                </select>
              </div>
              <p className="rss-identity-run" role="status" title={summary.lastIdentityBackfillAt ? exactTimeLabel(summary.lastIdentityBackfillAt) : undefined}>{identityBackfillLabel(summary)}</p>
              <p className="rss-identity-run" role="status" title={summary.lastMatchAt ? exactTimeLabel(summary.lastMatchAt) : undefined}>{matcherLabel(summary)}</p>
            </div>
          </details>
        </div>

        <details className="rss-source-disclosure">
          <summary><ServerCog size={15} /><span><strong>RSS 来源与设置</strong><small>{sources.length} 个来源 · 添加、测试或修改收集地址</small></span></summary>
        <aside className="rss-source-panel">
          <div className="rss-source-head">
            <div><ServerCog size={17} /><span><small>来源管理</small><strong>{sources.length} 个 RSS</strong></span></div>
            <button className="ops-link" disabled={saving || Boolean(testingSourceId)} type="button" onClick={openCreate}><Plus size={14} />添加来源</button>
          </div>

          {formOpen && (
            <div className="rss-source-form">
              <div className="rss-source-form__title"><strong>{editing ? '编辑来源' : '添加 RSS 来源'}</strong><button aria-label="关闭来源表单" disabled={saving} title="关闭来源表单" type="button" onClick={() => setFormOpen(false)}><X aria-hidden="true" size={15} /></button></div>
              <label>来源名称<input disabled={saving} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：主站 RSS" /></label>
              <label>私人 RSS 地址<input autoComplete="off" className="monospace-text" disabled={saving} type="password" value={form.feedUrl || ''} onChange={(event) => setForm({ ...form, feedUrl: event.target.value })} placeholder={editing ? '留空保持原地址' : 'https://…?passkey=…'} /></label>
              <div className="rss-source-form__pair">
                <label>
                  轮询周期
                  <div className={isPresetInterval(form.intervalMinutes) ? 'rss-source-form__interval' : 'rss-source-form__interval is-custom'}>
                    <select
                      aria-label="轮询周期选项"
                      disabled={saving}
                      value={isPresetInterval(form.intervalMinutes) ? String(form.intervalMinutes) : 'custom'}
                      onChange={(event) => {
                        const value = event.target.value;
                        setForm({ ...form, intervalMinutes: value === 'custom' ? (isPresetInterval(form.intervalMinutes) ? 10 : form.intervalMinutes) : Number(value) });
                      }}
                    >
                      <option value={1}>1 分钟</option>
                      <option value={3}>3 分钟</option>
                      <option value={5}>5 分钟</option>
                      <option value="custom">自定义</option>
                    </select>
                  {!isPresetInterval(form.intervalMinutes) && (
                    <input
                      aria-label="自定义轮询分钟数"
                      disabled={saving}
                      max={1440}
                      min={1}
                      placeholder="分钟"
                      step={1}
                      type="number"
                      value={form.intervalMinutes}
                      onChange={(event) => setForm({ ...form, intervalMinutes: event.currentTarget.valueAsNumber || 0 })}
                    />
                  )}
                  </div>
                </label>
                <label>保留时间<select disabled={saving} value={form.retentionDays} onChange={(event) => setForm({ ...form, retentionDays: Number(event.target.value) as 3 | 7 | 14 })}><option value={3}>3 天</option><option value={7}>7 天</option><option value={14}>14 天</option></select></label>
              </div>
              <label className="rss-source-check"><input checked={form.enabled} disabled={saving} type="checkbox" onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用这个来源</label>
              <label className="rss-source-check"><input checked={form.allowHttp} disabled={saving} type="checkbox" onChange={(event) => setForm({ ...form, allowHttp: event.target.checked })} />允许 HTTP 或非标准端口</label>
              <button className="ops-action-button ops-action-button--primary" disabled={saving || !form.name.trim() || !isValidInterval(form.intervalMinutes) || (!editing && !form.feedUrl?.trim())} type="button" onClick={submitSource}>{saving ? '正在保存' : '保存来源'}</button>
              <p>RSS 地址会明文保存在 SQLite，但页面、接口和日志不会回显完整 Passkey。</p>
            </div>
          )}

          <div className="rss-source-list">
            {sources.length === 0 && !formOpen && <div className="ops-empty">还没有 RSS 来源。添加后也不会自动访问，直到收集开关开启。</div>}
            {sources.map((source) => (
              <article className="rss-source-card" key={source.id}>
                <div className="rss-source-card__top">
                  <span className={source.lastError ? 'rss-source-light rss-source-light--error' : source.enabled ? 'rss-source-light' : 'rss-source-light rss-source-light--off'} />
                  <div><strong>{source.name}</strong><small>{source.domain}</small></div>
                  <span>{source.intervalMinutes}m</span>
                </div>
                <div className="rss-source-card__meta">
                  <span><Clock3 size={12} />保留 {source.retentionDays} 天</span>
                  <span>{source.lastSuccessAt ? <RelativeTime value={source.lastSuccessAt} /> : '尚未收集'}</span>
                </div>
                {source.lastError && <p>{source.lastError}</p>}
                <div className="rss-source-card__actions">
                  <button disabled={saving || Boolean(testingSourceId)} type="button" onClick={() => void runTest(source)}>{testingSourceId === source.id ? '测试中…' : '测试'}</button>
                  <button disabled={saving || Boolean(testingSourceId)} type="button" onClick={() => openEdit(source)}><Edit3 size={12} />编辑</button>
                  <button className="rss-source-delete" disabled={saving || Boolean(testingSourceId)} type="button" onClick={() => setDeleteTarget(source)}><Trash2 size={12} />删除</button>
                </div>
              </article>
            ))}
          </div>
        </aside>
        </details>
      </section>

      <ConfirmDialog busy={saving} labelledBy="rss-delete-title" describedBy="rss-delete-description" open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)}>
        {deleteTarget && (
          <>
          <span className="ops-confirm-dialog__signal">删除本地来源</span>
          <h2 id="rss-delete-title">删除“{deleteTarget.name}”？</h2>
          <p id="rss-delete-description">这会删除该来源在 Fluxa 内保存的种子索引，不会修改 PT 站点上的任何数据。</p>
          <div className="ops-confirm-dialog__meta"><span>来源</span><strong>{deleteTarget.domain}</strong><span>影响</span><strong>本地索引</strong></div>
          <div className="ops-confirm-dialog__actions"><button className="ops-action-button" disabled={saving} type="button" onClick={() => setDeleteTarget(null)}>取消</button><button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus disabled={saving} type="button" onClick={confirmDelete}>{saving ? '正在删除' : '确认删除'}</button></div>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        busy={Boolean(matchBusy)}
        labelledBy="rss-download-title"
        describedBy="rss-download-description"
        open={Boolean(downloadTarget)}
        onClose={() => setDownloadTarget(null)}
      >
        {downloadTarget && (
          <>
            <span className="ops-confirm-dialog__signal">人工确认交给 Torra</span>
            <h2 id="rss-download-title">把检查出的升级版本交给 Torra？</h2>
            <p id="rss-download-description">Torra 会再次核对当前追更、观察单元和分析结果；原有高质量文件不会在这里被删除。</p>
            <div className="ops-confirm-dialog__meta">
              <span>作品</span><strong>{downloadTarget.match.subscriptionTitle || downloadTarget.match.itemTitle || '已匹配追更作品'}</strong>
              <span>候选</span><strong>{downloadTarget.analysis.result?.selectedCount ?? 0} 个升级版本</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" type="button" onClick={() => setDownloadTarget(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus type="button" onClick={confirmMatchDownload}>确认交给 Torra</button>
            </div>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog className="rss-detail-dialog" labelledBy="rss-detail-title" describedBy="rss-detail-description" open={Boolean(detailItem)} onClose={closeItemDetail}>
        {detailItem && (
          <>
            <header className="rss-detail-head">
              <div>
                <span>本地种子详情</span>
                <h2 id="rss-detail-title">{detailItem.title}</h2>
              </div>
              <button aria-label="关闭种子详情" data-dialog-initial-focus title="关闭" type="button" onClick={closeItemDetail}><X aria-hidden="true" size={18} /></button>
            </header>
            {detailLoading && <div className="rss-detail-notice"><RefreshCcw className="rss-spin" size={14} />正在读取完整信息</div>}
            {detailError && <div className="rss-detail-notice rss-detail-notice--error"><AlertTriangle size={14} />{detailError}</div>}
            <div className="rss-detail-grid">
              <span>来源</span><strong>{detailItem.sourceName}</strong>
              <span>发布时间</span><strong>{exactTimeLabel(detailItem.publishedAt)}</strong>
              <span>类型与范围</span><strong>{episodeLabel(detailItem)} · {rssResourceScopeLabel(classifyRssResourceScope(detailItem))}</strong>
              <span>大小</span><strong>{sizeLabel(detailItem.sizeBytes)}</strong>
              <span>分类</span><strong>{detailItem.category || '未提供'}</strong>
              <span>版本</span><strong>{detailItem.versionSummary || '未提取'}</strong>
              <span>TMDB</span><strong>{detailItem.tmdbId || '未识别'}</strong>
              <span>IMDb</span><strong>{detailItem.imdbId || '未识别'}</strong>
              <span>身份状态</span><strong className={`rss-detail-status rss-detail-status--${detailItem.identityStatus}`}>{identityLabel(detailItem.identityStatus)}</strong>
              <span>身份来源</span><strong>{identitySourceLabel(detailItem.identitySource)}</strong>
              <span>置信度</span><strong>{identityConfidenceLabel(detailItem.identityConfidence)}</strong>
              <span>最近确认</span><strong>{exactTimeLabel(detailItem.identityUpdatedAt)}</strong>
              <span>匹配原因</span><strong>{rssMatchMethodLabel(detailItem.matchMethod, detailItem.matchConfidence)}</strong>
              <span>官种</span><strong>无法判断</strong>
              <span>下载 / 入库 / 重复</span><strong>尚未确认</strong>
              <span>当前处理状态</span><strong>{seedProcessingStateLabel(matchByItemId.get(detailItem.id), matchByItemId.get(detailItem.id) ? matchActions[matchByItemId.get(detailItem.id)!.id] : undefined)}</strong>
            </div>
            <section className="rss-detail-description" aria-labelledby="rss-detail-description-title">
              <h3 id="rss-detail-description-title">RSS 简介</h3>
              <p id="rss-detail-description">{detailItem.description || '该 RSS 条目没有提供简介。'}</p>
            </section>
            <p className="rss-detail-security">出于安全考虑，Fluxa 不向浏览器返回下载地址、详情地址或 Passkey。</p>
          </>
        )}
      </ConfirmDialog>
    </main>
  );
}
