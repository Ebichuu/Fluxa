import { Fragment, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Ban, CalendarDays, Check, ChevronLeft, ChevronRight, Database, Download, FileSearch, Pause, Play, Plus, RefreshCcw, RotateCcw, Search, Send, SlidersHorizontal, Trash2, X } from 'lucide-react';
import {
  blockSubscription,
  browseDiscover,
  deleteSubscription,
  getAutomationAction,
  getMoviePilotPreview,
  getRssSeedItems,
  getSubscriptionQualityWatch,
  getSubscriptionDetail,
  getSubscriptionItems,
  getSubscriptionCapabilities,
  getSubscriptionWorkbench,
  getTorraSubscriptionSyncStatus,
  getTorraPushPreview,
  importTorraSubscriptions,
  previewTorraSubscriptionSync,
  pushSubscriptionToTorra,
  pushToMoviePilot,
  runSubscriptionSweep,
  runTorraSubscriptionSync,
  saveSubscription,
  searchDiscover,
  setSubscriptionSeason,
  startTorraRewashAnalysis,
  startTorraRewashDownload,
  unblockSubscription,
  updateSubscriptionQualityWatch
} from '../../services/api';
import type { AutomationAction, RssSeedItem, RssSeedListResponse } from '../../types/rssSeedLibrary';
import type {
  DiscoverBrowseParams,
  DiscoverResourceItem,
  DiscoverResourceResponse,
  MoviePilotPreview,
  MoviePilotPushResult,
  QualityWatchResponse,
  DiscoverResult,
  SubscriptionDetailResponse,
  SubscriptionCapabilitiesResponse,
  SubscriptionItem,
  SubscriptionWorkbenchResponse,
  TorraSubscriptionSyncPreview,
  TorraSubscriptionSyncStatus,
  TorraPushPreviewResponse
} from '../../types/subscriptions';
import { handleHorizontalTabKeyDown } from '../../utils/keyboardNavigation';
import type { AppNavigate, TaskNavigationTarget } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import { PosterImage } from '../layout/PosterImage';

interface DiscoverPageProps {
  navigationTarget?: TaskNavigationTarget | null;
  onNavigate: AppNavigate;
  view?: 'discover' | 'subscriptions';
}

type DiscoverSource = DiscoverBrowseParams['source'];
type FilterKey = 'type' | 'trend' | 'sort' | 'language' | 'year' | 'genre';

interface FilterOption {
  value: string;
  label: string;
}

interface FilterGroup {
  key: FilterKey;
  label: string;
  options: FilterOption[];
}

const currentYear = new Date().getFullYear();

const defaultFilters: DiscoverBrowseParams = {
  source: 'daily',
  type: 'tv',
  trend: 'all',
  sort: 'popularity_desc',
  language: 'all',
  year: 'all',
  genre: 'all',
  provider: 'netflix',
  page: 1,
  limit: 16
};

const sources = [
  { id: 'daily', label: '全球日播' },
  { id: 'tmdb', label: 'TMDB' },
  { id: 'streaming', label: '海外流媒体' },
  { id: 'douban', label: '豆瓣' },
  { id: 'tencent', label: '腾讯视频' },
  { id: 'youku', label: '优酷' },
  { id: 'iqiyi', label: '爱奇艺' },
  { id: 'mango', label: '芒果' }
] satisfies Array<{ id: DiscoverSource; label: string }>;

// 与后端 STREAMING_PROVIDERS 对齐（TMDB watch-provider，数据来自 JustWatch）
const streamingPlatforms: FilterOption[] = [
  { value: 'netflix', label: 'Netflix' },
  { value: 'disney', label: 'Disney+' },
  { value: 'max', label: 'HBO Max' },
  { value: 'prime', label: 'Prime Video' },
  { value: 'apple', label: 'Apple TV+' },
  { value: 'hulu', label: 'Hulu' },
  { value: 'paramount', label: 'Paramount+' },
  { value: 'peacock', label: 'Peacock' }
];

const filterGroups: FilterGroup[] = [
  {
    key: 'type',
    label: '类型',
    options: [
      { value: 'tv', label: '电视剧' },
      { value: 'movie', label: '电影' }
    ]
  },
  {
    key: 'trend',
    label: '趋势',
    options: [
      { value: 'all', label: '全部' },
      { value: 'week', label: '周榜' },
      { value: 'day', label: '日榜' }
    ]
  },
  {
    key: 'sort',
    label: '排序',
    options: [
      { value: 'popularity_desc', label: '热度降序' },
      { value: 'popularity_asc', label: '热度升序' },
      { value: 'date_desc', label: '上映时间降序' },
      { value: 'date_asc', label: '上映时间升序' },
      { value: 'rating_desc', label: '评分最高' },
      { value: 'rating_asc', label: '评分最低' }
    ]
  },
  {
    key: 'language',
    label: '语言',
    options: [
      { value: 'all', label: '全部' },
      { value: 'zh', label: '中文' },
      { value: 'en', label: '英语' },
      { value: 'ja', label: '日语' },
      { value: 'ko', label: '韩语' },
      { value: 'fr', label: '法语' },
      { value: 'de', label: '德语' },
      { value: 'es', label: '西语' },
      { value: 'it', label: '意语' },
      { value: 'ru', label: '俄语' },
      { value: 'pt', label: '葡语' },
      { value: 'ar', label: '阿语' },
      { value: 'hi', label: '印地语' },
      { value: 'th', label: '泰语' }
    ]
  },
  {
    key: 'year',
    label: '年份',
    options: [
      { value: 'all', label: '全部' },
      ...Array.from({ length: 6 }, (_, index) => {
        const year = String(currentYear - index);
        return { value: year, label: year };
      }),
      { value: '2020s', label: '2020年代' },
      { value: '2010s', label: '2010年代' },
      { value: '2000s', label: '2000年代' },
      { value: '1990s', label: '90年代' },
      { value: '1980s', label: '80年代' }
    ]
  },
  {
    key: 'genre',
    label: '风格',
    options: [
      { value: 'all', label: '全部' },
      { value: 'adventure', label: '冒险' },
      { value: 'fantasy', label: '奇幻' },
      { value: 'animation', label: '动画' },
      { value: 'drama', label: '剧情' },
      { value: 'horror', label: '恐怖' },
      { value: 'action', label: '动作' },
      { value: 'comedy', label: '喜剧' },
      { value: 'history', label: '历史' },
      { value: 'western', label: '西部' },
      { value: 'thriller', label: '惊悚' },
      { value: 'crime', label: '犯罪' },
      { value: 'documentary', label: '纪录片' },
      { value: 'scifi', label: '科幻' },
      { value: 'mystery', label: '悬疑' },
      { value: 'music', label: '音乐' },
      { value: 'romance', label: '爱情' },
      { value: 'family', label: '家庭' },
      { value: 'war', label: '战争' }
    ]
  }
];

const languageLabels = Object.fromEntries(filterGroups.find((group) => group.key === 'language')?.options.map((item) => [item.value, item.label]) ?? []);

function formatCount(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value);
}

function activeFilterCount(filters: DiscoverBrowseParams) {
  if (filters.source !== 'tmdb' && filters.source !== 'streaming') return 0;
  return [
    filters.source === 'tmdb' && filters.trend !== defaultFilters.trend,
    filters.sort !== defaultFilters.sort,
    filters.language !== defaultFilters.language,
    filters.year !== defaultFilters.year,
    filters.genre !== defaultFilters.genre
  ].filter(Boolean).length;
}

// 每个来源实际支持的筛选维度：日播/平台热更是固定剧集榜，不显示无效筛选
const sourceFilterKeys: Record<DiscoverSource, FilterKey[]> = {
  tmdb: ['type', 'trend', 'sort', 'language', 'year', 'genre'],
  streaming: ['type', 'sort', 'language', 'year', 'genre'],
  douban: [],
  daily: [],
  tencent: [],
  youku: [],
  iqiyi: [],
  mango: []
};

function forcedTypeForSource(source: DiscoverSource, currentType: DiscoverBrowseParams['type']) {
  if (source === 'daily' || source === 'tencent' || source === 'youku' || source === 'iqiyi' || source === 'mango') return 'tv';
  return currentType;
}

function tmdbIdForResult(result: DiscoverResult) {
  if (result.source === 'tmdb') return result.tmdbId || String(result.id);
  return result.tmdbId || '';
}

function resultMeta(result: DiscoverResult) {
  const parts = [
    result.year || '年份未知',
    result.mediaType === 'tv' ? '电视剧' : '电影'
  ];
  if (result.rating > 0) {
    parts.push(result.rating.toFixed(1));
  }
  if (result.originalLanguage && languageLabels[result.originalLanguage]) {
    parts.push(languageLabels[result.originalLanguage]);
  }
  return parts.join(' · ');
}

function resourceTitle(item: DiscoverResourceItem) {
  return item.title?.trim() || '未命名资源';
}

function resourceMeta(item: DiscoverResourceItem) {
  return [
    item.drive || item.source_label || item.source,
    item.size,
    item.quality,
    item.date
  ].filter(Boolean).join(' · ');
}

function resourcePreviewText(item: DiscoverResourceItem) {
  return [
    item.full_text,
    item.subtitle,
    item.password ? `提取码：${item.password}` : '',
    item.season ? `第 ${item.season} 季` : '',
    item.episodes?.length ? `集数：${item.episodes.join(', ')}` : ''
  ].filter(Boolean).join('\n');
}

function formatRssSeedSize(sizeBytes: number) {
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const unitIndex = Math.min(Math.floor(Math.log(sizeBytes) / Math.log(1024)), units.length - 1);
  const value = sizeBytes / (1024 ** unitIndex);
  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

function rssEpisodeNumbers(item: RssSeedItem) {
  if (item.episodeStart == null) return [];
  const start = Math.max(0, item.episodeStart);
  const end = Math.max(start, item.episodeEnd ?? start);
  return Array.from({ length: Math.min(end - start + 1, 200) }, (_, index) => start + index);
}

function rssEpisodeLabel(item: RssSeedItem) {
  const parts: string[] = [];
  if (item.seasonNumber != null) parts.push(`S${String(item.seasonNumber).padStart(2, '0')}`);
  if (item.episodeStart != null) {
    const start = `E${String(item.episodeStart).padStart(2, '0')}`;
    const end = item.episodeEnd != null && item.episodeEnd !== item.episodeStart
      ? `-E${String(item.episodeEnd).padStart(2, '0')}`
      : '';
    parts.push(`${start}${end}`);
  }
  return parts.join('');
}

function mapRssSeedsToResources(
  target: DiscoverResult,
  payload: RssSeedListResponse
): DiscoverResourceResponse {
  const sourceCounts = new Map<string, { label: string; count: number }>();
  const seasonEpisodes = new Map<number, Set<number>>();
  const items = payload.items.map((item) => {
    const sourceKey = item.sourceId || 'rss';
    const source = sourceCounts.get(sourceKey) ?? { label: item.sourceName || 'RSS', count: 0 };
    source.count += 1;
    sourceCounts.set(sourceKey, source);
    const episodes = rssEpisodeNumbers(item);
    if (item.seasonNumber != null && episodes.length > 0) {
      const values = seasonEpisodes.get(item.seasonNumber) ?? new Set<number>();
      episodes.forEach((episode) => values.add(episode));
      seasonEpisodes.set(item.seasonNumber, values);
    }
    return {
      source: sourceKey,
      source_key: sourceKey,
      source_label: item.sourceName || 'RSS',
      title: item.title,
      subtitle: [
        rssEpisodeLabel(item),
        item.hasDownload ? '已保留下载信息' : '仅保存种子元数据'
      ].filter(Boolean).join(' · '),
      quality: item.versionSummary,
      size: formatRssSeedSize(item.sizeBytes),
      date: item.publishedAt || item.lastSeenAt,
      full_text: item.description,
      season: item.seasonNumber == null ? undefined : String(item.seasonNumber),
      episodes
    } satisfies DiscoverResourceItem;
  });
  const sources = [
    { key: 'all', label: '全部来源', count: items.length },
    ...Array.from(sourceCounts, ([key, source]) => ({ key, ...source }))
  ];
  const seasons = Array.from(seasonEpisodes, ([season, episodes]) => {
    const values = Array.from(episodes).sort((left, right) => left - right);
    return { season: String(season), episodes: values, resource_episodes: values };
  }).sort((left, right) => Number(left.season) - Number(right.season));
  return {
    success: true,
    title: target.title,
    media_type: target.mediaType,
    items,
    sources,
    seasons,
    errors: [],
    cache_hits: []
  };
}

function mergeRssSeedResponses(payloads: RssSeedListResponse[]): RssSeedListResponse {
  const items = new Map<string, RssSeedItem>();
  payloads.forEach((payload) => {
    payload.items.forEach((item) => items.set(item.id, item));
  });
  return {
    items: Array.from(items.values()).slice(0, 50),
    total: items.size,
    limit: 50,
    offset: 0
  };
}

type SubscriptionTab = 'movie' | 'tv' | 'blocked';
type SubscriptionStatusFilter = 'all' | 'pending' | 'done';
type SubscriptionUpdateFilter = 'all' | 'today' | '3' | '7';

interface DiscoverConfirmation {
  signal: string;
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
}

function resolvedSubscriptionStatus(item: SubscriptionItem) {
  if (item.status) return item.status;
  const match = item.progressText.match(/^(\d+)\/(\d+)$/);
  if (match && Number(match[2]) > 0 && Number(match[1]) >= Number(match[2])) return 'done';
  return item.mediaType === 'movie' && item.inLibrary ? 'done' : 'pending';
}

function daysSinceSubscriptionUpdate(value: string) {
  if (!value) return Number.POSITIVE_INFINITY;
  const timestamp = new Date(value.replace(' ', 'T')).getTime();
  if (!Number.isFinite(timestamp)) return Number.POSITIVE_INFINITY;
  return Math.max(0, Math.floor((Date.now() - timestamp) / 86_400_000));
}

function subscriptionUpdateLabel(value: string) {
  const days = daysSinceSubscriptionUpdate(value);
  if (!Number.isFinite(days)) return '更新时间未知';
  if (days === 0) return '今天更新';
  return `${days} 天前更新`;
}

function subscriptionReadAtLabel(value: string) {
  if (!value) return '尚未读取';
  const parsed = new Date(value.replace(' ', 'T'));
  if (!Number.isFinite(parsed.getTime())) return '读取时间未知';
  return `最近读取 ${parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  })}`;
}

function reconciliationLabel(item: SubscriptionItem) {
  const labels = {
    linked: '已关联',
    only_fluxa: '仅 Fluxa',
    only_torra: '仅 Torra',
    conflict: '存在冲突',
    remote_missing: '远端已消失'
  } as const;
  return item.reconciliationState ? labels[item.reconciliationState] : item.torra?.status === 'linked' ? '已关联' : '尚未对账';
}

function fulfillmentLabel(item: SubscriptionItem) {
  const labels = {
    pending_sync: '待同步',
    following: '追更中',
    completed: '已完成',
    paused: '已暂停',
    blocked: '被阻塞'
  } as const;
  return item.fulfillmentState ? labels[item.fulfillmentState] : resolvedSubscriptionStatus(item) === 'done' ? '已完成' : '追更中';
}

const terminalAutomationStates = new Set(['succeeded', 'failed', 'cancelled']);

function watchStateLabel(state: string) {
  const labels: Record<string, string> = {
    waiting_first_version: '等待首个版本',
    waiting_library_baseline: '等待入库基线',
    observing_upgrade: '观察升级中',
    search_due: '等待分析',
    search_running: '分析进行中',
    target_reached: '已达到目标',
    observation_expired: '观察已结束',
    paused: '已暂停',
    blocked: '已阻塞'
  };
  return labels[state] || state || '未知状态';
}

function unitLabel(unit: QualityWatchResponse['units'][number]) {
  if (unit.episodeNumber != null) {
    return `S${String(unit.seasonNumber ?? 1).padStart(2, '0')}E${String(unit.episodeNumber).padStart(2, '0')}`;
  }
  if (unit.seasonNumber != null) return `S${String(unit.seasonNumber).padStart(2, '0')}`;
  return '整部电影';
}

function automationStatusLabel(action: AutomationAction | null) {
  if (!action) return '';
  if (action.status === 'succeeded') {
    if (action.type === 'rewash-download') return '候选下载已完成';
    return (action.result?.selectedCount ?? 0) > 0
      ? `分析已完成，发现 ${action.result?.selectedCount} 个升级候选`
      : '分析已完成，没有升级候选';
  }
  if (action.status === 'failed') return action.error?.message || '动作失败';
  if (action.status === 'cancelled') return '动作已取消';
  return action.type === 'rewash-download' ? '候选下载执行中' : 'Torra 分析执行中';
}

export function DiscoverPage({ navigationTarget = null, onNavigate, view = 'discover' }: DiscoverPageProps) {
  const subscriptionsOnly = view === 'subscriptions';
  const [filters, setFilters] = useState<DiscoverBrowseParams>(defaultFilters);
  const [query, setQuery] = useState('');
  const [activeSearch, setActiveSearch] = useState('');
  const [searchPage, setSearchPage] = useState(1);
  const [results, setResults] = useState<DiscoverResult[]>([]);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [pageInfo, setPageInfo] = useState({
    page: 1,
    totalPages: 1,
    totalResults: 0,
    sourceLabel: '全球日播'
  });
  const [subs, setSubs] = useState<SubscriptionItem[]>([]);
  const [blockedTitles, setBlockedTitles] = useState<string[]>([]);
  const [subsLoading, setSubsLoading] = useState(true);
  const [subsMoreLoading, setSubsMoreLoading] = useState(false);
  const [subsError, setSubsError] = useState('');
  const [workbench, setWorkbench] = useState<SubscriptionWorkbenchResponse | null>(null);
  const [subscriptionCapabilities, setSubscriptionCapabilities] = useState<SubscriptionCapabilitiesResponse | null>(null);
  const [subscriptionTab, setSubscriptionTab] = useState<SubscriptionTab>('tv');
  const [subscriptionKeyword, setSubscriptionKeyword] = useState('');
  const deferredSubscriptionKeyword = useDeferredValue(subscriptionKeyword);
  const [subscriptionYear, setSubscriptionYear] = useState('all');
  const [subscriptionStatus, setSubscriptionStatus] = useState<SubscriptionStatusFilter>('all');
  const [subscriptionUpdate, setSubscriptionUpdate] = useState<SubscriptionUpdateFilter>('all');
  const [sweepMessage, setSweepMessage] = useState('');
  const [subscriptionAction, setSubscriptionAction] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SubscriptionDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSeason, setDetailSeason] = useState<number | null>(null);
  const [resourceTarget, setResourceTarget] = useState<DiscoverResult | null>(null);
  const [resourceData, setResourceData] = useState<DiscoverResourceResponse | null>(null);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [resourceError, setResourceError] = useState('');
  const [resourceQueries, setResourceQueries] = useState<string[]>([]);
  const [resourceSource, setResourceSource] = useState('all');
  const [resourcePreview, setResourcePreview] = useState<DiscoverResourceItem | null>(null);
  const [torraPushPreview, setTorraPushPreview] = useState<TorraPushPreviewResponse | null>(null);
  const [torraPushMessage, setTorraPushMessage] = useState('');
  const [torraPushBusy, setTorraPushBusy] = useState('');
  const [qualityWatch, setQualityWatch] = useState<QualityWatchResponse | null>(null);
  const [qualityWatchBusy, setQualityWatchBusy] = useState('');
  const [qualityWatchMessage, setQualityWatchMessage] = useState('');
  const [selectedUnitId, setSelectedUnitId] = useState('');
  const [automationAction, setAutomationAction] = useState<AutomationAction | null>(null);
  const [moviePilotPreview, setMoviePilotPreview] = useState<MoviePilotPreview | null>(null);
  const [moviePilotBusy, setMoviePilotBusy] = useState('');
  const [moviePilotMessage, setMoviePilotMessage] = useState('');
  const [torraSyncStatus, setTorraSyncStatus] = useState<TorraSubscriptionSyncStatus | null>(null);
  const [torraSyncPreview, setTorraSyncPreview] = useState<TorraSubscriptionSyncPreview | null>(null);
  const [torraSyncBusy, setTorraSyncBusy] = useState('');
  const [torraSyncMessage, setTorraSyncMessage] = useState('');
  const [confirmation, setConfirmation] = useState<DiscoverConfirmation | null>(null);
  const automationRequestRef = useRef<AbortController | null>(null);
  const detailRequestRef = useRef<AbortController | null>(null);
  const resourceRequestRef = useRef<AbortController | null>(null);
  const resourcePanelRef = useRef<HTMLElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const loadSubs = useCallback(() => {
    setSubsLoading(true);
    setSubsError('');
    const request = subscriptionsOnly
      ? getSubscriptionWorkbench({
          limit: 24,
          offset: 0,
          mediaType: subscriptionTab === 'blocked' ? undefined : subscriptionTab,
          query: deferredSubscriptionKeyword
        })
      : getSubscriptionItems(true);
    request
      .then((payload) => {
        if ('items' in payload) {
          setWorkbench(payload);
          setSubs(payload.items);
          setBlockedTitles(payload.blockedTitles ?? []);
          setTorraSyncStatus(payload.torraSync);
          return;
        }
        if (payload.subscriptions) {
          setSubs(payload.subscriptions.items);
          setBlockedTitles(payload.blockedTitles ?? []);
        }
      })
      .catch((reason: unknown) => setSubsError(reason instanceof Error ? reason.message : '订阅工作台当前不可用'))
      .finally(() => setSubsLoading(false));
  }, [deferredSubscriptionKeyword, subscriptionTab, subscriptionsOnly]);

  const loadMoreSubs = useCallback(() => {
    const page = workbench?.page;
    const nextOffset = page?.nextOffset;
    if (!subscriptionsOnly || subscriptionTab === 'blocked' || !page || nextOffset == null || subsMoreLoading) return;
    setSubsMoreLoading(true);
    getSubscriptionWorkbench({
      limit: page.limit,
      offset: nextOffset,
      mediaType: subscriptionTab,
      query: deferredSubscriptionKeyword
    })
      .then((payload) => {
        setWorkbench(payload);
        setSubs((current) => {
          const seen = new Set(current.map((item) => item.id || `${item.mediaType}:${item.tmdbId}:${item.seasonNumber ?? 0}:${item.title}`));
          return [
            ...current,
            ...payload.items.filter((item) => {
              const key = item.id || `${item.mediaType}:${item.tmdbId}:${item.seasonNumber ?? 0}:${item.title}`;
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            })
          ];
        });
      })
      .catch((reason: unknown) => setSubsError(reason instanceof Error ? reason.message : '更多追更读取失败'))
      .finally(() => setSubsMoreLoading(false));
  }, [deferredSubscriptionKeyword, subsMoreLoading, subscriptionTab, subscriptionsOnly, workbench]);

  useEffect(() => {
    loadSubs();
  }, [loadSubs]);

  useEffect(() => {
    if (!subscriptionsOnly || !navigationTarget) return;
    if (navigationTarget.mediaType) setSubscriptionTab(navigationTarget.mediaType);
    if (navigationTarget.title) setSubscriptionKeyword(navigationTarget.title);
  }, [navigationTarget, subscriptionsOnly]);

  useEffect(() => {
    const controller = new AbortController();
    getSubscriptionCapabilities({ signal: controller.signal })
      .then(setSubscriptionCapabilities)
      .catch(() => setSubscriptionCapabilities(null));
    return () => controller.abort();
  }, []);

  const loadTorraSyncStatus = useCallback(() => {
    if (!subscriptionsOnly) return;
    getTorraSubscriptionSyncStatus()
      .then(setTorraSyncStatus)
      .catch(() => setTorraSyncMessage('Torra 同步状态暂不可用'));
  }, [subscriptionsOnly]);

  useEffect(() => {
    loadTorraSyncStatus();
  }, [loadTorraSyncStatus]);

  useEffect(() => {
    const focusSearch = () => searchInputRef.current?.focus();
    window.addEventListener('mcc:focus-discover-search', focusSearch);
    return () => window.removeEventListener('mcc:focus-discover-search', focusSearch);
  }, []);

  useEffect(() => () => {
    automationRequestRef.current?.abort();
    detailRequestRef.current?.abort();
  }, []);

  const applyPayload = useCallback((payload: Awaited<ReturnType<typeof browseDiscover>>) => {
    setConfigured(payload.configured);
    setResults(payload.results);
    setPageInfo({
      page: payload.page ?? 1,
      totalPages: Math.max(1, payload.totalPages ?? 1),
      totalResults: payload.totalResults ?? payload.results.length,
      sourceLabel: payload.sourceLabel ?? 'TMDB'
    });
  }, []);

  useEffect(() => {
    if (subscriptionsOnly) {
      setLoading(false);
      return;
    }
    if (activeSearch) return;
    let cancelled = false;
    setLoading(true);

    browseDiscover(filters)
      .then((payload) => {
        if (!cancelled) applyPayload(payload);
      })
      .catch(() => {
        if (!cancelled) {
          setResults([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeSearch, applyPayload, filters, subscriptionsOnly]);

  useEffect(() => {
    if (subscriptionsOnly) return;
    if (!activeSearch) return;
    let cancelled = false;
    setLoading(true);

    searchDiscover(activeSearch, searchPage)
      .then((payload) => {
        if (!cancelled) applyPayload(payload);
      })
      .catch(() => {
        if (!cancelled) setResults([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeSearch, applyPayload, searchPage, subscriptionsOnly]);

  const subscribedKeys = useMemo(() => new Set(subs.map((item) => `${item.mediaType}:${item.tmdbId}`)), [subs]);
  const subscriptionYears = useMemo(() => [
    ...new Set(subs.map((item) => item.year).filter((year): year is string => Boolean(year)))
  ].sort().reverse(), [subs]);
  const visibleSubscriptions = useMemo(() => {
    if (subscriptionTab === 'blocked') return [];
    const keyword = subscriptionKeyword.trim().toLowerCase();
    return subs.filter((item) => {
      if (item.mediaType !== subscriptionTab) return false;
      if (subscriptionYear !== 'all' && item.year !== subscriptionYear) return false;
      if (subscriptionStatus !== 'all' && resolvedSubscriptionStatus(item) !== subscriptionStatus) return false;
      const days = daysSinceSubscriptionUpdate(item.updatedAt);
      if (subscriptionUpdate === 'today' && days !== 0) return false;
      if (subscriptionUpdate === '3' && days > 3) return false;
      if (subscriptionUpdate === '7' && days > 7) return false;
      if (keyword) {
        const haystack = [item.title, item.seasonName, item.sourceLabel, item.tmdbId].filter(Boolean).join(' ').toLowerCase();
        if (!haystack.includes(keyword)) return false;
      }
      return true;
    });
  }, [subs, subscriptionKeyword, subscriptionStatus, subscriptionTab, subscriptionUpdate, subscriptionYear]);
  const localWriteEnabled = subscriptionsOnly
    ? Boolean(workbench?.capabilities.find((capability) => capability.key === 'local_write')?.enabled)
    : true;
  const workbenchStats = workbench?.stats ?? {
    total: subs.length,
    movie: subs.filter((item) => item.mediaType === 'movie').length,
    tv: subs.filter((item) => item.mediaType === 'tv').length,
    pending: subs.filter((item) => !item.inLibrary).length,
    inLibrary: subs.filter((item) => item.inLibrary).length
  };
  const reconciliationSummary = workbench?.reconciliation?.summary;
  const torraPushEnabled = Boolean(subscriptionCapabilities?.torraPush.enabled);
  const schedulerRunning = Boolean(subscriptionCapabilities?.scheduler.running);
  const followConfirmationText = !subscriptionCapabilities
    ? '保存追更意图；实际同步状态将在保存后确认。'
    : !torraPushEnabled
      ? '保存追更意图，当前不会自动获取；可稍后预览并手动同步到 Torra。'
      : !schedulerRunning
        ? '保存追更意图；Torra 推送已开启，但定时任务未运行，需要手动同步。'
        : '保存后进入自动追更，系统会按 PT 优先策略继续处理。';
  const followSuccessText = !subscriptionCapabilities || !torraPushEnabled
    ? '已保存追更意图，当前不会自动获取'
    : !schedulerRunning
      ? '已保存追更意图，等待手动同步到 Torra'
      : '已保存追更意图，已进入自动追更';

  const changeSource = (source: DiscoverSource) => {
    setQuery('');
    setActiveSearch('');
    setSearchPage(1);
    setFilters((current) => ({
      ...current,
      source,
      type: forcedTypeForSource(source, current.type),
      page: 1
    }));
  };

  const updateFilter = (key: FilterKey, value: string) => {
    setActiveSearch('');
    setSearchPage(1);
    setFilters((current) => ({
      ...current,
      [key]: value,
      page: 1
    }));
  };

  const resetFilters = () => {
    setQuery('');
    setActiveSearch('');
    setSearchPage(1);
    setFilters(defaultFilters);
  };

  const runSearch = (event: FormEvent) => {
    event.preventDefault();
    const keyword = query.trim();
    if (!keyword) {
      setActiveSearch('');
      setSearchPage(1);
      return;
    }
    setActiveSearch(keyword);
    setSearchPage(1);
  };

  const goPage = (nextPage: number) => {
    const page = Math.max(1, Math.min(pageInfo.totalPages, nextPage));
    if (activeSearch) {
      setSearchPage(page);
      return;
    }
    setFilters((current) => ({ ...current, page }));
  };

  const subscribe = (result: DiscoverResult) => {
    const tmdbId = tmdbIdForResult(result);
    if (!tmdbId) return;
    const payload = {
      title: result.title,
      mediaType: result.mediaType,
      tmdbId,
      posterUrl: result.posterUrl,
      year: result.year,
      originalLanguage: result.originalLanguage,
      genreIds: result.genreIds,
      originCountry: result.originCountry
    };
    setConfirmation({
      signal: '自动订阅',
      title: `订阅《${payload.title}》？`,
      description: followConfirmationText,
      confirmLabel: '确认订阅',
      onConfirm: () => {
        setSubscriptionAction(`save:${payload.mediaType}:${payload.tmdbId}`);
        saveSubscription(payload)
          .then(() => {
            setSweepMessage(`${followSuccessText}：${payload.title}`);
            loadSubs();
          })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '保存订阅失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const runSweep = () => {
    setConfirmation({
      signal: '自动订阅',
      title: '更新自动订阅来源？',
      description: '这会重新读取已启用的榜单来源，并增量合并到本地台账；不会搜索当前剧集，也不会删除手动订阅或 Torra 镜像。',
      confirmLabel: '开始更新',
      onConfirm: () => {
        setSubscriptionAction('run');
        runSubscriptionSweep()
          .then(() => {
            setSweepMessage('自动订阅来源已更新，列表正在重新读取。');
            loadSubs();
          })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '执行失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const previewTorraMirror = () => {
    setTorraSyncBusy('preview');
    setTorraSyncMessage('');
    previewTorraSubscriptionSync()
      .then((preview) => {
        setTorraSyncPreview(preview);
        setTorraSyncMessage(`已读取 Torra：${preview.summary.total} 条，${preview.summary.importable} 条可同步`);
      })
      .catch((error: unknown) => setTorraSyncMessage(error instanceof Error ? error.message : 'Torra 订阅预览失败'))
      .finally(() => setTorraSyncBusy(''));
  };

  const importTorraMirror = () => {
    if (!torraSyncPreview) return;
    setConfirmation({
      signal: 'Torra 单向镜像',
      title: `导入 ${torraSyncPreview.summary.importable} 条 Torra 订阅？`,
      description: '只会写入 Fluxa 本地订阅台账，不会修改或删除 Torra 中的任何订阅。',
      confirmLabel: '确认导入',
      onConfirm: () => {
        setTorraSyncBusy('import');
        importTorraSubscriptions(window.crypto.randomUUID())
          .then((result) => {
            setTorraSyncMessage(`已导入 ${result.summary.imported ?? 0} 条，更新 ${result.summary.updated ?? 0} 条`);
            setTorraSyncPreview(null);
            loadSubs();
            loadTorraSyncStatus();
          })
          .catch((error: unknown) => setTorraSyncMessage(error instanceof Error ? error.message : 'Torra 订阅导入失败'))
          .finally(() => setTorraSyncBusy(''));
      }
    });
  };

  const refreshTorraMirror = () => {
    setTorraSyncBusy('sync');
    setTorraSyncMessage('');
    runTorraSubscriptionSync()
      .then((result) => {
        setTorraSyncMessage(`状态同步完成：更新 ${result.summary.updated ?? 0} 条`);
        loadSubs();
        loadTorraSyncStatus();
      })
      .catch((error: unknown) => setTorraSyncMessage(error instanceof Error ? error.message : 'Torra 状态同步失败'))
      .finally(() => setTorraSyncBusy(''));
  };

  const removeSubscription = (item: SubscriptionItem) => {
    if (!item.id) return;
    setConfirmation({
      signal: '订阅管理',
      title: `删除《${item.title}》？`,
      description: '删除后不会加入屏蔽列表；如果之后来源再次命中，仍可能重新出现。',
      confirmLabel: '删除订阅',
      destructive: true,
      onConfirm: () => {
        setSubscriptionAction(`delete:${item.id}`);
        deleteSubscription(item.id!)
          .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已删除订阅：${item.title}`); })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '删除失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const blockItem = (item: SubscriptionItem) => {
    if (!item.id) return;
    setConfirmation({
      signal: '订阅管理',
      title: `删除并屏蔽《${item.title}》？`,
      description: '自动订阅后续会跳过这个标题，直到你在屏蔽列表中取消屏蔽。',
      confirmLabel: '删除并屏蔽',
      destructive: true,
      onConfirm: () => {
        setSubscriptionAction(`block:${item.id}`);
        blockSubscription({ id: item.id!, title: item.title })
          .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已屏蔽订阅：${item.title}`); })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '屏蔽失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const unblockItem = (title: string) => {
    setSubscriptionAction(`unblock:${title}`);
    unblockSubscription(title)
      .then(() => { loadSubs(); setSweepMessage(`已取消屏蔽：${title}`); })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '取消屏蔽失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const changeSeason = (item: SubscriptionItem, seasonNumber: number, seasonName?: string) => {
    if (!item.id) return;
    setConfirmation({
      signal: '订阅管理',
      title: `改为订阅《${item.title}》第 ${seasonNumber} 季？`,
      description: '当前订阅季会被替换，下载和入库规则将按新季继续处理。',
      confirmLabel: '切换订阅季',
      onConfirm: () => {
        setSubscriptionAction(`season:${item.id}`);
        setSubscriptionSeason(item.id!, seasonNumber, seasonName)
          .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已更新订阅季：${item.title} · S${seasonNumber}`); })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '更新订阅季失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const previewTorraPush = (item: SubscriptionItem) => {
    if (!item.id) return;
    setTorraPushBusy(`preview:${item.id}`);
    setTorraPushMessage('');
    setTorraPushPreview(null);
    getTorraPushPreview(item.id)
      .then(setTorraPushPreview)
      .catch((error: unknown) => setTorraPushMessage(error instanceof Error ? error.message : 'Torra 推送预检失败'))
      .finally(() => setTorraPushBusy(''));
  };

  const confirmTorraPush = (item: SubscriptionItem) => {
    if (!item.id || !torraPushPreview?.preview.ready) return;
    setTorraPushBusy(`push:${item.id}`);
    setTorraPushMessage('');
    pushSubscriptionToTorra(item.id, window.crypto.randomUUID())
      .then((result) => {
        setTorraPushMessage(result.message);
        setSweepMessage(`${item.title}：${result.message}`);
        loadSubs();
      })
      .catch((error: unknown) => setTorraPushMessage(error instanceof Error ? error.message : 'Torra 推送失败'))
      .finally(() => setTorraPushBusy(''));
  };

  const pollAutomationAction = async (actionId: string) => {
    automationRequestRef.current?.abort();
    const controller = new AbortController();
    automationRequestRef.current = controller;
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const action = await getAutomationAction(actionId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAutomationAction(action);
        if (terminalAutomationStates.has(action.status)) return;
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 1500);
          controller.signal.addEventListener('abort', () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
      }
      if (!controller.signal.aborted) setQualityWatchMessage('动作仍在后台执行，可稍后重新打开详情查看结果。');
    } catch (reason) {
      if (!controller.signal.aborted) {
        setQualityWatchMessage(reason instanceof Error ? reason.message : '自动化动作状态读取失败');
      }
    } finally {
      if (automationRequestRef.current === controller) automationRequestRef.current = null;
    }
  };

  const updateQualityWatch = (item: SubscriptionItem, input: { paused?: boolean; windowHours?: 24 | 48; scheduleMinutes?: number[] }) => {
    if (!item.id) return;
    setQualityWatchBusy(`update:${item.id}`);
    setQualityWatchMessage('');
    updateSubscriptionQualityWatch(item.id, input)
      .then((payload) => {
        setQualityWatch(payload);
        setSelectedUnitId((current) => current || payload.units[0]?.id || '');
        setQualityWatchMessage(input.paused === undefined ? '质量观察设置已保存' : input.paused ? '质量观察已暂停' : '质量观察已恢复');
      })
      .catch((reason: unknown) => setQualityWatchMessage(reason instanceof Error ? reason.message : '质量观察设置失败'))
      .finally(() => setQualityWatchBusy(''));
  };

  const startAnalysis = (item: SubscriptionItem) => {
    if (!item.id) return;
    setQualityWatchBusy(`analysis:${item.id}`);
    setQualityWatchMessage('正在提交 Torra 质量分析…');
    setAutomationAction(null);
    startTorraRewashAnalysis(item.id, {
      idempotencyKey: window.crypto.randomUUID(),
      ...(selectedUnitId ? { unitId: selectedUnitId } : {})
    })
      .then((action) => {
        setAutomationAction(action);
        setQualityWatchMessage(automationStatusLabel(action));
        void pollAutomationAction(action.id);
      })
      .catch((reason: unknown) => setQualityWatchMessage(reason instanceof Error ? reason.message : 'Torra 分析提交失败'))
      .finally(() => setQualityWatchBusy(''));
  };

  const startDownload = (item: SubscriptionItem) => {
    if (!item.id || !automationAction || automationAction.status !== 'succeeded' || automationAction.type !== 'rewash-analysis') return;
    const itemId = item.id;
    const analysis = automationAction;
    setConfirmation({
      signal: '质量升级',
      title: `下载《${item.title}》的升级候选？`,
      description: '这会把人工分析选中的候选交给 Torra 下载，原有入库版本不会立即删除。',
      confirmLabel: '确认下载',
      onConfirm: () => {
        setQualityWatchBusy(`download:${itemId}`);
        setQualityWatchMessage('正在提交 Torra 候选下载…');
        startTorraRewashDownload(itemId, {
          confirm: true,
          idempotencyKey: window.crypto.randomUUID(),
          analysisActionId: analysis.id,
          ...((analysis.unitId || selectedUnitId) ? { unitId: analysis.unitId || selectedUnitId } : {})
        })
          .then((action) => {
            setAutomationAction(action);
            setQualityWatchMessage(automationStatusLabel(action));
            void pollAutomationAction(action.id);
          })
          .catch((reason: unknown) => setQualityWatchMessage(reason instanceof Error ? reason.message : 'Torra 候选下载提交失败'))
          .finally(() => setQualityWatchBusy(''));
      }
    });
  };

  const previewMoviePilot = (item: SubscriptionItem) => {
    if (!item.id) return;
    setMoviePilotBusy(`preview:${item.id}`);
    setMoviePilotMessage('正在检查 MoviePilot 备用条件…');
    setMoviePilotPreview(null);
    getMoviePilotPreview(item.id)
      .then((preview) => {
        setMoviePilotPreview(preview);
        setMoviePilotMessage(preview.ready ? 'MoviePilot 备用条件已满足' : preview.blockers.join('；'));
      })
      .catch((reason: unknown) => setMoviePilotMessage(reason instanceof Error ? reason.message : 'MoviePilot 预览失败'))
      .finally(() => setMoviePilotBusy(''));
  };

  const confirmMoviePilot = (item: SubscriptionItem) => {
    if (!item.id || !moviePilotPreview?.ready) return;
    const itemId = item.id;
    setConfirmation({
      signal: '备用通道',
      title: `将《${item.title}》交给 MoviePilot？`,
      description: '这只会执行已通过预检的备用推送，不会改变 Torra 作为默认主通道的优先级。',
      confirmLabel: '确认备用推送',
      onConfirm: () => {
        setMoviePilotBusy(`push:${itemId}`);
        setMoviePilotMessage('正在执行 MoviePilot 备用推送…');
        pushToMoviePilot(itemId, window.crypto.randomUUID())
          .then((result: MoviePilotPushResult) => {
            setMoviePilotMessage(result.message);
            setSweepMessage(`${item.title}：${result.message}`);
            loadSubs();
          })
          .catch((reason: unknown) => setMoviePilotMessage(reason instanceof Error ? reason.message : 'MoviePilot 备用推送失败'))
          .finally(() => setMoviePilotBusy(''));
      }
    });
  };

  const closeDetail = () => {
    automationRequestRef.current?.abort();
    detailRequestRef.current?.abort();
    setDetailId(null);
    setDetail(null);
    setDetailSeason(null);
    setTorraPushPreview(null);
    setTorraPushMessage('');
    setTorraPushBusy('');
    setQualityWatch(null);
    setQualityWatchBusy('');
    setQualityWatchMessage('');
    setSelectedUnitId('');
    setAutomationAction(null);
    setMoviePilotPreview(null);
    setMoviePilotBusy('');
    setMoviePilotMessage('');
  };

  const openDetail = (item: SubscriptionItem) => {
    if (!item.id) {
      return;
    }
    if (detailId === item.id) {
      closeDetail();
      return;
    }
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    setDetailId(item.id);
    setDetail(null);
    setQualityWatch(null);
    setAutomationAction(null);
    setMoviePilotPreview(null);
    setQualityWatchMessage('');
    setMoviePilotMessage('');
    setDetailSeason(null);
    setSelectedUnitId('');
    setDetailLoading(true);
    setTorraPushPreview(null);
    setTorraPushMessage('');
    Promise.allSettled([
      getSubscriptionDetail(item.id, undefined, { signal: controller.signal }),
      getSubscriptionQualityWatch(item.id, { signal: controller.signal })
    ])
      .then(([detailResult, watchResult]) => {
        if (controller.signal.aborted) return;
        if (detailResult.status === 'fulfilled') {
          setDetail(detailResult.value);
          const firstSeason = detailResult.value.seasons[0];
          setDetailSeason(firstSeason?.seasonNumber ?? firstSeason?.season_number ?? null);
        } else {
          setDetail(null);
        }
        if (watchResult.status === 'fulfilled') {
          setQualityWatch(watchResult.value);
          setSelectedUnitId(watchResult.value.units[0]?.id || '');
        } else {
          setQualityWatchMessage(watchResult.reason instanceof Error ? watchResult.reason.message : '质量观察状态暂不可用');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
        if (detailRequestRef.current === controller) detailRequestRef.current = null;
      });
  };

  useEffect(() => {
    if (!subscriptionsOnly || !navigationTarget || detailId) return;
    const targetItem = subs.find((item) => (
      (navigationTarget.subscriptionId && item.id === navigationTarget.subscriptionId)
      || (navigationTarget.tmdbId && String(item.tmdbId || '') === navigationTarget.tmdbId
        && (!navigationTarget.seasonNumber || item.seasonNumber === navigationTarget.seasonNumber))
    ));
    if (!targetItem) return;
    openDetail(targetItem);
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(`[data-subscription-id="${CSS.escape(targetItem.id || '')}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  }, [detailId, navigationTarget, subscriptionsOnly, subs]);

  useEffect(() => {
    if (!resourceTarget) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const panel = resourcePanelRef.current;
      if (!panel) return;
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      panel.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'nearest' });
      panel.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [resourceTarget]);

  useEffect(() => () => resourceRequestRef.current?.abort(), []);

  const openResourceSearch = (
    result: DiscoverResult,
    querySource: string[] | Promise<string[]> = [result.title]
  ) => {
    resourceRequestRef.current?.abort();
    const controller = new AbortController();
    resourceRequestRef.current = controller;
    setResourceTarget(result);
    setResourceData(null);
    setResourceError('');
    setResourceQueries([result.title]);
    setResourceSource('all');
    setResourcePreview(null);
    setResourceLoading(true);
    Promise.resolve(querySource)
      .then((values) => {
        const queries = Array.from(new Set(
          values.map((value) => value.trim()).filter(Boolean)
        )).slice(0, 4);
        const resolvedQueries = queries.length > 0 ? queries : [result.title];
        if (!controller.signal.aborted) setResourceQueries(resolvedQueries);
        return Promise.all(resolvedQueries.map((query) => getRssSeedItems(
          {
            query,
            tmdbId: result.tmdbId,
            mediaType: result.mediaType,
            seasonNumber: result.seasonNumber,
            year: result.year,
            limit: 50,
            offset: 0
          },
          { signal: controller.signal }
        )));
      })
      .then((payloads) => {
        if (!controller.signal.aborted) {
          setResourceData(mapRssSeedsToResources(result, mergeRssSeedResponses(payloads)));
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setResourceError(error instanceof Error ? error.message : '本地 RSS 种子箱查询失败');
        }
      })
      .finally(() => {
        if (resourceRequestRef.current === controller) {
          setResourceLoading(false);
          resourceRequestRef.current = null;
        }
      });
  };

  const searchSubscriptionResources = (item: SubscriptionItem) => {
    if (!item.id) return;
    const target = {
      id: Number(item.tmdbId) || 0,
      mediaType: item.mediaType === 'tv' ? 'tv' : 'movie',
      title: item.title,
      year: item.year ?? '',
      posterUrl: item.posterUrl,
      overview: '',
      rating: 0,
      source: 'subscription',
      sourceLabel: item.sourceLabel || '我的订阅',
      sourceId: item.id,
      tmdbId: item.tmdbId,
      seasonNumber: item.seasonNumber
    } satisfies DiscoverResult;
    const detailRequest = detailId === item.id && detail?.detail
      ? Promise.resolve(detail)
      : getSubscriptionDetail(item.id);
    const aliases = detailRequest
      .then((payload) => [
        item.title,
        payload.detail?.title || '',
        payload.detail?.englishTitle || '',
        payload.detail?.originalTitle || ''
      ])
      .catch(() => [item.title]);
    openResourceSearch(target, aliases);
  };

  const closeResourceSearch = () => {
    setResourceTarget(null);
    setResourceData(null);
    setResourceError('');
    setResourceQueries([]);
    setResourcePreview(null);
    resourceRequestRef.current?.abort();
    resourceRequestRef.current = null;
  };

  const updateProvider = (value: string) => {
    setActiveSearch('');
    setSearchPage(1);
    setFilters((current) => ({ ...current, provider: value, page: 1 }));
  };

  const totalPages = Math.max(1, pageInfo.totalPages);
  const canPrev = pageInfo.page > 1;
  const canNext = pageInfo.page < totalPages;
  const filterCount = activeFilterCount(filters);
  const visibleGroups = filterGroups.filter((group) => sourceFilterKeys[filters.source].includes(group.key));
  const visibleResources = useMemo(() => {
    const rows = resourceData?.items ?? [];
    return resourceSource === 'all'
      ? rows
      : rows.filter((item) => (item.source_key || item.source) === resourceSource);
  }, [resourceData, resourceSource]);

  const renderResourcePanel = (variant: 'subscription' | 'discover') => {
    if (!resourceTarget) return null;
    if (variant === 'subscription' && resourceTarget.source !== 'subscription') return null;
    if (variant === 'discover' && resourceTarget.source === 'subscription') return null;
    return (
      <section
        ref={resourcePanelRef}
        aria-label={`${resourceTarget.title} RSS 种子搜索结果`}
        aria-live="polite"
        className={`discover-resource-panel discover-resource-panel--${variant === 'subscription' ? 'inline' : 'grid'}`}
        tabIndex={-1}
      >
        <header className="discover-resource-panel__head">
          <div>
            <small>RSS 种子搜索</small>
            <h2>{resourceTarget.title}</h2>
            <p>{resourceLoading ? '正在查询本地种子箱…' : `已搜索：${resourceQueries.join(' / ')} · ${visibleResources.length} 条`}</p>
          </div>
          <button aria-label="关闭资源搜索" className="tool-link" title="关闭" type="button" onClick={closeResourceSearch}>
            <X aria-hidden="true" size={16} />
          </button>
        </header>
        {resourceLoading && <div className="discover-resource-empty">正在查询本地 RSS 种子箱…</div>}
        {!resourceLoading && resourceError && <div className="discover-resource-empty">{resourceError}</div>}
        {!resourceLoading && resourceData && (
          <>
            {resourceData.sources.length > 0 && (
              <div className="discover-resource-tabs" role="tablist" aria-label="资源来源">
                {resourceData.sources.map((source) => (
                  <button
                    aria-selected={resourceSource === source.key}
                    className={resourceSource === source.key ? 'discover-resource-tab discover-resource-tab--active' : 'discover-resource-tab'}
                    key={source.key}
                    role="tab"
                    tabIndex={resourceSource === source.key ? 0 : -1}
                    type="button"
                    onClick={() => {
                      setResourceSource(source.key);
                      setResourcePreview(null);
                    }}
                    onKeyDown={handleHorizontalTabKeyDown}
                  >
                    {source.label} <span>{source.count}</span>
                  </button>
                ))}
              </div>
            )}
            {resourceData.seasons.length > 0 && (
              <div className="discover-resource-seasons" aria-label="资源季集状态">
                {resourceData.seasons.map((season) => (
                  <div key={season.season}>
                    <strong>S{String(season.season).padStart(2, '0')}</strong>
                    <span>{season.resource_episodes?.length ?? season.episodes.length} / {season.episodes.length} 集</span>
                    {season.notice && <small>{season.notice}</small>}
                  </div>
                ))}
              </div>
            )}
            {resourceData.errors.length > 0 && <p className="discover-resource-notice">{resourceData.errors[0]}</p>}
            <div className="discover-resource-list">
              {visibleResources.map((item, index) => {
                const previewText = resourcePreviewText(item);
                const activePreview = resourcePreview === item;
                return (
                  <article className="discover-resource-row" key={`${item.source_key || item.source || 'rss'}-${item.title || index}-${item.date || index}`}>
                    <div>
                      <strong>{resourceTitle(item)}</strong>
                      <small>{resourceMeta(item) || '来源信息未提供'}</small>
                    </div>
                    <div className="discover-resource-row__actions">
                      <button
                        aria-expanded={activePreview}
                        className="tool-link"
                        disabled={!previewText}
                        type="button"
                        onClick={() => setResourcePreview(activePreview ? null : item)}
                      >
                        <FileSearch aria-hidden="true" size={14} />
                        预览
                      </button>
                    </div>
                    {activePreview && previewText && (
                      <div className="discover-resource-preview"><pre>{previewText}</pre></div>
                    )}
                  </article>
                );
              })}
              {visibleResources.length === 0 && (
                <div className="discover-resource-empty">
                  <span>{variant === 'subscription' ? '没有找到与该订阅身份和季集相符的种子。' : '本地种子箱中没有匹配种子。'}</span>
                  <small>已搜索：{resourceQueries.join(' / ')}</small>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    );
  };

  return (
    <main className={subscriptionsOnly ? 'work-page ops-page ops-page--discover ops-page--subscriptions' : 'work-page ops-page ops-page--discover'}>
      <section className="ops-hero ops-hero--discover">
        <div>
          <p className="ops-eyebrow">{subscriptionsOnly ? '自动获取' : '找片'}</p>
          <h1>{subscriptionsOnly ? '订阅' : '发现'}</h1>
          <p className="ops-discover-subtitle">{subscriptionsOnly ? '管理正在追的电影和剧集。' : '找到想看的内容，加入订阅即可。'}</p>
          <p className="ops-deck">{subscriptionsOnly ? '在这里查看进度、调整季数或重新交给 Torra；后续下载和入库会自动回到任务中心。' : '可以浏览榜单、国内平台和海外流媒体；加入订阅后由 PT 主线继续处理。'}</p>
        </div>
        <div className="ops-discover-policy">
          <span><Database size={15} />默认获取方式</span>
          <strong>PT / Torra</strong>
          <small><Send size={13} />{!subscriptionCapabilities ? '正在确认追更能力' : !torraPushEnabled ? '保存意图，暂不自动获取' : !schedulerRunning ? '保存后等待手动同步' : '保存后进入自动追更'}</small>
        </div>
      </section>

      <div className={subscriptionsOnly ? 'ops-discover-layout ops-discover-layout--subscriptions' : 'ops-discover-layout'}>
      {!subscriptionsOnly && <div>
        <section className="ops-panel discover-source-panel" aria-label="发现来源">
          {sources.map((source) => (
            <button
              aria-pressed={source.id === filters.source}
              className={source.id === filters.source ? 'discover-source discover-source--active' : 'discover-source'}
              key={source.id}
              title={source.label}
              type="button"
              onClick={() => changeSource(source.id)}
            >
              {source.label}
            </button>
          ))}
        </section>

        <section className="ops-panel discover-filter-panel" aria-label="发现筛选">
          <div className="discover-toolbar">
            <form className="discover-search" onSubmit={runSearch}>
              <Search aria-hidden="true" size={15} strokeWidth={1.8} />
              <input
                aria-label="搜索影视"
                placeholder="搜索片名，回车确认"
                ref={searchInputRef}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <button className="tool-link" type="submit">搜索</button>
            </form>
            <button className="tool-link discover-reset" type="button" onClick={resetFilters}>
              <RotateCcw aria-hidden="true" size={14} />
              重置
            </button>
          </div>

          <div className="discover-filter-summary">
            <span><SlidersHorizontal aria-hidden="true" size={14} /> {filterCount} 个筛选</span>
            <span>{activeSearch ? `搜索：${activeSearch}` : pageInfo.sourceLabel}</span>
            <span>{formatCount(pageInfo.totalResults)} 条结果</span>
          </div>

          <div className="discover-filter-grid">
            {visibleGroups.length === 0 && filters.source !== 'streaming' && (
              <div className="discover-filter-row">
                <span>筛选</span>
                <div className="discover-filter-options">
                  <small className="discover-filter-note">该来源是固定剧集榜单，支持搜索和翻页，无筛选维度。</small>
                </div>
              </div>
            )}
            {filters.source === 'streaming' && (
              <div className="discover-filter-row">
                <span>平台</span>
                <div className="discover-filter-options">
                  {streamingPlatforms.map((platform) => {
                    const active = filters.provider === platform.value;
                    return (
                      <button
                        aria-pressed={active}
                        className={active ? 'discover-filter-chip discover-filter-chip--active' : 'discover-filter-chip'}
                        key={platform.value}
                        type="button"
                        onClick={() => updateProvider(platform.value)}
                      >
                        {platform.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            {visibleGroups.map((group) => (
              <div className="discover-filter-row" key={group.key}>
                <span>{group.label}</span>
                <div className="discover-filter-options">
                  {group.options.map((option) => {
                    const active = filters[group.key] === option.value;
                    return (
                      <button
                        aria-pressed={active}
                        className={active ? 'discover-filter-chip discover-filter-chip--active' : 'discover-filter-chip'}
                        key={option.value}
                        type="button"
                        onClick={() => updateFilter(group.key, option.value)}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </section>

        {!configured && (
          <div className="ops-panel ops-empty discover-empty">配置 TMDB_API_KEY 后，这里会显示热门与搜索结果。</div>
        )}
        {configured && loading && <div className="ops-panel ops-empty discover-empty">正在读取内容来源…</div>}
        {configured && !loading && results.length === 0 && (
          <div className="ops-panel ops-empty discover-empty">没有找到内容，换个关键词或筛选试试。</div>
        )}

        <div className="discover-grid">
          {results.map((result) => {
            const tmdbId = tmdbIdForResult(result);
            const canSubscribe = Boolean(tmdbId);
            const subscribed = canSubscribe && subscribedKeys.has(`${result.mediaType}:${tmdbId}`);
            const resourceActive = resourceTarget === result;
            const cardKey = `${result.mediaType}-${result.source || 'tmdb'}-${result.sourceId || result.id}`;
            return (
              <Fragment key={cardKey}>
                <article className="ops-panel discover-card">
                  <PosterImage
                    className="discover-card__poster"
                    fallbackClassName="discover-card__poster--fallback"
                    src={result.posterUrl}
                    title={result.title}
                  />
                  <div className="discover-card__body">
                    <strong title={result.title}>{result.title}</strong>
                    <small>{resultMeta(result)}</small>
                    {result.overview && <p>{result.overview}</p>}
                    <div className="discover-card__actions">
                      <button
                        aria-expanded={resourceActive}
                        className={resourceActive ? 'tool-link discover-card__action discover-card__action--active' : 'tool-link discover-card__action'}
                        type="button"
                        onClick={() => openResourceSearch(result)}
                      >
                        <FileSearch aria-hidden="true" size={14} />
                        {resourceActive ? (resourceLoading ? '查询中' : '查看结果') : '资源'}
                      </button>
                      <button
                        className={subscribed ? 'tool-link discover-card__action discover-card__action--done' : 'tool-link discover-card__action'}
                        disabled={subscribed || !canSubscribe || Boolean(subscriptionAction)}
                        title={canSubscribe ? '保存到我的订阅' : '未匹配到 TMDB，暂不能订阅'}
                        type="button"
                        onClick={() => subscribe(result)}
                      >
                        {subscribed ? <Check aria-hidden="true" size={14} /> : <Plus aria-hidden="true" size={14} />}
                        {subscribed ? '已订阅' : subscriptionAction === `save:${result.mediaType}:${tmdbIdForResult(result)}` ? '保存中' : canSubscribe ? '订阅' : '待匹配'}
                      </button>
                    </div>
                  </div>
                </article>
                {resourceActive && renderResourcePanel('discover')}
              </Fragment>
            );
          })}
        </div>

        {configured && !loading && results.length > 0 && (
          <nav className="discover-pagination" aria-label="发现页分页">
            <button className="tool-link" disabled={!canPrev} type="button" onClick={() => goPage(pageInfo.page - 1)}>
              <ChevronLeft aria-hidden="true" size={14} />
              上一页
            </button>
            <span>第 {pageInfo.page} / {totalPages} 页</span>
            <button className="tool-link" disabled={!canNext} type="button" onClick={() => goPage(pageInfo.page + 1)}>
              下一页
              <ChevronRight aria-hidden="true" size={14} />
            </button>
          </nav>
        )}
      </div>}

      <aside className={subscriptionsOnly ? 'ops-inspector ops-subscription-console discover-subs discover-subs--full' : 'ops-inspector ops-subscription-console discover-subs'} aria-label="我的订阅">
        <div className="activity-panel__head">
          <div><small>自动获取</small><h2>我的订阅</h2></div>
          <span className="queue-count">{subscriptionsOnly ? (workbench?.page.total ?? workbenchStats.total) : subs.length} 条</span>
          <button className="ops-action-button" type="button" onClick={() => onNavigate('subscription-settings')}>
            <SlidersHorizontal aria-hidden="true" size={14} />
            订阅设置
          </button>
          {subscriptionsOnly && <button className="ops-action-button" disabled={subsLoading} title="重新读取工作台状态和订阅链路" type="button" onClick={loadSubs}>
            <RefreshCcw aria-hidden="true" size={14} />
            {subsLoading ? '读取中' : '刷新'}
          </button>}
          <button className="ops-action-button" disabled={Boolean(subscriptionAction) || !localWriteEnabled} title={localWriteEnabled ? '更新已启用的自动订阅来源' : '本地订阅写入已关闭'} type="button" onClick={runSweep}>
            <RefreshCcw aria-hidden="true" size={14} />
            {subscriptionAction === 'run' ? '更新中' : '更新自动订阅来源'}
          </button>
          {subscriptionsOnly && <button className="ops-action-button" type="button" onClick={() => onNavigate('calendar')}>
            <CalendarDays aria-hidden="true" size={14} />
            日历视图
          </button>}
        </div>
        <div className="ops-subscription-policy"><strong>PT 优先</strong><span>Torra 推送保持安全开关控制</span></div>
        {subscriptionsOnly && workbench && (
          <>
            <section className="subscription-capabilities" aria-label="订阅工作台能力状态">
              {workbench.capabilities.map((capability) => (
                <div className={`subscription-capability is-${capability.state}`} key={capability.key} title={capability.detail}>
                  <span aria-hidden="true" />
                  <div><strong>{capability.label}</strong><small>{capability.detail}</small></div>
                </div>
              ))}
            </section>
            <section className="subscription-workbench-summary" aria-label="订阅统计">
              <span><b>{workbenchStats.movie}</b>电影</span>
              <span><b>{workbenchStats.tv}</b>剧集</span>
              <span><b>{workbenchStats.pending}</b>待处理</span>
              <span><b>{workbenchStats.inLibrary}</b>已入库</span>
              <small>{subscriptionReadAtLabel(workbench.lastReadAt)}</small>
            </section>
          </>
        )}
        {sweepMessage && <p className="console-panel__hint">{sweepMessage}</p>}
        {subscriptionsOnly && (
          <section className="torra-sync-panel" aria-label="Torra 订阅同步">
            <header>
              <div>
                <small>Fluxa / Torra 只读对账</small>
                <strong>{reconciliationSummary ? `${reconciliationSummary.localTotal} 条本地 · ${reconciliationSummary.remoteTotal} 条 Torra` : `${torraSyncStatus?.linked ?? 0} 条已关联`}</strong>
              </div>
              <span className={torraSyncStatus?.enabled ? 'is-enabled' : undefined}>
                {torraSyncStatus?.enabled ? '镜像同步已开启' : '当前只读'}
              </span>
            </header>
            {reconciliationSummary ? (
              <div className="torra-sync-panel__status torra-sync-panel__status--reconciliation">
                <span><b>{reconciliationSummary.reconciliation.linked}</b>已关联</span>
                <span><b>{reconciliationSummary.reconciliation.only_fluxa}</b>仅 Fluxa</span>
                <span><b>{reconciliationSummary.reconciliation.only_torra}</b>仅 Torra</span>
                <span><b>{reconciliationSummary.reconciliation.conflict}</b>存在冲突</span>
                <span><b>{reconciliationSummary.reconciliation.remote_missing}</b>远端已消失</span>
              </div>
            ) : (
              <div className="torra-sync-panel__status">
                <span><b>{torraSyncStatus?.current ?? 0}</b>当前有效</span>
                <span><b>{torraSyncStatus?.remoteMissing ?? 0}</b>远端缺失</span>
                <span><b>{torraSyncStatus?.lastSyncedAt ? subscriptionUpdateLabel(torraSyncStatus.lastSyncedAt) : '尚未'}</b>最近同步</span>
              </div>
            )}
            {torraSyncPreview && (
              <div className="torra-sync-panel__preview">
                <span>远端 <b>{torraSyncPreview.summary.total}</b></span>
                <span>新增 <b>{torraSyncPreview.summary.new}</b></span>
                <span>已关联 <b>{torraSyncPreview.summary.linked}</b></span>
                <span>重复 <b>{torraSyncPreview.summary.duplicates}</b></span>
                <span>无法识别 <b>{torraSyncPreview.summary.unmapped}</b></span>
              </div>
            )}
            {!torraSyncStatus?.enabled && <p>对账为只读；未确认导入前，不会修改 Fluxa 台账或 Torra 远端订阅。</p>}
            {torraSyncMessage && <p role="status">{torraSyncMessage}</p>}
            <footer>
              <button className="ops-action-button" disabled={Boolean(torraSyncBusy)} type="button" onClick={previewTorraMirror}>
                <Database aria-hidden="true" size={14} />
                {torraSyncBusy === 'preview' ? '读取中' : '预览订阅'}
              </button>
              {torraSyncPreview && torraSyncPreview.summary.importable > 0 && (
                <button className="ops-action-button ops-action-button--primary" disabled={Boolean(torraSyncBusy) || !torraSyncStatus?.enabled || torraSyncPreview.summary.conflicts > 0} type="button" onClick={importTorraMirror}>
                  <Download aria-hidden="true" size={14} />
                  {torraSyncBusy === 'import' ? '导入中' : '确认导入'}
                </button>
              )}
              {(torraSyncStatus?.linked ?? 0) > 0 && (
                <button className="ops-action-button" disabled={Boolean(torraSyncBusy) || !torraSyncStatus?.enabled} type="button" onClick={refreshTorraMirror}>
                  <RefreshCcw aria-hidden="true" size={14} />
                  {torraSyncBusy === 'sync' ? '同步中' : '同步状态'}
                </button>
              )}
              {!torraSyncStatus?.enabled && <button className="tool-link" type="button" onClick={() => onNavigate('settings')}>前往设置</button>}
            </footer>
          </section>
        )}
        <div className="discover-sub-tabs" role="tablist" aria-label="订阅类型">
          {([
            ['movie', '电影订阅', subscriptionsOnly ? workbenchStats.movie : subs.filter((item) => item.mediaType === 'movie').length],
            ['tv', '电视剧订阅', subscriptionsOnly ? workbenchStats.tv : subs.filter((item) => item.mediaType === 'tv').length],
            ['blocked', '被屏蔽', blockedTitles.length]
          ] as const).map(([key, label, count]) => (
            <button
              aria-selected={subscriptionTab === key}
              className={subscriptionTab === key ? 'discover-sub-tab discover-sub-tab--active' : 'discover-sub-tab'}
              key={key}
              role="tab"
              tabIndex={subscriptionTab === key ? 0 : -1}
              type="button"
              onClick={() => {
                setSubscriptionTab(key);
                closeDetail();
              }}
              onKeyDown={handleHorizontalTabKeyDown}
            >
              {label}<span>{count}</span>
            </button>
          ))}
        </div>

        {subscriptionTab !== 'blocked' && (
          <div className="discover-sub-filters">
            <label className="discover-sub-search">
              <Search aria-hidden="true" size={13} />
              <input
                aria-label="搜索订阅标题或关键词"
                placeholder="搜索标题、来源或 TMDB ID"
                type="search"
                value={subscriptionKeyword}
                onChange={(event) => setSubscriptionKeyword(event.target.value)}
              />
            </label>
            <div className="discover-sub-filter-row">
              <span>状态</span>
              {([['all', '全部'], ['pending', '未完成'], ['done', '已完成']] as const).map(([value, label]) => (
                <button
                  className={subscriptionStatus === value ? 'is-active' : undefined}
                  key={value}
                  type="button"
                  onClick={() => setSubscriptionStatus(value)}
                >{label}</button>
              ))}
            </div>
            <div className="discover-sub-filter-row">
              <span>更新</span>
              {([['all', '全部'], ['today', '今日'], ['3', '三日'], ['7', '七日']] as const).map(([value, label]) => (
                <button
                  className={subscriptionUpdate === value ? 'is-active' : undefined}
                  key={value}
                  type="button"
                  onClick={() => setSubscriptionUpdate(value)}
                >{label}</button>
              ))}
              <select aria-label="订阅年份" value={subscriptionYear} onChange={(event) => setSubscriptionYear(event.target.value)}>
                <option value="all">全部年份</option>
                {subscriptionYears.map((year) => <option key={year} value={year}>{year}</option>)}
              </select>
            </div>
          </div>
        )}

        {subsLoading && <p className="console-panel__hint">正在读取订阅工作台…</p>}
        {subsError && (
          <div className="subscription-read-error" role="alert">
            <span>{subsError}</span>
            <button className="ops-action-button" disabled={subsLoading} type="button" onClick={loadSubs}>
              <RefreshCcw aria-hidden="true" size={14} />重试
            </button>
          </div>
        )}

        {!subsLoading && !subsError && subscriptionTab === 'blocked' && blockedTitles.length === 0 && (
          <p className="console-panel__hint">暂无被屏蔽订阅。</p>
        )}
        {!subsLoading && !subsError && subscriptionTab === 'blocked' && blockedTitles.map((title) => (
          <div className="discover-sub-blocked" key={title}>
            <div><Ban aria-hidden="true" size={14} /><span><strong>{title}</strong><small>自动订阅会跳过这个标题</small></span></div>
            <button className="tool-link" disabled={Boolean(subscriptionAction) || !localWriteEnabled} type="button" onClick={() => unblockItem(title)}>取消屏蔽</button>
          </div>
        ))}

        {!subsLoading && !subsError && subscriptionsOnly && subscriptionTab !== 'blocked' && subs.length === 0 && (
          <section className="subscription-empty-guide" aria-label="导入 Torra 订阅引导">
            <Database aria-hidden="true" size={24} />
            <div>
              <strong>本地订阅台账为空</strong>
              <p>先只读预览 Torra 现有订阅，确认数量和冲突后再导入 Fluxa。第一阶段不会修改或删除 Torra 远端订阅。</p>
            </div>
            <ol>
              <li className={torraSyncPreview ? 'is-complete' : 'is-current'}><b>1</b><span>预览 Torra 订阅</span></li>
              <li className={torraSyncPreview ? 'is-current' : undefined}><b>2</b><span>检查新增、重复和冲突</span></li>
              <li><b>3</b><span>明确确认后导入本地台账</span></li>
            </ol>
            <footer>
              <button className="ops-action-button" disabled={Boolean(torraSyncBusy)} type="button" onClick={previewTorraMirror}>
                <Database aria-hidden="true" size={14} />
                {torraSyncBusy === 'preview' ? '读取中' : '预览 Torra 订阅'}
              </button>
              {torraSyncPreview && torraSyncPreview.summary.importable > 0 && (
                <button className="ops-action-button ops-action-button--primary" disabled={Boolean(torraSyncBusy) || !torraSyncStatus?.enabled || torraSyncPreview.summary.conflicts > 0} type="button" onClick={importTorraMirror}>
                  <Download aria-hidden="true" size={14} />
                  {torraSyncBusy === 'import' ? '导入中' : `确认导入 ${torraSyncPreview.summary.importable} 条`}
                </button>
              )}
              {!torraSyncStatus?.enabled && <button className="tool-link" type="button" onClick={() => onNavigate('settings')}>先开启镜像同步</button>}
            </footer>
          </section>
        )}
        {!subsLoading && !subsError && subscriptionTab !== 'blocked' && subs.length > 0 && visibleSubscriptions.length === 0 && (
          <p className="console-panel__hint">当前筛选下没有订阅内容。</p>
        )}
        {subscriptionTab !== 'blocked' && visibleSubscriptions.map((item) => {
          const seasons = detailId === item.id ? detail?.seasons ?? [] : [];
          const activeSeason = seasons.find((season) =>
            (season.seasonNumber ?? season.season_number ?? 0) === detailSeason
          ) ?? seasons[0];
          const activeSeasonNumber = activeSeason?.seasonNumber ?? activeSeason?.season_number ?? 0;
          const detailInfo = detailId === item.id ? detail?.detail : null;
          const libraryProgress = detailInfo?.inLibrary || item.inLibrary
            ? '已完成入库'
            : item.mediaType === 'tv' && detailInfo?.episodeCount
              ? `${detailInfo.libraryEpisodeCount ?? 0}/${detailInfo.episodeCount} 集已入库`
              : item.progressText || '等待首个入库记录';
          const subscriptionScope = item.mediaType === 'tv'
            ? item.seasonName || (item.seasonNumber != null ? `第 ${item.seasonNumber} 季` : '按剧集持续追更')
            : '整部电影';
          const torraRoute = item.readOnly
            ? item.torraSyncState === 'remote_missing' ? 'Torra 远端已缺失' : 'Torra 已有订阅，只读同步'
            : '由 Fluxa 管理，可检查后推送';
          return (
            <div
              className={detailId === item.id ? 'discover-sub discover-sub--open' : 'discover-sub'}
              data-subscription-id={item.id}
              key={item.id ?? item.title}
            >
              <div className="activity-row">
                <PosterImage
                  className="discover-sub__poster"
                  fallbackClassName="discover-sub__poster--fallback"
                  src={item.posterUrl}
                  title={item.title}
                />
                <button
                  className="activity-row__text discover-sub__open"
                  title={item.mediaType === 'tv' ? '查看季集详情' : '查看详情'}
                  type="button"
                  onClick={() => openDetail(item)}
                >
                  <strong>{item.title}</strong>
                  <small>
                    {item.mediaType === 'tv' ? '剧集' : '电影'}
                    {' · PT'}
                    {item.year && ` · ${item.year}`}
                    {item.seasonName && ` · ${item.seasonName}`}
                    {item.progressText && ` · 进度 ${item.progressText}`}
                    {item.inLibrary && ' · 已入库'}
                  </small>
                  <em>{item.readOnly ? '来自 Torra · 只读' : item.sourceLabel || 'Fluxa'} · {subscriptionUpdateLabel(item.updatedAt)}</em>
                </button>
                <button
                  aria-label={`搜索 ${item.title} 的资源`}
                  className="tool-link"
                  title="只读搜索资源"
                  type="button"
                  onClick={() => searchSubscriptionResources(item)}
                >
                  <FileSearch aria-hidden="true" size={14} />
                </button>
                {!item.readOnly && <button
                  aria-label={`检查并推送 ${item.title} 到 Torra`}
                  className="tool-link"
                  disabled={Boolean(torraPushBusy) || !localWriteEnabled}
                  title="先读取分类、保存路径和 Torra 查重结果"
                  type="button"
                  onClick={() => {
                    if (detailId !== item.id) openDetail(item);
                    previewTorraPush(item);
                  }}
                >
                  <Send aria-hidden="true" size={14} />
                </button>}
                {!item.readOnly && <button
                  aria-label={`屏蔽订阅 ${item.title}`}
                  className="tool-link"
                  disabled={Boolean(subscriptionAction) || !localWriteEnabled}
                  title="删除并屏蔽：自动订阅不再加回"
                  type="button"
                  onClick={() => blockItem(item)}
                >
                  <Ban aria-hidden="true" size={14} />
                </button>}
                {!item.readOnly && <button aria-label={`删除订阅 ${item.title}`} className="tool-link" disabled={Boolean(subscriptionAction) || !localWriteEnabled} title={localWriteEnabled ? '只删除，不加入屏蔽列表' : '本地订阅写入已关闭'} type="button" onClick={() => removeSubscription(item)}>
                  <Trash2 aria-hidden="true" size={14} />
                </button>}
              </div>

              {subscriptionsOnly && (
                <div className="discover-sub__chain" aria-label={`${item.title} 处理状态`}>
                  <span className={item.reconciliationState === 'linked' ? 'is-ok' : ['conflict', 'remote_missing'].includes(item.reconciliationState ?? '') ? 'is-warn' : undefined} title={item.reasonText}>
                    <b>对账状态</b><small>{reconciliationLabel(item)}</small>
                  </span>
                  <span className={item.fulfillmentState === 'completed' ? 'is-ok' : item.fulfillmentState === 'blocked' ? 'is-warn' : undefined}>
                    <b>履约状态</b><small>{fulfillmentLabel(item)}</small>
                  </span>
                  <span>
                    <b>健康状态</b><HealthBadge label={item.healthState ? undefined : '尚未确认'} state={item.healthState || 'evidence_insufficient'} />
                  </span>
                  <span><b>范围</b><small>{item.scope || subscriptionScope}</small></span>
                  <span className={(item.missingEpisodes?.length ?? 0) > 0 ? 'is-warn' : undefined}>
                    <b>缺集</b><small>{item.missingEpisodes?.length ? item.missingEpisodes.join('、') : item.inLibrary ? '无' : '尚未确认'}</small>
                  </span>
                  <span
                    className={item.reconciliationState === 'linked' ? 'is-ok' : ['conflict', 'remote_missing'].includes(item.reconciliationState ?? '') ? 'is-warn' : undefined}
                    title={item.reasonText || item.torra?.detail}
                  >
                    <b>对账</b><small>{reconciliationLabel(item)}</small>
                  </span>
                  <span className={item.qb?.status === 'blocked' ? 'is-warn' : item.qb?.status === 'done' || item.qb?.status === 'active' ? 'is-ok' : undefined} title={item.qb?.detail}>
                    <b>qB</b><small>{item.qb?.detail || '未接入'}</small>
                  </span>
                  <span className={item.cloud115?.status === 'blocked' ? 'is-warn' : item.cloud115?.status === 'done' ? 'is-ok' : undefined} title={item.cloud115?.detail}>
                    <b>115</b><small>{item.cloud115?.detail || '未接入'}</small>
                  </span>
                  <span className={item.library?.status === 'done' || item.inLibrary ? 'is-ok' : item.library?.status === 'blocked' ? 'is-warn' : undefined} title={item.library?.detail}>
                    <b>入库</b><small>{item.library?.detail || (item.inLibrary ? '已入库' : '等待中')}</small>
                  </span>
                  {(item.blockingReason || item.reasonText) && <p><strong>当前状态</strong>{item.blockingReason || item.reasonText}</p>}
                </div>
              )}

              {resourceTarget?.source === 'subscription' && resourceTarget.sourceId === item.id && renderResourcePanel('subscription')}

              {detailId === item.id && (
                <div className="sub-detail">
                  {detailLoading && <small className="sub-detail__hint">详情加载中…</small>}
                  {!detailLoading && (!detail || !detail.success) && (
                    <small className="sub-detail__hint">详情加载失败，稍后再试。</small>
                  )}
                  {!detailLoading && detail?.success && !detail.detail && (
                    <small className="sub-detail__hint">没有 TMDB 匹配，暂无详情。</small>
                  )}
                  {!detailLoading && detail?.success && detail.detail && (
                    <>
                      <div className="sub-detail__summary">
                        <strong>{detailInfo?.title || item.title}</strong>
                        <span className={detailInfo?.inLibrary || item.inLibrary ? 'is-library' : 'is-pending'}>
                          {detailInfo?.inLibrary || item.inLibrary ? '已入库' : '待补'}
                        </span>
                        <small>
                          {[detailInfo?.year || item.year, detailInfo?.rating ? `评分 ${detailInfo.rating}` : '', detailInfo?.runtime]
                            .filter(Boolean).join(' · ') || '暂无详细元数据'}
                        </small>
                        {detailInfo?.overview && <p>{detailInfo.overview}</p>}
                      </div>
                      <div className="sub-detail__meta">
                        <span><b>TMDB</b>{detailInfo?.tmdbId || item.tmdbId || '-'}</span>
                        <span><b>类型</b>{detailInfo?.genres?.join(' / ') || '-'}</span>
                        <span><b>国家 / 语言</b>{[detailInfo?.country, detailInfo?.language].filter(Boolean).join(' / ') || '-'}</span>
                        <span><b>日期</b>{detailInfo?.date || detailInfo?.release_date || detailInfo?.first_air_date || '-'}</span>
                      </div>
                      <section className="sub-detail__route" aria-label="订阅处理轨道">
                        <header>
                          <div><strong>订阅处理轨道</strong><small>从订阅到入库的当前状态</small></div>
                          <span>{subscriptionUpdateLabel(item.updatedAt)}</span>
                        </header>
                        <div className="sub-detail__route-grid">
                          <span><b>01</b><strong>订阅来源</strong><small>{item.readOnly ? 'Torra 镜像' : item.sourceLabel || 'Fluxa'}</small></span>
                          <span><b>02</b><strong>订阅范围</strong><small>{subscriptionScope}</small></span>
                          <span><b>03</b><strong>PT / Torra</strong><small>{torraRoute}</small></span>
                          <span className={detailInfo?.inLibrary || item.inLibrary ? 'is-complete' : undefined}><b>04</b><strong>整理入库</strong><small>{libraryProgress}</small></span>
                        </div>
                        <footer>
                          <button className="tool-link" type="button" onClick={() => searchSubscriptionResources(item)}>
                            <FileSearch aria-hidden="true" size={13} />搜索资源
                          </button>
                          <button
                            className="tool-link"
                            type="button"
                            onClick={() => onNavigate('tasks', {
                              subscriptionId: item.id,
                              tmdbId: detailInfo?.tmdbId || item.tmdbId,
                              title: detailInfo?.title || item.title,
                              seasonNumber: item.seasonNumber ?? detailSeason
                            })}
                          >
                            <Database aria-hidden="true" size={13} />查看任务中心
                          </button>
                          {!item.readOnly && (
                            <button className="ops-action-button ops-action-button--primary" disabled={Boolean(torraPushBusy) || !localWriteEnabled} type="button" onClick={() => previewTorraPush(item)}>
                              <Send aria-hidden="true" size={13} />检查 Torra 推送
                            </button>
                          )}
                        </footer>
                      </section>
                      <div className="sub-detail__section">
                        <strong>整体入库路径</strong>
                        {(detailInfo?.libraryPaths ?? []).length > 0 ? (
                          <div className="sub-detail__paths">{detailInfo?.libraryPaths?.map((path) => <code key={path}>{path}</code>)}</div>
                        ) : <small className="sub-detail__hint">暂无入库路径</small>}
                      </div>
                      <div className="sub-detail__section">
                        <strong>演员</strong>
                        {(detailInfo?.cast ?? []).length > 0 ? (
                          <div className="sub-detail__cast">
                            {detailInfo?.cast?.map((person) => (
                              <div key={`${person.name}-${person.character}`}>
                                <PosterImage className="sub-detail__cast-poster" src={person.profileUrl} title={person.name} />
                                <strong title={person.name}>{person.name}</strong><small title={person.character || '演员'}>{person.character || '演员'}</small>
                              </div>
                            ))}
                          </div>
                        ) : <small className="sub-detail__hint">暂无演员信息</small>}
                      </div>
                      {item.mediaType === 'tv' && seasons.length > 0 && (
                        <div className="sub-detail__seasons" role="tablist" aria-label="季选择">
                          {seasons.map((season) => {
                            const seasonNumber = season.seasonNumber ?? season.season_number ?? 0;
                            return (
                              <button
                                aria-selected={activeSeasonNumber === seasonNumber}
                                className={activeSeasonNumber === seasonNumber ? 'discover-filter-chip discover-filter-chip--active' : 'discover-filter-chip'}
                                key={seasonNumber}
                                role="tab"
                                tabIndex={activeSeasonNumber === seasonNumber ? 0 : -1}
                                type="button"
                                onClick={() => setDetailSeason(seasonNumber)}
                                onKeyDown={handleHorizontalTabKeyDown}
                              >
                                {seasonNumber === 0 ? '特别篇' : `S${String(seasonNumber).padStart(2, '0')}`}
                              </button>
                            );
                          })}
                        </div>
                      )}
                      {item.mediaType === 'tv' && activeSeason && (
                        <div className="sub-detail__episodes">
                          <div className="sub-detail__season-head">
                            <strong>{activeSeason.name || (activeSeasonNumber === 0 ? '特别篇' : `第 ${activeSeasonNumber} 季`)}</strong>
                            <small>{activeSeason.libraryCount ?? 0}/{activeSeason.episodeCount || activeSeason.episodes.length || '?'} 集入库</small>
                          </div>
                          {activeSeason.episodes.map((episode) => {
                            const episodeNumber = episode.episodeNumber ?? episode.episode_number ?? 0;
                            return (
                            <div className="sub-detail__episode" key={episodeNumber}>
                              <b>E{String(episodeNumber).padStart(2, '0')}</b>
                              <span>{episode.title || episode.name || '未定名'}</span>
                              <small>{episode.inLibrary ? '已入库' : episode.airDate || episode.air_date || '待定'}</small>
                              {(episode.libraryPaths ?? []).map((path) => <code key={path}>{path}</code>)}
                            </div>
                            );
                          })}
                          {activeSeason.episodes.length === 0 && (
                            <small className="sub-detail__hint">这一季还没有分集信息。</small>
                          )}
                        </div>
                      )}
                      {!item.readOnly && item.mediaType === 'tv' &&
                        activeSeasonNumber !== (item.seasonNumber ?? activeSeasonNumber) && (
                          <button
                            className="tool-link"
                            disabled={Boolean(subscriptionAction) || !localWriteEnabled}
                            title="通过 NasEmby 原保存接口更新订阅季"
                            type="button"
                            onClick={() => changeSeason(item, activeSeasonNumber, activeSeason.name)}
                          >
                            <Check aria-hidden="true" size={14} />
                            改为订阅第 {activeSeasonNumber} 季
                          </button>
                      )}
                    </>
                  )}
                  <section className="sub-detail__section quality-watch-panel">
                    <div className="quality-watch-panel__head">
                      <div><strong>质量观察与人工追更</strong><small>{qualityWatch ? `${qualityWatch.policy.windowHours} 小时观察窗口` : '读取中'}</small></div>
                      {qualityWatch && (
                        <span className={qualityWatch.paused ? 'state-chip' : 'state-chip state-chip--ok'}>
                          {qualityWatch.paused ? '已暂停' : '观察中'}
                        </span>
                      )}
                    </div>
                    {qualityWatch && qualityWatch.units.length > 0 ? (
                      <>
                        {qualityWatch.units.length > 1 && (
                          <label className="quality-watch-panel__unit">
                            观察单元
                            <select aria-label="选择质量观察单元" value={selectedUnitId} onChange={(event) => setSelectedUnitId(event.target.value)}>
                              {qualityWatch.units.map((unit) => <option key={unit.id} value={unit.id}>{unitLabel(unit)} · {watchStateLabel(unit.state)}</option>)}
                            </select>
                          </label>
                        )}
                        <div className="quality-watch-panel__units">
                          {qualityWatch.units.slice(0, 4).map((unit) => (
                            <span key={unit.id}><b>{unitLabel(unit)}</b><small>{watchStateLabel(unit.state)}</small></span>
                          ))}
                        </div>
                        <div className="quality-watch-panel__actions">
                          <button
                            className="tool-link"
                            disabled={qualityWatchBusy === `update:${item.id}` || Boolean(automationAction && !terminalAutomationStates.has(automationAction.status))}
                            type="button"
                            onClick={() => updateQualityWatch(item, { paused: !qualityWatch.paused })}
                          >
                            {qualityWatch.paused ? <Play size={13} /> : <Pause size={13} />}
                            {qualityWatch.paused ? '恢复观察' : '暂停观察'}
                          </button>
                          <button
                            className="tool-link"
                            disabled={qualityWatchBusy === `update:${item.id}` || Boolean(automationAction && !terminalAutomationStates.has(automationAction.status))}
                            type="button"
                            onClick={() => {
                              const windowHours = qualityWatch.policy.windowHours === 24 ? 48 : 24;
                              updateQualityWatch(item, { windowHours, scheduleMinutes: windowHours === 24 ? [720, 1440] : [720, 1440, 2880] });
                            }}
                          >
                            <RotateCcw size={13} />切换 {qualityWatch.policy.windowHours === 24 ? '48' : '24'} 小时窗口
                          </button>
                          <button
                            className="ops-action-button ops-action-button--primary"
                            disabled={Boolean(qualityWatchBusy) || Boolean(automationAction && !terminalAutomationStates.has(automationAction.status)) || (qualityWatch.units.length > 1 && !selectedUnitId)}
                            type="button"
                            onClick={() => startAnalysis(item)}
                          >
                            <RefreshCcw size={13} />{qualityWatchBusy === `analysis:${item.id}` ? '正在提交' : '人工分析升级'}
                          </button>
                          {automationAction?.status === 'succeeded' && automationAction.type === 'rewash-analysis' && (automationAction.result?.selectedCount ?? 0) > 0 && (
                            <button
                              className="ops-action-button"
                              disabled={Boolean(qualityWatchBusy)}
                              type="button"
                              onClick={() => startDownload(item)}
                            >
                              <Download size={13} />{qualityWatchBusy === `download:${item.id}` ? '正在提交' : '下载升级候选'}
                            </button>
                          )}
                        </div>
                        {automationAction && <p className="quality-watch-panel__status" role="status">{automationStatusLabel(automationAction)}</p>}
                      </>
                    ) : (
                      <small className="sub-detail__hint">当前没有可操作的观察单元，等待首个版本或入库基线。</small>
                    )}
                    {qualityWatchMessage && <p className="quality-watch-panel__status" role="status">{qualityWatchMessage}</p>}
                  </section>
                  <section className="sub-detail__section moviepilot-backup-panel">
                    <div className="quality-watch-panel__head">
                      <div><strong>MoviePilot 备用通道</strong><small>仅在 Torra 观察结束且主链空闲时可用</small></div>
                      {moviePilotPreview && <span className={moviePilotPreview.ready ? 'state-chip state-chip--ok' : 'state-chip'}>{moviePilotPreview.ready ? '可以备用' : '当前阻塞'}</span>}
                    </div>
                    <div className="quality-watch-panel__actions">
                      <button className="tool-link" disabled={Boolean(moviePilotBusy)} type="button" onClick={() => previewMoviePilot(item)}>
                        <SlidersHorizontal size={13} />{moviePilotBusy === `preview:${item.id}` ? '正在检查' : '检查备用条件'}
                      </button>
                      {moviePilotPreview?.ready && (
                        <button className="ops-action-button ops-action-button--primary" disabled={Boolean(moviePilotBusy)} type="button" onClick={() => confirmMoviePilot(item)}>
                          <Send size={13} />{moviePilotBusy === `push:${item.id}` ? '正在推送' : '确认备用推送'}
                        </button>
                      )}
                    </div>
                    {moviePilotPreview && <small className="sub-detail__hint">{moviePilotPreview.ready ? `模式：${moviePilotPreview.mode === 'search-existing' ? '已有订阅触发搜索' : '创建订阅并触发搜索'}` : moviePilotPreview.blockers.join('；')}</small>}
                    {moviePilotMessage && <p className="quality-watch-panel__status" role="status">{moviePilotMessage}</p>}
                  </section>
                  {torraPushBusy === `preview:${item.id}` && (
                    <small className="sub-detail__hint">正在读取 Torra 分类、路径和在线查重结果…</small>
                  )}
                  {torraPushPreview?.subscription.id === item.id && (
                    <section className={torraPushPreview.preview.ready ? 'torra-push-panel is-ready' : 'torra-push-panel is-blocked'}>
                      <header>
                        <span>推送前检查</span>
                        <strong>{torraPushPreview.preview.ready ? '可以推送' : '当前不可推送'}</strong>
                      </header>
                      <dl>
                        <div><dt>媒体身份</dt><dd>{item.mediaType === 'tv' ? `剧集${item.seasonNumber ? ` · S${String(item.seasonNumber).padStart(2, '0')}` : ''}` : '电影'} · TMDB {item.tmdbId || '-'}</dd></div>
                        <div><dt>统一分类</dt><dd>{torraPushPreview.preview.category?.label || '待人工分类'}</dd></div>
                        <div><dt>保存路径</dt><dd><code>{torraPushPreview.preview.savePath || '尚未生成'}</code></dd></div>
                        <div><dt>在线查重</dt><dd>{torraPushPreview.preview.duplicate?.found ? `已存在：${torraPushPreview.preview.duplicate.name || torraPushPreview.preview.duplicate.subscriptionId}` : torraPushPreview.preview.duplicate?.checked ? '未发现重复订阅' : '尚未完成'}</dd></div>
                      </dl>
                      {torraPushPreview.preview.blockers.length > 0 && (
                        <ul>{torraPushPreview.preview.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
                      )}
                      {torraPushPreview.preview.warnings.length > 0 && (
                        <p>{torraPushPreview.preview.warnings.join('；')}</p>
                      )}
                      <div>
                        <button className="tool-link" disabled={Boolean(torraPushBusy)} type="button" onClick={() => setTorraPushPreview(null)}>关闭预览</button>
                        {torraPushPreview.preview.ready && (
                          <button className="ops-action-button ops-action-button--primary" disabled={Boolean(torraPushBusy) || !localWriteEnabled} type="button" onClick={() => confirmTorraPush(item)}>
                            <Send size={14} />{torraPushBusy === `push:${item.id}` ? '正在推送' : '确认推送到 Torra'}
                          </button>
                        )}
                      </div>
                    </section>
                  )}
                  {torraPushMessage && <p className="console-panel__hint" role="status">{torraPushMessage}</p>}
                </div>
              )}
            </div>
          );
        })}
        {subscriptionsOnly && subscriptionTab !== 'blocked' && workbench?.page.hasMore && (
          <div className="subscription-page-more">
            <span>已读取 {subs.length} / {workbench.page.total} 条</span>
            <button className="ops-action-button" disabled={subsMoreLoading} type="button" onClick={loadMoreSubs}>
              <RefreshCcw aria-hidden="true" size={14} />
              {subsMoreLoading ? '读取中' : '加载更多追更'}
            </button>
          </div>
        )}
      </aside>
      </div>
      <ConfirmDialog
        open={Boolean(confirmation)}
        labelledBy="discover-confirm-title"
        describedBy="discover-confirm-description"
        onClose={() => setConfirmation(null)}
      >
        {confirmation && (
          <>
            <span className={confirmation.destructive ? 'ops-confirm-dialog__signal ops-confirm-dialog__signal--danger' : 'ops-confirm-dialog__signal'}>{confirmation.signal}</span>
            <h2 id="discover-confirm-title">{confirmation.title}</h2>
            <p id="discover-confirm-description">{confirmation.description}</p>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" type="button" onClick={() => setConfirmation(null)}>取消</button>
              <button className={confirmation.destructive ? 'ops-action-button ops-action-button--danger' : 'ops-action-button ops-action-button--primary'} data-dialog-initial-focus type="button" onClick={() => {
                const action = confirmation.onConfirm;
                setConfirmation(null);
                action();
              }}>{confirmation.confirmLabel}</button>
            </div>
          </>
        )}
      </ConfirmDialog>
    </main>
  );
}
