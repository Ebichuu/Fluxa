import { Fragment, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Ban, Check, ChevronLeft, ChevronRight, Database, Download, FileSearch, Pause, Play, Plus, RefreshCcw, RotateCcw, Search, Send, SlidersHorizontal, Trash2, X } from 'lucide-react';
import { currentHistoryEntryIs, writeUrlQuery, type UrlHistoryMode } from '../../app/urlState';
import {
  backfillSubscriptionVisuals,
  blockSubscription,
  browseDiscover,
  createRssMatch,
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
  startRssMatchAnalysis,
  startRssMatchDownload,
  unblockSubscription,
  updateSubscriptionQualityWatch
} from '../../services/api';
import type { AutomationAction, RssMatch, RssSeedItem, RssSeedListResponse } from '../../types/rssSeedLibrary';
import {
  classifyRssResourceScope,
  countRssResourceScopes,
  rssMatchMethodLabel,
  rssResourceScopeLabel,
  rssResourceScopeSummaryText
} from '../../types/rssSeedLibrary';
import type {
  DiscoverBrowseParams,
  DiscoverResourceItem,
  DiscoverResourceResponse,
  DiscoverSourceStatus,
  ManualFollowProvider,
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
import { createIdempotencyKey } from '../../utils/idempotency';
import type { AppNavigate, TaskNavigationTarget } from '../layout/AppTopNav';
import { HealthBadge } from '../status/HealthBadge';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import { PosterImage } from '../layout/PosterImage';
import { RelativeTime } from '../status/RelativeTime';

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

interface DiscoverUrlState {
  filters: DiscoverBrowseParams;
  query: string;
  page: number;
}

function filterOptionValue(key: FilterKey, value: string | null, fallback: string) {
  return filterGroups.find((group) => group.key === key)?.options.some((option) => option.value === value)
    ? value as string
    : fallback;
}

function readDiscoverUrlState(location: Location = window.location): DiscoverUrlState {
  const query = new URLSearchParams(location.search);
  const sourceValue = query.get('source');
  const source = sources.some((item) => item.id === sourceValue) ? sourceValue as DiscoverSource : defaultFilters.source;
  const typeValue = query.get('type');
  const type = forcedTypeForSource(source, typeValue === 'movie' || typeValue === 'tv' ? typeValue : defaultFilters.type);
  const parsedPage = Number(query.get('page'));
  const page = Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  return {
    filters: {
      ...defaultFilters,
      source,
      type,
      trend: filterOptionValue('trend', query.get('trend'), defaultFilters.trend) as DiscoverBrowseParams['trend'],
      sort: filterOptionValue('sort', query.get('sort'), defaultFilters.sort),
      language: filterOptionValue('language', query.get('language'), defaultFilters.language),
      year: filterOptionValue('year', query.get('year'), defaultFilters.year),
      genre: filterOptionValue('genre', query.get('genre'), defaultFilters.genre),
      provider: streamingPlatforms.some((item) => item.value === query.get('provider')) ? query.get('provider') as string : defaultFilters.provider,
      page
    },
    query: query.get('q')?.trim() ?? '',
    page
  };
}

function writeDiscoverUrlState(state: DiscoverUrlState, mode: UrlHistoryMode) {
  writeUrlQuery({
    q: state.query || null,
    source: state.filters.source === defaultFilters.source ? null : state.filters.source,
    type: state.filters.type === defaultFilters.type ? null : state.filters.type,
    trend: state.filters.trend === defaultFilters.trend ? null : state.filters.trend,
    sort: state.filters.sort === defaultFilters.sort ? null : state.filters.sort,
    language: state.filters.language === defaultFilters.language ? null : state.filters.language,
    year: state.filters.year === defaultFilters.year ? null : state.filters.year,
    genre: state.filters.genre === defaultFilters.genre ? null : state.filters.genre,
    provider: state.filters.source === 'streaming' && state.filters.provider !== defaultFilters.provider ? state.filters.provider : null,
    page: state.page > 1 ? state.page : null
  }, mode);
}

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

function resourceEvidenceLines(item: DiscoverResourceItem) {
  if (!item.rssItemId) return [];
  const identityKnown = item.identityStatus === 'identified';
  return [
    `匹配原因：${rssMatchMethodLabel(item.matchMethod, item.matchConfidence)}`,
    `资源范围：${item.scope ? rssResourceScopeLabel(item.scope) : '范围待确认'}`,
    '官种：无法判断',
    '下载 / 入库 / 重复：尚未确认',
    '当前处理状态：未处理',
    `优先检查理由：${identityKnown && item.scope === 'explicit_episode'
      ? '精确身份优先 · 明确季集优先'
      : identityKnown
        ? '精确身份优先'
        : item.scope === 'explicit_episode'
          ? '明确季集优先'
          : '暂无优先证据；最终下载推荐只来自 Torra 分析评分'}`
  ];
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
    const scope = classifyRssResourceScope(item);
    return {
      rssItemId: item.id,
      source: sourceKey,
      source_key: sourceKey,
      source_label: item.sourceName || 'RSS',
      title: item.title,
      subtitle: [
        rssEpisodeLabel(item),
        rssResourceScopeLabel(scope)
      ].filter(Boolean).join(' · '),
      quality: item.versionSummary,
      size: formatRssSeedSize(item.sizeBytes),
      date: item.publishedAt || item.lastSeenAt,
      full_text: item.description,
      season: item.seasonNumber == null ? undefined : String(item.seasonNumber),
      episodes,
      scope,
      matchMethod: item.matchMethod,
      matchConfidence: item.matchConfidence,
      identityStatus: item.identityStatus,
      torraHandoffReady: item.hasDownload
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
    scopeCounts: countRssResourceScopes(items.map((item) => item.scope ?? 'scope_pending')),
    errors: [],
    cache_hits: [],
    sourceStatuses: [{
      key: 'local-rss',
      label: '本地 RSS 种子箱',
      status: 'available',
      count: items.length,
      message: items.length > 0 ? `已找到 ${items.length} 条候选` : '已搜索，未找到匹配种子'
    }]
  };
}

function discoverSourceLabel(source: DiscoverSource) {
  return sources.find((item) => item.id === source)?.label ?? source;
}

function unavailableSourceStatus(key: string, label: string, message: string): DiscoverSourceStatus {
  return { key, label, status: 'unavailable', count: 0, message };
}

function DiscoverSourceStatusList({ statuses }: { statuses: DiscoverSourceStatus[] }) {
  if (statuses.length === 0) return null;
  return (
    <ul className="discover-source-statuses" aria-label="本次搜索来源状态">
      {statuses.map((source) => (
        <li className={`is-${source.status}`} key={source.key}>
          <span aria-hidden="true" />
          <strong>{source.label}</strong>
          <small>{source.message || (source.count > 0 ? `找到 ${source.count} 条` : '已搜索，未找到结果')}</small>
        </li>
      ))}
    </ul>
  );
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

function followScopeLabel(item: SubscriptionItem) {
  if (item.mediaType !== 'tv') return '整部电影';
  return item.seasonName || (item.seasonNumber != null ? `第 ${item.seasonNumber} 季` : '按剧集持续追更');
}

const followProviderLabels: Record<ManualFollowProvider, string> = {
  torra: 'Torra',
  moviepilot: 'MoviePilot',
  symedia: 'Symedia',
  resource_rule: '资源处理规则',
  none: ''
};

function subscriptionUserStatus(item: SubscriptionItem) {
  if (item.healthState === 'action_required' || item.fulfillmentState === 'blocked') {
    return item.blockingReason || item.reasonText || '当前追更需要处理';
  }
  if (item.reconciliationState === 'conflict') {
    return '';
  }
  if (item.reconciliationState === 'remote_missing') {
    return 'Torra 中已找不到这条追更，请检查是否被删除或替换';
  }
  if (item.readOnly || item.reconciliationState === 'only_torra') {
    return item.fulfillmentState === 'completed'
      ? '追更已在 Torra 完成，Fluxa 正在读取下载与入库结果'
      : '追更已在 Torra 生效，Fluxa 正在读取下载与入库进度';
  }
  return item.blockingReason || '';
}

function subscriptionUserStatusTone(item: SubscriptionItem) {
  return item.healthState === 'action_required'
    || item.fulfillmentState === 'blocked'
    || item.reconciliationState === 'remote_missing'
    ? 'is-danger'
    : 'is-neutral';
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
    if (action.type === 'rewash-download') return 'Torra 已接收候选下载请求';
    return (action.result?.selectedCount ?? 0) > 0
      ? `分析已完成，发现 ${action.result?.selectedCount} 个升级候选`
      : '分析已完成，没有升级候选';
  }
  if (action.status === 'failed') return action.error?.message || '动作失败';
  if (action.status === 'cancelled') return '动作已取消';
  return action.type === 'rewash-download' ? '候选下载执行中' : 'Torra 分析执行中';
}

function matchingResourceUnits(
  item: DiscoverResourceItem,
  mediaType: DiscoverResult['mediaType'],
  watch: QualityWatchResponse | null
) {
  if (!watch) return [];
  const activeStates = new Set(['observing_upgrade', 'search_due', 'search_running']);
  const season = item.season == null ? null : Number(item.season);
  const episodes = item.episodes ?? [];
  return watch.units.filter((unit) => {
    if (!activeStates.has(unit.state) || !unit.baselineReadyAt) return false;
    if (mediaType === 'movie') return unit.seasonNumber == null;
    if (!Number.isInteger(season) || episodes.length === 0) return false;
    return unit.seasonNumber === season
      && unit.episodeNumber != null
      && episodes.includes(unit.episodeNumber);
  });
}

function readFollowingFilters() {
  const query = new URLSearchParams(window.location.search);
  const mediaType = query.get('mediaType');
  const status = query.get('status');
  const updated = query.get('updated');
  return {
    tab: mediaType === 'movie' || mediaType === 'tv' || mediaType === 'blocked' ? mediaType : 'tv' as SubscriptionTab,
    keyword: query.get('q') || '',
    year: query.get('year') || 'all',
    status: status === 'pending' || status === 'done' ? status : 'all' as SubscriptionStatusFilter,
    updated: updated === 'today' || updated === '3' || updated === '7' ? updated : 'all' as SubscriptionUpdateFilter,
    missingEpisodesOnly: query.get('missingEpisodes') === '1'
  };
}

function readFollowingDetailTarget(location: Location = window.location) {
  const query = new URLSearchParams(location.search);
  const seasonValue = query.get('seasonNumber');
  const parsedSeason = Number(seasonValue);
  return {
    subscriptionId: query.get('subscriptionId')?.trim() || '',
    tmdbId: query.get('tmdbId')?.trim() || '',
    seasonNumber: seasonValue !== null && Number.isInteger(parsedSeason) && parsedSeason >= 0 ? parsedSeason : null
  };
}

const followingDetailHistoryKind = 'following:detail';
const followingListHistoryKind = 'following:list';
const subscriptionPageSize = 24;
const subscriptionFilterPageSize = 100;
const subscriptionFilterMaxRows = 2000;

function writeFollowingDetailTarget(item: SubscriptionItem | null, seasonNumber: number | null, mode: UrlHistoryMode) {
  let entryKind: string | undefined = followingListHistoryKind;
  if (item) {
    entryKind = mode === 'push' || currentHistoryEntryIs(followingDetailHistoryKind)
      ? followingDetailHistoryKind
      : undefined;
  }
  writeUrlQuery({
    subscriptionId: item?.id || null,
    tmdbId: item?.tmdbId || null,
    title: item?.title || null,
    seasonNumber: item && seasonNumber != null ? seasonNumber : null
  }, mode, entryKind);
}

export function DiscoverPage({ navigationTarget = null, onNavigate, view = 'discover' }: DiscoverPageProps) {
  const subscriptionsOnly = view === 'subscriptions';
  const initialFollowingFilters = subscriptionsOnly ? readFollowingFilters() : null;
  const [initialDiscoverState] = useState(() => subscriptionsOnly ? null : readDiscoverUrlState());
  const [filters, setFilters] = useState<DiscoverBrowseParams>(initialDiscoverState?.filters ?? defaultFilters);
  const [query, setQuery] = useState(initialDiscoverState?.query ?? '');
  const [activeSearch, setActiveSearch] = useState(initialDiscoverState?.query ?? '');
  const [searchPage, setSearchPage] = useState(initialDiscoverState?.page ?? 1);
  const [results, setResults] = useState<DiscoverResult[]>([]);
  const [configured, setConfigured] = useState(true);
  const [discoverError, setDiscoverError] = useState('');
  const [discoverSourceStatuses, setDiscoverSourceStatuses] = useState<DiscoverSourceStatus[]>([]);
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
  const [subscriptionTab, setSubscriptionTab] = useState<SubscriptionTab>(initialFollowingFilters?.tab ?? 'tv');
  const [subscriptionKeyword, setSubscriptionKeyword] = useState(initialFollowingFilters?.keyword ?? '');
  const deferredSubscriptionKeyword = useDeferredValue(subscriptionKeyword);
  const [subscriptionYear, setSubscriptionYear] = useState(initialFollowingFilters?.year ?? 'all');
  const [subscriptionStatus, setSubscriptionStatus] = useState<SubscriptionStatusFilter>(initialFollowingFilters?.status ?? 'all');
  const [subscriptionUpdate, setSubscriptionUpdate] = useState<SubscriptionUpdateFilter>(initialFollowingFilters?.updated ?? 'all');
  const [missingEpisodesOnly, setMissingEpisodesOnly] = useState(initialFollowingFilters?.missingEpisodesOnly ?? false);
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
  const [resourceActionItem, setResourceActionItem] = useState<DiscoverResourceItem | null>(null);
  const [resourceUnitId, setResourceUnitId] = useState('');
  const [resourceMatch, setResourceMatch] = useState<RssMatch | null>(null);
  const [resourceActionBusy, setResourceActionBusy] = useState('');
  const [resourceActionMessage, setResourceActionMessage] = useState('');
  const [resourceQualityWatch, setResourceQualityWatch] = useState<QualityWatchResponse | null>(null);
  const [torraPushPreview, setTorraPushPreview] = useState<TorraPushPreviewResponse | null>(null);
  const [torraPushMessage, setTorraPushMessage] = useState('');
  const [torraPushBusy, setTorraPushBusy] = useState('');
  const [qualityWatch, setQualityWatch] = useState<QualityWatchResponse | null>(null);
  const [qualityWatchBusy, setQualityWatchBusy] = useState('');
  const [qualityWatchMessage, setQualityWatchMessage] = useState('');
  const [selectedUnitId, setSelectedUnitId] = useState('');
  const [qualityAutomationAction, setQualityAutomationAction] = useState<AutomationAction | null>(null);
  const [resourceAutomationAction, setResourceAutomationAction] = useState<AutomationAction | null>(null);
  const [moviePilotPreview, setMoviePilotPreview] = useState<MoviePilotPreview | null>(null);
  const [moviePilotBusy, setMoviePilotBusy] = useState('');
  const [moviePilotMessage, setMoviePilotMessage] = useState('');
  const [torraSyncStatus, setTorraSyncStatus] = useState<TorraSubscriptionSyncStatus | null>(null);
  const [torraSyncPreview, setTorraSyncPreview] = useState<TorraSubscriptionSyncPreview | null>(null);
  const [torraSyncBusy, setTorraSyncBusy] = useState('');
  const [torraSyncMessage, setTorraSyncMessage] = useState('');
  const [confirmation, setConfirmation] = useState<DiscoverConfirmation | null>(null);
  const qualityAutomationRequestRef = useRef<AbortController | null>(null);
  const resourceAutomationRequestRef = useRef<AbortController | null>(null);
  const resourceQualityWatchRequestRef = useRef<AbortController | null>(null);
  const resourceTargetSubscriptionIdRef = useRef('');
  const detailRequestRef = useRef<AbortController | null>(null);
  const resourceRequestRef = useRef<AbortController | null>(null);
  const subsRequestRef = useRef<AbortController | null>(null);
  const resourcePanelRef = useRef<HTMLElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const posterBackfillAttemptedRef = useRef(new Set<string>());
  const resourceAutomationInFlight = Boolean(
    resourceAutomationAction && !terminalAutomationStates.has(resourceAutomationAction.status)
  );
  const resourceWorkflowLocked = Boolean(resourceActionBusy) || resourceAutomationInFlight;
  const resourcePanelCloseLocked = Boolean(resourceActionBusy);

  const requestPosterBackfills = useCallback((ids: string[] = []) => {
    const pending = ids.filter((id) => id && !posterBackfillAttemptedRef.current.has(id));
    if (pending.length === 0) return;
    pending.forEach((id) => posterBackfillAttemptedRef.current.add(id));
    void backfillSubscriptionVisuals(pending)
      .then((result) => {
        result.errors.forEach((id) => posterBackfillAttemptedRef.current.delete(id));
        if (result.items.length === 0) return;
        const visuals = new Map(result.items.map((item) => [item.id, item]));
        const applyVisuals = (items: SubscriptionItem[]) => items.map((item) => {
          const visual = item.id ? visuals.get(item.id) : undefined;
          return visual ? { ...item, posterUrl: visual.posterUrl, backdropUrl: visual.backdropUrl } : item;
        });
        setSubs(applyVisuals);
        setWorkbench((current) => current ? { ...current, items: applyVisuals(current.items) } : current);
      })
      .catch(() => pending.forEach((id) => posterBackfillAttemptedRef.current.delete(id)));
  }, []);

  const loadSubs = useCallback(async () => {
    subsRequestRef.current?.abort();
    const controller = new AbortController();
    subsRequestRef.current = controller;
    setSubsLoading(true);
    setSubsMoreLoading(false);
    setSubsError('');
    try {
      if (!subscriptionsOnly) {
        const payload = await getSubscriptionItems(true, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setSubs(payload.subscriptions?.items ?? []);
        setBlockedTitles(payload.blockedTitles ?? []);
        return;
      }

      const completeFilterResult = subscriptionTab !== 'blocked' && (
        subscriptionYear !== 'all'
        || subscriptionStatus !== 'all'
        || subscriptionUpdate !== 'all'
        || missingEpisodesOnly
      );
      const requestInput = {
        limit: completeFilterResult ? subscriptionFilterPageSize : subscriptionPageSize,
        offset: 0,
        mediaType: subscriptionTab === 'blocked' ? undefined : subscriptionTab,
        query: deferredSubscriptionKeyword
      } as const;
      const first = await getSubscriptionWorkbench(requestInput, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (completeFilterResult && first.page.total > subscriptionFilterMaxRows) {
        throw new Error(`筛选范围超过 ${subscriptionFilterMaxRows} 条，请先输入标题缩小范围`);
      }
      let latest = first;
      const allItems = [...first.items];
      const seenItemKeys = new Set(first.items.map((item) => (
        item.id || `${item.mediaType}:${item.tmdbId}:${item.seasonNumber ?? 0}:${item.title}`
      )));
      const posterBackfillIds = new Set(first.posterBackfillIds ?? []);

      while (completeFilterResult && latest.page.nextOffset != null) {
        if (allItems.length >= subscriptionFilterMaxRows) {
          throw new Error(`筛选范围超过 ${subscriptionFilterMaxRows} 条，请先输入标题缩小范围`);
        }
        latest = await getSubscriptionWorkbench({
          ...requestInput,
          offset: latest.page.nextOffset
        }, { signal: controller.signal });
        if (controller.signal.aborted) return;
        latest.items.forEach((item) => {
          const key = item.id || `${item.mediaType}:${item.tmdbId}:${item.seasonNumber ?? 0}:${item.title}`;
          if (seenItemKeys.has(key)) return;
          seenItemKeys.add(key);
          allItems.push(item);
        });
        latest.posterBackfillIds?.forEach((id) => posterBackfillIds.add(id));
      }

      const payload = completeFilterResult ? {
        ...first,
        items: allItems,
        posterBackfillIds: [...posterBackfillIds],
        page: {
          ...first.page,
          limit: allItems.length || requestInput.limit,
          nextOffset: null,
          hasMore: false
        }
      } : first;
      setWorkbench(payload);
      setSubs(payload.items);
      setBlockedTitles(payload.blockedTitles ?? []);
      setTorraSyncStatus(payload.torraSync);
      requestPosterBackfills(payload.posterBackfillIds);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setSubsError(reason instanceof Error ? reason.message : '追更工作台当前不可用');
      }
    } finally {
      if (subsRequestRef.current === controller) {
        subsRequestRef.current = null;
        setSubsLoading(false);
      }
    }
  }, [
    deferredSubscriptionKeyword,
    missingEpisodesOnly,
    requestPosterBackfills,
    subscriptionStatus,
    subscriptionTab,
    subscriptionUpdate,
    subscriptionYear,
    subscriptionsOnly
  ]);

  const loadMoreSubs = useCallback(async () => {
    const page = workbench?.page;
    const nextOffset = page?.nextOffset;
    if (!subscriptionsOnly || subscriptionTab === 'blocked' || !page || nextOffset == null || subsMoreLoading) return;
    subsRequestRef.current?.abort();
    const controller = new AbortController();
    subsRequestRef.current = controller;
    setSubsMoreLoading(true);
    try {
      const payload = await getSubscriptionWorkbench({
        limit: page.limit,
        offset: nextOffset,
        mediaType: subscriptionTab,
        query: deferredSubscriptionKeyword
      }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setWorkbench(payload);
      requestPosterBackfills(payload.posterBackfillIds);
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
    } catch (reason) {
      if (!controller.signal.aborted) {
        setSubsError(reason instanceof Error ? reason.message : '更多追更读取失败');
      }
    } finally {
      if (subsRequestRef.current === controller) subsRequestRef.current = null;
      if (!controller.signal.aborted) setSubsMoreLoading(false);
    }
  }, [deferredSubscriptionKeyword, requestPosterBackfills, subsMoreLoading, subscriptionTab, subscriptionsOnly, workbench]);

  useEffect(() => {
    loadSubs();
  }, [loadSubs]);

  useEffect(() => {
    if (!subscriptionsOnly || !navigationTarget) return;
    if (navigationTarget.mediaType) setSubscriptionTab(navigationTarget.mediaType);
    if (navigationTarget.title) setSubscriptionKeyword(navigationTarget.title);
  }, [navigationTarget, subscriptionsOnly]);

  useEffect(() => {
    if (subscriptionsOnly) return undefined;
    const restore = () => {
      const next = readDiscoverUrlState();
      setFilters(next.filters);
      setQuery(next.query);
      setActiveSearch(next.query);
      setSearchPage(next.page);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [subscriptionsOnly]);

  useEffect(() => {
    if (!subscriptionsOnly) return undefined;
    const restore = () => {
      const next = readFollowingFilters();
      setSubscriptionTab(next.tab);
      setSubscriptionKeyword(next.keyword);
      setSubscriptionYear(next.year);
      setSubscriptionStatus(next.status);
      setSubscriptionUpdate(next.updated);
      setMissingEpisodesOnly(next.missingEpisodesOnly);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [subscriptionsOnly]);

  useEffect(() => {
    if (!subscriptionsOnly) return;
    const query = new URLSearchParams(window.location.search);
    if (subscriptionTab === 'blocked') query.set('mediaType', 'blocked');
    else query.set('mediaType', subscriptionTab);
    if (subscriptionKeyword.trim()) query.set('q', subscriptionKeyword.trim()); else query.delete('q');
    if (subscriptionYear !== 'all') query.set('year', subscriptionYear); else query.delete('year');
    if (subscriptionStatus !== 'all') query.set('status', subscriptionStatus); else query.delete('status');
    if (subscriptionUpdate !== 'all') query.set('updated', subscriptionUpdate); else query.delete('updated');
    if (missingEpisodesOnly) query.set('missingEpisodes', '1'); else query.delete('missingEpisodes');
    const search = query.toString();
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${search ? `?${search}` : ''}`);
  }, [missingEpisodesOnly, subscriptionKeyword, subscriptionStatus, subscriptionTab, subscriptionUpdate, subscriptionYear, subscriptionsOnly]);

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
    qualityAutomationRequestRef.current?.abort();
    resourceAutomationRequestRef.current?.abort();
    resourceQualityWatchRequestRef.current?.abort();
    detailRequestRef.current?.abort();
    subsRequestRef.current?.abort();
  }, []);

  const applyPayload = useCallback((payload: Awaited<ReturnType<typeof browseDiscover>>) => {
    setConfigured(payload.configured);
    setResults(payload.results);
    setDiscoverSourceStatuses(payload.sourceStatuses ?? []);
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
    setDiscoverError('');
    setDiscoverSourceStatuses([]);

    browseDiscover(filters)
      .then((payload) => {
        if (!cancelled) applyPayload(payload);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setConfigured(true);
          setResults([]);
          const message = reason instanceof Error ? reason.message : '内容来源暂不可用';
          setDiscoverError(message);
          setDiscoverSourceStatuses([
            unavailableSourceStatus(filters.source, discoverSourceLabel(filters.source), message)
          ]);
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
    setDiscoverError('');
    setDiscoverSourceStatuses([]);

    searchDiscover(activeSearch, searchPage)
      .then((payload) => {
        if (!cancelled) applyPayload(payload);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setConfigured(true);
          setResults([]);
          const message = reason instanceof Error ? reason.message : '内容搜索暂不可用';
          setDiscoverError(message);
          setDiscoverSourceStatuses([unavailableSourceStatus('tmdb', 'TMDB', message)]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeSearch, applyPayload, searchPage, subscriptionsOnly]);

  const subscribedKeys = useMemo(() => new Set(subs.map((item) => `${item.mediaType}:${item.tmdbId}`)), [subs]);
  const subscriptionYears = useMemo(() => {
    const latestYear = new Date().getFullYear() + 1;
    const years = new Set(
      Array.from({ length: latestYear - 1899 }, (_, index) => String(latestYear - index))
    );
    subs.forEach((item) => {
      if (item.year) years.add(item.year);
    });
    if (/^\d{4}$/.test(subscriptionYear)) years.add(subscriptionYear);
    return [...years].sort().reverse();
  }, [subs, subscriptionYear]);
  const visibleSubscriptions = useMemo(() => {
    if (subscriptionTab === 'blocked') return [];
    const keyword = subscriptionKeyword.trim().toLowerCase();
    return subs.filter((item) => {
      if (item.mediaType !== subscriptionTab) return false;
      if (subscriptionYear !== 'all' && item.year !== subscriptionYear) return false;
      if (subscriptionStatus !== 'all' && resolvedSubscriptionStatus(item) !== subscriptionStatus) return false;
      if (missingEpisodesOnly && (item.missingEpisodes?.length ?? 0) === 0) return false;
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
  }, [missingEpisodesOnly, subs, subscriptionKeyword, subscriptionStatus, subscriptionTab, subscriptionUpdate, subscriptionYear]);
  const localWriteEnabled = subscriptionsOnly
    ? Boolean(workbench?.capabilities.find((capability) => capability.key === 'local_write')?.enabled)
    : true;
  const workbenchStats = workbench?.stats ?? {
    total: subs.length,
    movie: subs.filter((item) => item.mediaType === 'movie').length,
    tv: subs.filter((item) => item.mediaType === 'tv').length,
    pending: subs.filter((item) => !item.inLibrary).length,
    following: subs.filter((item) => item.fulfillmentState === 'following').length,
    completed: subs.filter((item) => item.fulfillmentState === 'completed' || item.chainState === 'completed').length,
    actionRequired: subs.filter((item) => item.healthState === 'action_required' || item.fulfillmentState === 'blocked').length,
    inLibrary: subs.filter((item) => item.inLibrary).length
  };
  const subscriptionCountsUnavailable = subscriptionsOnly && !workbench;
  const reconciliationSummary = workbench?.reconciliation?.summary;
  const torraPushEnabled = Boolean(subscriptionCapabilities?.torraPush.enabled);
  const schedulerRunning = Boolean(subscriptionCapabilities?.scheduler.running);
  const manualFollow = subscriptionCapabilities?.manualFollow;
  // manualFollow 缺失（后端未就绪）时回退到现有 capabilities 推断，不报错
  const manualFollowState = manualFollow?.state
    ?? (!subscriptionCapabilities
      ? null
      : !subscriptionCapabilities.localWrite.enabled
        ? 'write_disabled'
        : torraPushEnabled
          ? 'queued_ready'
          : 'saved_only');
  const manualFollowProviderLabel = manualFollow && manualFollow.provider !== 'none'
    ? followProviderLabels[manualFollow.provider]
    : '';
  const followButtonLabel = manualFollowState === 'write_disabled'
    ? '追更写入已关闭'
    : manualFollowState === 'saved_only'
      ? '加入追更（仅保存）'
      : '加入追更';
  const followWriteDisabled = manualFollowState === 'write_disabled';
  const followConfirmationText = manualFollow
    ? manualFollow.state === 'write_disabled'
      ? `追更写入已关闭，当前无法保存新的追更。${manualFollow.blockers.join('；')}`
      : manualFollow.state === 'saved_only'
        ? '只会保存到 Fluxa 追更台账，当前没有可运行的后续获取能力。'
        : `保存后会立即交给${manualFollowProviderLabel || '后续能力'}继续处理。`
    : !subscriptionCapabilities
      ? '保存追更意图；实际生效结果将在保存后确认。'
      : !torraPushEnabled
        ? '保存追更意图，当前不会自动获取；可稍后预览并手动同步到 Torra。'
        : !schedulerRunning
          ? '保存追更意图；Torra 推送已开启，但定时任务未运行，需要手动同步。'
          : '保存后进入自动追更，系统会按 PT 优先策略继续处理。';
  const followFallbackSuccessText = !subscriptionCapabilities || !torraPushEnabled
    ? '已保存追更，当前不会自动获取'
    : !schedulerRunning
      ? '已保存追更，等待手动同步到 Torra'
      : '已保存追更，已进入自动追更';
  const followPolicyHint = !subscriptionCapabilities
    ? '正在确认追更能力'
    : manualFollow
      ? manualFollow.state === 'write_disabled'
        ? '追更写入已关闭'
        : manualFollow.state === 'saved_only'
          ? '加入后仅保存，暂无后续获取能力'
          : `加入后交给${manualFollowProviderLabel || '后续能力'}处理`
      : !torraPushEnabled
        ? '保存意图，暂不自动获取'
        : !schedulerRunning
          ? '保存后等待手动同步'
          : '保存后进入自动追更';
  const recentFollows = useMemo(() => {
    if (subscriptionsOnly) return [];
    const updatedAtMs = (item: SubscriptionItem) => {
      const parsed = new Date((item.updatedAt || '').replace(' ', 'T')).getTime();
      return Number.isFinite(parsed) ? parsed : 0;
    };
    return [...subs]
      .sort((left, right) => updatedAtMs(right) - updatedAtMs(left))
      .slice(0, 3);
  }, [subs, subscriptionsOnly]);

  const changeSource = (source: DiscoverSource) => {
    const nextFilters = {
      ...filters,
      source,
      type: forcedTypeForSource(source, filters.type),
      page: 1
    };
    setQuery('');
    setActiveSearch('');
    setSearchPage(1);
    setFilters(nextFilters);
    writeDiscoverUrlState({ filters: nextFilters, query: '', page: 1 }, 'push');
  };

  const updateFilter = (key: FilterKey, value: string) => {
    const nextFilters = { ...filters, [key]: value, page: 1 };
    setQuery('');
    setActiveSearch('');
    setSearchPage(1);
    setFilters(nextFilters);
    writeDiscoverUrlState({ filters: nextFilters, query: '', page: 1 }, 'push');
  };

  const resetFilters = () => {
    setQuery('');
    setActiveSearch('');
    setSearchPage(1);
    setFilters(defaultFilters);
    writeDiscoverUrlState({ filters: defaultFilters, query: '', page: 1 }, 'push');
  };

  const runSearch = (event: FormEvent) => {
    event.preventDefault();
    const keyword = query.trim();
    if (!keyword) {
      setActiveSearch('');
      setSearchPage(1);
      const nextFilters = { ...filters, page: 1 };
      setFilters(nextFilters);
      writeDiscoverUrlState({ filters: nextFilters, query: '', page: 1 }, 'push');
      return;
    }
    setActiveSearch(keyword);
    setSearchPage(1);
    writeDiscoverUrlState({ filters, query: keyword, page: 1 }, 'push');
  };

  const goPage = (nextPage: number) => {
    const page = Math.max(1, Math.min(pageInfo.totalPages, nextPage));
    if (activeSearch) {
      setSearchPage(page);
      writeDiscoverUrlState({ filters, query: activeSearch, page }, 'push');
      return;
    }
    const nextFilters = { ...filters, page };
    setFilters(nextFilters);
    writeDiscoverUrlState({ filters: nextFilters, query: '', page }, 'push');
  };

  const subscribe = (result: DiscoverResult) => {
    const tmdbId = tmdbIdForResult(result);
    if (!tmdbId || followWriteDisabled) return;
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
      signal: '加入追更',
      title: `把《${payload.title}》加入追更？`,
      description: followConfirmationText,
      confirmLabel: followButtonLabel,
      onConfirm: () => {
        setSubscriptionAction(`save:${payload.mediaType}:${payload.tmdbId}`);
        saveSubscription(payload)
          .then((saved) => {
            // activation 缺失（后端未就绪）时回退到现有推断文案
            setSweepMessage(saved.activation?.message
              ? `${payload.title}：${saved.activation.message}`
              : `${followFallbackSuccessText}：${payload.title}`);
            loadSubs();
          })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '保存追更失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const runSweep = () => {
    setConfirmation({
      signal: '自动追更',
      title: '更新自动追更来源？',
      description: '这会重新读取已启用的榜单来源，并增量合并到本地台账；不会搜索当前剧集，也不会删除手动追更或 Torra 镜像。',
      confirmLabel: '开始更新',
      onConfirm: () => {
        setSubscriptionAction('run');
        runSubscriptionSweep()
          .then(() => {
            setSweepMessage('自动追更来源已更新，列表正在重新读取。');
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
        importTorraSubscriptions(createIdempotencyKey())
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
      signal: '追更管理',
      title: `删除《${item.title}》？`,
      description: '删除后不会加入屏蔽列表；如果之后来源再次命中，仍可能重新出现。',
      confirmLabel: '删除追更',
      destructive: true,
      onConfirm: () => {
        setSubscriptionAction(`delete:${item.id}`);
        deleteSubscription(item.id!)
          .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已删除追更：${item.title}`); })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '删除失败'))
          .finally(() => setSubscriptionAction(''));
      }
    });
  };

  const blockItem = (item: SubscriptionItem) => {
    if (!item.id) return;
    setConfirmation({
      signal: '追更管理',
      title: `删除并屏蔽《${item.title}》？`,
      description: '自动追更后续会跳过这个标题，直到你在屏蔽列表中取消屏蔽。',
      confirmLabel: '删除并屏蔽',
      destructive: true,
      onConfirm: () => {
        setSubscriptionAction(`block:${item.id}`);
        blockSubscription({ id: item.id!, title: item.title })
          .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已屏蔽追更：${item.title}`); })
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
      signal: '追更管理',
      title: `改为追更《${item.title}》第 ${seasonNumber} 季？`,
      description: '当前追更季会被替换，下载和入库规则将按新季继续处理。',
      confirmLabel: '切换追更季',
      onConfirm: () => {
        setSubscriptionAction(`season:${item.id}`);
        setSubscriptionSeason(item.id!, seasonNumber, seasonName)
          .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已更新追更季：${item.title} · S${seasonNumber}`); })
          .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '更新追更季失败'))
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
    pushSubscriptionToTorra(item.id, createIdempotencyKey())
      .then((result) => {
        setTorraPushMessage(result.message);
        setSweepMessage(`${item.title}：${result.message}`);
        loadSubs();
      })
      .catch((error: unknown) => setTorraPushMessage(error instanceof Error ? error.message : 'Torra 推送失败'))
      .finally(() => setTorraPushBusy(''));
  };

  const pollAutomationAction = async (
    actionId: string,
    requestRef: { current: AbortController | null },
    setAction: (action: AutomationAction) => void,
    onPollingIssue: (message: string) => void,
    onUpdate?: (action: AutomationAction) => void
  ) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const action = await getAutomationAction(actionId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAction(action);
        onUpdate?.(action);
        if (terminalAutomationStates.has(action.status)) return;
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 1500);
          controller.signal.addEventListener('abort', () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
      }
      if (!controller.signal.aborted) {
        const message = '动作仍在后台执行，可稍后重新打开详情查看结果。';
        onPollingIssue(message);
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        const message = reason instanceof Error ? reason.message : '自动化动作状态读取失败';
        onPollingIssue(message);
      }
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
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
    qualityAutomationRequestRef.current?.abort();
    const controller = new AbortController();
    qualityAutomationRequestRef.current = controller;
    setQualityWatchBusy(`analysis:${item.id}`);
    setQualityWatchMessage('正在提交 Torra 质量分析…');
    setQualityAutomationAction(null);
    startTorraRewashAnalysis(item.id, {
      idempotencyKey: createIdempotencyKey(),
      ...(selectedUnitId ? { unitId: selectedUnitId } : {})
    }, { signal: controller.signal })
      .then((action) => {
        if (controller.signal.aborted) return;
        setQualityAutomationAction(action);
        setQualityWatchMessage(automationStatusLabel(action));
        if (qualityAutomationRequestRef.current === controller) qualityAutomationRequestRef.current = null;
        void pollAutomationAction(
          action.id,
          qualityAutomationRequestRef,
          setQualityAutomationAction,
          setQualityWatchMessage
        );
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setQualityWatchMessage(reason instanceof Error ? reason.message : 'Torra 分析提交失败');
        }
      })
      .finally(() => {
        if (qualityAutomationRequestRef.current === controller) qualityAutomationRequestRef.current = null;
        if (!controller.signal.aborted) setQualityWatchBusy('');
      });
  };

  const startDownload = (item: SubscriptionItem) => {
    if (!item.id || !qualityAutomationAction || qualityAutomationAction.status !== 'succeeded' || qualityAutomationAction.type !== 'rewash-analysis') return;
    const itemId = item.id;
    const analysis = qualityAutomationAction;
    setConfirmation({
      signal: '质量升级',
      title: `下载《${item.title}》的升级候选？`,
      description: '这会把人工分析选中的候选交给 Torra 下载，原有入库版本不会立即删除。',
      confirmLabel: '确认下载',
      onConfirm: () => {
        qualityAutomationRequestRef.current?.abort();
        const controller = new AbortController();
        qualityAutomationRequestRef.current = controller;
        setQualityWatchBusy(`download:${itemId}`);
        setQualityWatchMessage('正在提交 Torra 候选下载…');
        startTorraRewashDownload(itemId, {
          confirm: true,
          idempotencyKey: createIdempotencyKey(),
          analysisActionId: analysis.id,
          ...((analysis.unitId || selectedUnitId) ? { unitId: analysis.unitId || selectedUnitId } : {})
        }, { signal: controller.signal })
          .then((action) => {
            if (controller.signal.aborted) return;
            setQualityAutomationAction(action);
            setQualityWatchMessage(automationStatusLabel(action));
            if (qualityAutomationRequestRef.current === controller) qualityAutomationRequestRef.current = null;
            void pollAutomationAction(
              action.id,
              qualityAutomationRequestRef,
              setQualityAutomationAction,
              setQualityWatchMessage
            );
          })
          .catch((reason: unknown) => {
            if (!controller.signal.aborted) {
              setQualityWatchMessage(reason instanceof Error ? reason.message : 'Torra 候选下载提交失败');
            }
          })
          .finally(() => {
            if (qualityAutomationRequestRef.current === controller) qualityAutomationRequestRef.current = null;
            if (!controller.signal.aborted) setQualityWatchBusy('');
          });
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
        pushToMoviePilot(itemId, createIdempotencyKey())
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

  const closeDetail = (historyMode: UrlHistoryMode | false = 'push') => {
    const urlTarget = readFollowingDetailTarget();
    const hadDetailTarget = Boolean(detailId || urlTarget.subscriptionId || urlTarget.tmdbId);
    qualityAutomationRequestRef.current?.abort();
    qualityAutomationRequestRef.current = null;
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
    setQualityAutomationAction(null);
    setMoviePilotPreview(null);
    setMoviePilotBusy('');
    setMoviePilotMessage('');
    if (!historyMode || !hadDetailTarget) return;
    if (historyMode === 'push' && currentHistoryEntryIs(followingDetailHistoryKind)) {
      window.history.back();
    } else {
      writeFollowingDetailTarget(null, null, 'replace');
    }
  };

  const beginResourceAction = async (item: DiscoverResourceItem) => {
    const subscriptionId = resourceTarget?.source === 'subscription' ? resourceTarget.sourceId : '';
    if (!item.rssItemId || !subscriptionId || !resourceTarget || resourceWorkflowLocked) return;
    resourceQualityWatchRequestRef.current?.abort();
    resourceQualityWatchRequestRef.current = null;
    resourceAutomationRequestRef.current?.abort();
    const controller = new AbortController();
    resourceAutomationRequestRef.current = controller;
    setResourceActionItem(item);
    setResourceMatch(null);
    setResourceAutomationAction(null);
    setResourceUnitId('');
    setResourceActionBusy('prepare');
    setResourceActionMessage('正在核对追更范围与观察单元…');
    try {
      const watch = resourceQualityWatch?.subscriptionId === subscriptionId
        ? resourceQualityWatch
        : await getSubscriptionQualityWatch(subscriptionId, { signal: controller.signal });
      if (
        controller.signal.aborted
        || resourceTargetSubscriptionIdRef.current !== subscriptionId
        || watch.subscriptionId !== subscriptionId
      ) return;
      setResourceQualityWatch(watch);
      const units = matchingResourceUnits(item, resourceTarget.mediaType, watch);
      setResourceUnitId(units[0]?.id || '');
      setResourceActionMessage(units.length > 0
        ? units.length === 1
          ? `已定位到 ${unitLabel(units[0])}，可以检查可用版本`
          : `这个种子覆盖 ${units.length} 个追更目标，请选择一个季集`
        : '种子与当前有效观察单元无法唯一对应，请确认季集或等待首个版本入库');
    } catch (reason) {
      if (!controller.signal.aborted) {
        setResourceActionMessage(reason instanceof Error ? reason.message : '质量观察状态读取失败');
      }
    } finally {
      if (resourceAutomationRequestRef.current === controller) resourceAutomationRequestRef.current = null;
      if (!controller.signal.aborted) setResourceActionBusy('');
    }
  };

  const analyzeResourceMatch = async (restart = false) => {
    const subscriptionId = resourceTarget?.source === 'subscription' ? resourceTarget.sourceId : '';
    if (!resourceActionItem?.rssItemId || !subscriptionId || !resourceUnitId) return;
    resourceAutomationRequestRef.current?.abort();
    const controller = new AbortController();
    resourceAutomationRequestRef.current = controller;
    setResourceActionBusy('analysis');
    setResourceActionMessage(restart ? '正在重新建立安全匹配…' : '正在建立安全匹配…');
    setResourceAutomationAction(null);
    try {
      const match = await createRssMatch({
        rssItemId: resourceActionItem.rssItemId,
        subscriptionId,
        unitId: resourceUnitId
      }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setResourceMatch(match);
      let action: AutomationAction;
      if (match.triggerActionId && !restart) {
        setResourceActionMessage('发现已有处理记录，正在恢复当前进度…');
        action = await getAutomationAction(match.triggerActionId, { signal: controller.signal });
      } else {
        setResourceActionMessage(restart ? '正在重新提交 Torra 质量分析…' : '匹配已建立，正在提交 Torra 质量分析…');
        action = await startRssMatchAnalysis(match.id, createIdempotencyKey(), { signal: controller.signal });
      }
      if (controller.signal.aborted) return;
      setResourceMatch({ ...match, triggerActionId: action.id });
      setResourceAutomationAction(action);
      setResourceActionMessage(automationStatusLabel(action));
      if (!terminalAutomationStates.has(action.status)) {
        if (resourceAutomationRequestRef.current === controller) resourceAutomationRequestRef.current = null;
        void pollAutomationAction(
          action.id,
          resourceAutomationRequestRef,
          setResourceAutomationAction,
          setResourceActionMessage,
          (latest) => setResourceActionMessage(automationStatusLabel(latest))
        );
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        setResourceActionMessage(reason instanceof Error ? reason.message : 'RSS 候选分析失败');
      }
    } finally {
      if (resourceAutomationRequestRef.current === controller) resourceAutomationRequestRef.current = null;
      if (!controller.signal.aborted) setResourceActionBusy('');
    }
  };

  const confirmResourceDownload = () => {
    if (
      !resourceMatch
      || !resourceAutomationAction
      || resourceAutomationAction.type !== 'rewash-analysis'
      || resourceAutomationAction.status !== 'succeeded'
      || (resourceAutomationAction.result?.selectedCount ?? 0) < 1
    ) return;
    const match = resourceMatch;
    const analysis = resourceAutomationAction;
    setConfirmation({
      signal: 'RSS 追更',
      title: `把《${resourceTarget?.title || '当前作品'}》的升级版本交给 Torra？`,
      description: 'Fluxa 只会把本次 Torra 分析选中的升级版本交给 Torra，原有高质量版本不会被主动清理。',
      confirmLabel: '确认交给 Torra',
      onConfirm: () => {
        resourceAutomationRequestRef.current?.abort();
        const controller = new AbortController();
        resourceAutomationRequestRef.current = controller;
        setResourceActionBusy('download');
        setResourceActionMessage('正在把升级版本交给 Torra…');
        startRssMatchDownload(match.id, {
          confirm: true,
          idempotencyKey: createIdempotencyKey(),
          analysisActionId: analysis.id
        }, { signal: controller.signal })
          .then((action) => {
            if (controller.signal.aborted) return;
            setResourceAutomationAction(action);
            setResourceActionMessage(automationStatusLabel(action));
            if (resourceAutomationRequestRef.current === controller) resourceAutomationRequestRef.current = null;
            void pollAutomationAction(
              action.id,
              resourceAutomationRequestRef,
              setResourceAutomationAction,
              setResourceActionMessage,
              (latest) => setResourceActionMessage(automationStatusLabel(latest))
            );
          })
          .catch((reason: unknown) => {
            if (!controller.signal.aborted) {
              setResourceActionMessage(reason instanceof Error ? reason.message : 'Torra 候选下载提交失败');
            }
          })
          .finally(() => {
            if (resourceAutomationRequestRef.current === controller) resourceAutomationRequestRef.current = null;
            if (!controller.signal.aborted) setResourceActionBusy('');
          });
      }
    });
  };

  const openDetail = (
    item: SubscriptionItem,
    options: { seasonNumber?: number | null; historyMode?: UrlHistoryMode | false; toggle?: boolean } = {}
  ) => {
    if (!item.id) {
      return;
    }
    if (detailId === item.id && options.toggle !== false) {
      closeDetail();
      return;
    }
    const requestedSeason = options.seasonNumber ?? item.seasonNumber ?? null;
    const historyMode = options.historyMode === undefined
      ? (detailId || currentHistoryEntryIs(followingDetailHistoryKind) ? 'replace' : 'push')
      : options.historyMode;
    qualityAutomationRequestRef.current?.abort();
    qualityAutomationRequestRef.current = null;
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setDetailId(item.id);
    setDetail(null);
    setQualityWatch(null);
    setQualityAutomationAction(null);
    setMoviePilotPreview(null);
    setQualityWatchMessage('');
    setMoviePilotMessage('');
    setDetailSeason(requestedSeason);
    setSelectedUnitId('');
    setDetailLoading(true);
    setTorraPushPreview(null);
    setTorraPushMessage('');
    if (historyMode) writeFollowingDetailTarget(item, requestedSeason, historyMode);
    Promise.allSettled([
      item.readOnly
        ? Promise.resolve({ success: true, detail: null, seasons: [] } as SubscriptionDetailResponse)
        : getSubscriptionDetail(item.id, requestedSeason ?? undefined, { signal: controller.signal }),
      getSubscriptionQualityWatch(item.id, { signal: controller.signal })
    ])
      .then(([detailResult, watchResult]) => {
        if (controller.signal.aborted) return;
        if (detailResult.status === 'fulfilled') {
          setDetail(detailResult.value);
          const requested = detailResult.value.seasons.find((season) => (
            season.seasonNumber ?? season.season_number ?? 0
          ) === requestedSeason);
          const resolvedSeason = requested ?? detailResult.value.seasons[0];
          const resolvedSeasonNumber = resolvedSeason?.seasonNumber ?? resolvedSeason?.season_number ?? null;
          setDetailSeason(resolvedSeasonNumber);
          if (resolvedSeasonNumber !== requestedSeason) writeFollowingDetailTarget(item, resolvedSeasonNumber, 'replace');
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

  const selectDetailSeason = (item: SubscriptionItem, seasonNumber: number) => {
    setDetailSeason(seasonNumber);
    writeFollowingDetailTarget(item, seasonNumber, 'replace');
  };

  useEffect(() => {
    if (!subscriptionsOnly || !navigationTarget) return;
    const urlTarget = readFollowingDetailTarget();
    if (!urlTarget.subscriptionId && !urlTarget.tmdbId) return;
    const targetSeason = urlTarget.seasonNumber ?? navigationTarget.seasonNumber ?? null;
    const targetItem = subs.find((item) => urlTarget.subscriptionId && item.id === urlTarget.subscriptionId)
      ?? subs.find((item) => urlTarget.tmdbId && String(item.tmdbId || '') === urlTarget.tmdbId && item.seasonNumber === targetSeason)
      ?? subs.find((item) => urlTarget.tmdbId && String(item.tmdbId || '') === urlTarget.tmdbId);
    if (!targetItem) return;
    if (detailId === targetItem.id) {
      if (targetSeason != null) setDetailSeason(targetSeason);
      return;
    }
    openDetail(targetItem, { seasonNumber: targetSeason, historyMode: false, toggle: false });
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(`[data-subscription-id="${CSS.escape(targetItem.id || '')}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  }, [detailId, navigationTarget, subscriptionsOnly, subs]);

  useEffect(() => {
    if (!subscriptionsOnly) return undefined;
    const restoreDetail = () => {
      const target = readFollowingDetailTarget();
      if (!target.subscriptionId && !target.tmdbId) {
        closeDetail(false);
        return;
      }
      const item = subs.find((candidate) => target.subscriptionId && candidate.id === target.subscriptionId)
        ?? subs.find((candidate) => target.tmdbId && String(candidate.tmdbId || '') === target.tmdbId && candidate.seasonNumber === target.seasonNumber)
        ?? subs.find((candidate) => target.tmdbId && String(candidate.tmdbId || '') === target.tmdbId);
      if (item) openDetail(item, { seasonNumber: target.seasonNumber, historyMode: false, toggle: false });
    };
    window.addEventListener('popstate', restoreDetail);
    return () => window.removeEventListener('popstate', restoreDetail);
  }, [subscriptionsOnly, subs]);

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
    if (resourceWorkflowLocked) return false;
    resourceQualityWatchRequestRef.current?.abort();
    resourceQualityWatchRequestRef.current = null;
    resourceTargetSubscriptionIdRef.current = result.source === 'subscription' ? (result.sourceId ?? '') : '';
    resourceAutomationRequestRef.current?.abort();
    resourceAutomationRequestRef.current = null;
    resourceRequestRef.current?.abort();
    const controller = new AbortController();
    resourceRequestRef.current = controller;
    setResourceTarget(result);
    setResourceData(null);
    setResourceError('');
    setResourceQueries([result.title]);
    setResourceSource('all');
    setResourcePreview(null);
    setResourceActionItem(null);
    setResourceUnitId('');
    setResourceMatch(null);
    setResourceAutomationAction(null);
    setResourceQualityWatch(null);
    setResourceActionBusy('');
    setResourceActionMessage('');
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
    return true;
  };

  const searchSubscriptionResources = (item: SubscriptionItem) => {
    if (!item.id || resourceWorkflowLocked) return;
    const subscriptionId = item.id;
    const target = {
      id: Number(item.tmdbId) || 0,
      mediaType: item.mediaType === 'tv' ? 'tv' : 'movie',
      title: item.title,
      year: item.year ?? '',
      posterUrl: item.posterUrl,
      overview: '',
      rating: 0,
      source: 'subscription',
      sourceLabel: item.sourceLabel || '我的追更',
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
    if (!openResourceSearch(target, aliases)) return;
    const controller = new AbortController();
    resourceQualityWatchRequestRef.current = controller;
    getSubscriptionQualityWatch(subscriptionId, { signal: controller.signal })
      .then((watch) => {
        if (
          controller.signal.aborted
          || resourceTargetSubscriptionIdRef.current !== subscriptionId
          || watch.subscriptionId !== subscriptionId
        ) return;
        setResourceQualityWatch(watch);
      })
      .catch(() => undefined)
      .finally(() => {
        if (resourceQualityWatchRequestRef.current === controller) {
          resourceQualityWatchRequestRef.current = null;
        }
      });
  };

  const closeResourceSearch = () => {
    if (resourcePanelCloseLocked) return;
    if (resourceAutomationInFlight) {
      setSweepMessage(`《${resourceTarget?.title || '当前作品'}》的处理已在后台继续，可到任务中心查看任务记录。`);
    } else if (resourceAutomationAction?.type === 'rewash-download') {
      setSweepMessage(`《${resourceTarget?.title || '当前作品'}》的下载处理已有记录，可到任务中心查看后续。`);
    }
    resourceQualityWatchRequestRef.current?.abort();
    resourceQualityWatchRequestRef.current = null;
    resourceTargetSubscriptionIdRef.current = '';
    resourceAutomationRequestRef.current?.abort();
    resourceAutomationRequestRef.current = null;
    setResourceTarget(null);
    setResourceData(null);
    setResourceError('');
    setResourceQueries([]);
    setResourcePreview(null);
    setResourceActionItem(null);
    setResourceUnitId('');
    setResourceMatch(null);
    setResourceAutomationAction(null);
    setResourceQualityWatch(null);
    setResourceActionBusy('');
    setResourceActionMessage('');
    resourceRequestRef.current?.abort();
    resourceRequestRef.current = null;
  };

  const updateProvider = (value: string) => {
    const nextFilters = { ...filters, provider: value, page: 1 };
    setQuery('');
    setActiveSearch('');
    setSearchPage(1);
    setFilters(nextFilters);
    writeDiscoverUrlState({ filters: nextFilters, query: '', page: 1 }, 'push');
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
  const visibleResourceScopeCounts = useMemo(
    () => countRssResourceScopes(visibleResources.map((item) => item.scope ?? 'scope_pending')),
    [visibleResources]
  );
  const resourceSourceStatuses = resourceData?.sourceStatuses ?? [];

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
          <button
            aria-label="关闭资源搜索"
            className="tool-link"
            disabled={resourcePanelCloseLocked}
            title={resourcePanelCloseLocked
              ? '正在提交，收到动作编号后可关闭'
              : resourceAutomationInFlight
                ? '关闭面板，动作会在后台继续'
                : '关闭'}
            type="button"
            onClick={closeResourceSearch}
          >
            <X aria-hidden="true" size={16} />
          </button>
        </header>
        {resourceLoading && <div className="discover-resource-empty">正在查询本地 RSS 种子箱…</div>}
        {!resourceLoading && resourceError && (
          <div className="discover-resource-empty" role="alert">
            <strong>本地 RSS 种子搜索暂不可用</strong>
            <span>{resourceError}</span>
            <DiscoverSourceStatusList statuses={[
              unavailableSourceStatus('local-rss', '本地 RSS 种子箱', resourceError)
            ]} />
          </div>
        )}
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
            {visibleResourceScopeCounts.total > 0 && (
              <p className="discover-resource-scope-summary" role="status">
                {rssResourceScopeSummaryText(visibleResourceScopeCounts)}
              </p>
            )}
            {resourceData.errors.length > 0 && <p className="discover-resource-notice">{resourceData.errors[0]}</p>}
            <div className="discover-resource-list">
              {visibleResources.map((item, index) => {
                const previewText = resourcePreviewText(item);
                const evidenceLines = resourceEvidenceLines(item);
                const activePreview = resourcePreview === item;
                const activeResourceAction = Boolean(
                  item.rssItemId && resourceActionItem?.rssItemId === item.rssItemId
                );
                const resourceUnits = activeResourceAction && resourceTarget
                  ? matchingResourceUnits(item, resourceTarget.mediaType, resourceQualityWatch)
                  : [];
                const resourceAction = activeResourceAction ? resourceAutomationAction : null;
                const upgradeOptions = resourceAction?.result?.upgradeOptions ?? [];
                return (
                  <article className="discover-resource-row" key={item.rssItemId || `${item.source_key || item.source || 'rss'}-${item.title || index}-${item.date || index}`}>
                    <div>
                      <strong>{resourceTitle(item)}</strong>
                      <small>{resourceMeta(item) || '来源信息未提供'}</small>
                    </div>
                    <div className="discover-resource-row__actions">
                      <button
                        aria-expanded={activePreview}
                        className="tool-link"
                        disabled={!previewText && evidenceLines.length === 0}
                        type="button"
                        onClick={() => setResourcePreview(activePreview ? null : item)}
                      >
                        <FileSearch aria-hidden="true" size={14} />
                        查看识别证据
                      </button>
                      {variant === 'subscription' && item.rssItemId && item.torraHandoffReady && (
                        <button
                          className="ops-action-button ops-action-button--primary"
                          disabled={resourceWorkflowLocked}
                          type="button"
                          onClick={() => void beginResourceAction(item)}
                        >
                          <RefreshCcw aria-hidden="true" size={14} />
                          {activeResourceAction && resourceWorkflowLocked ? '处理中' : activeResourceAction ? '重新选择' : '交给 Torra 处理'}
                        </button>
                      )}
                    </div>
                    {activePreview && (
                      <div className="discover-resource-preview">
                        {evidenceLines.length > 0 && (
                          <ul className="discover-resource-evidence" aria-label="识别证据">
                            {evidenceLines.map((line) => <li key={line}>{line}</li>)}
                          </ul>
                        )}
                        {previewText && <pre>{previewText}</pre>}
                      </div>
                    )}
                    {activeResourceAction && (
                      <div className="discover-resource-workflow" role="status">
                        <div className="discover-resource-workflow__head">
                          <div>
                            <strong>追更处理</strong>
                            <small>{resourceActionMessage || '请选择要处理的季集目标'}</small>
                          </div>
                          {resourceMatch && <span className="state-chip state-chip--ok">匹配已建立</span>}
                        </div>
                        {resourceUnits.length > 0 && (
                          <label>
                            目标季集
                            <select
                              aria-label="选择 RSS 种子对应的追更季集"
                              disabled={Boolean(resourceActionBusy) || Boolean(resourceMatch)}
                              value={resourceUnitId}
                              onChange={(event) => setResourceUnitId(event.target.value)}
                            >
                              {resourceUnits.map((unit) => (
                                <option key={unit.id} value={unit.id}>{unitLabel(unit)}</option>
                              ))}
                            </select>
                          </label>
                        )}
                        {upgradeOptions.length > 0 && (
                          <div className="discover-resource-workflow__options" aria-label="升级候选摘要">
                            {upgradeOptions.map((option, optionIndex) => (
                              <span key={`${option.currentScore}-${option.upgradeScore}-${optionIndex}`}>
                                <b>{option.quality || `评分 ${option.currentScore} → ${option.upgradeScore}`}</b>
                                <small>提升 {option.scoreGain}{option.size != null ? ` · ${String(option.size)}` : ''}</small>
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="discover-resource-workflow__actions">
                          {(!resourceMatch || !resourceAction || ['failed', 'cancelled'].includes(resourceAction.status)) && (
                            <button
                              className="ops-action-button ops-action-button--primary"
                              disabled={!resourceUnitId || Boolean(resourceActionBusy)}
                              type="button"
                              onClick={() => void analyzeResourceMatch(Boolean(resourceMatch?.triggerActionId))}
                            >
                              <RefreshCcw aria-hidden="true" size={14} />
                              {resourceActionBusy === 'analysis' ? '正在检查' : resourceMatch ? '重新检查' : '检查可用版本'}
                            </button>
                          )}
                          {resourceAction?.type === 'rewash-analysis'
                            && resourceAction.status === 'succeeded'
                            && (resourceAction.result?.selectedCount ?? 0) > 0 && (
                            <button
                              className="ops-action-button ops-action-button--primary"
                              disabled={Boolean(resourceActionBusy)}
                              type="button"
                              onClick={confirmResourceDownload}
                            >
                              <Download aria-hidden="true" size={14} />
                              交给 Torra 处理
                            </button>
                          )}
                          {resourceAction?.type === 'rewash-download' && (
                            <button
                              className="tool-link"
                              type="button"
                              onClick={() => onNavigate('tasks', {
                                subscriptionId: resourceTarget.sourceId,
                                tmdbId: resourceTarget.tmdbId,
                                title: resourceTarget.title,
                                seasonNumber: resourceTarget.seasonNumber
                              })}
                            >
                              <Database aria-hidden="true" size={14} />查看任务后续
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </article>
                );
              })}
              {visibleResources.length === 0 && (
                <div className="discover-resource-empty">
                  <strong>{variant === 'subscription' ? '暂未找到符合该追更身份和季集的种子' : '本地种子箱中暂未找到匹配种子'}</strong>
                  <small>已搜索：{resourceQueries.join(' / ')}</small>
                  <DiscoverSourceStatusList statuses={resourceSourceStatuses} />
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
      <section className={subscriptionsOnly ? 'ops-hero ops-hero--discover ops-hero--compact' : 'ops-hero ops-hero--discover'}>
        <div>
          <p className="ops-eyebrow">{subscriptionsOnly ? '自动获取' : '找片'}</p>
          <h1>{subscriptionsOnly ? '追更' : '发现'}</h1>
          <p className={subscriptionsOnly ? 'ops-page-subtitle' : 'ops-discover-subtitle'}>{subscriptionsOnly ? '管理正在追的电影和剧集。' : '找到想看的内容，加入追更即可。'}</p>
          <p className="ops-deck">{subscriptionsOnly ? '在这里查看进度、调整季数或重新交给 Torra；后续下载和入库会自动回到任务中心。' : '可以浏览榜单、国内平台和海外流媒体；加入追更后由 PT 主线继续处理。'}</p>
        </div>
        <div className={subscriptionsOnly ? 'ops-discover-policy ops-discover-policy--compact' : 'ops-discover-policy'}>
          <span><Database size={15} />{subscriptionsOnly ? '默认 PT / Torra' : '默认获取方式'}</span>
          {!subscriptionsOnly && <strong>PT / Torra</strong>}
          <small><Send size={13} />{followPolicyHint}</small>
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
          <div className="ops-panel ops-empty discover-empty">
            <strong>内容来源尚未配置</strong>
            <span>请在控制室配置 TMDB API Key 或 Bearer Token，保存后再重新搜索。</span>
            <DiscoverSourceStatusList statuses={discoverSourceStatuses} />
          </div>
        )}
        {configured && !loading && discoverError && (
          <div className="ops-panel ops-empty discover-empty" role="alert">
            <strong>本次搜索未完成</strong>
            <span>{discoverError}</span>
            <DiscoverSourceStatusList statuses={discoverSourceStatuses} />
          </div>
        )}
        {configured && loading && <div className="ops-panel ops-empty discover-empty">正在读取内容来源…</div>}
        {configured && !loading && !discoverError && results.length === 0 && (
          <div className="ops-panel ops-empty discover-empty">
            <strong>{activeSearch ? `没有找到“${activeSearch}”` : '当前筛选没有结果'}</strong>
            <span>已完成本次来源查询；可以换关键词、类型或年份再试。</span>
            <DiscoverSourceStatusList statuses={discoverSourceStatuses} />
          </div>
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
                    fallbackVariant="icon"
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
                        disabled={resourceWorkflowLocked}
                        title={resourceWorkflowLocked ? '当前追更操作完成后可切换资源' : '搜索本地 RSS 资源'}
                        type="button"
                        onClick={() => openResourceSearch(result)}
                      >
                        <FileSearch aria-hidden="true" size={14} />
                        {resourceActive ? (resourceLoading ? '查询中' : '查看结果') : '资源'}
                      </button>
                      <button
                        className={subscribed ? 'tool-link discover-card__action discover-card__action--done' : 'tool-link discover-card__action'}
                        disabled={subscribed || !canSubscribe || followWriteDisabled || Boolean(subscriptionAction)}
                        title={followWriteDisabled ? '追更写入已关闭' : canSubscribe ? '加入追更' : '未匹配到 TMDB，暂不能追更'}
                        type="button"
                        onClick={() => subscribe(result)}
                      >
                        {subscribed ? <Check aria-hidden="true" size={14} /> : <Plus aria-hidden="true" size={14} />}
                        {subscribed ? '已追更' : subscriptionAction === `save:${result.mediaType}:${tmdbIdForResult(result)}` ? '保存中' : canSubscribe ? followButtonLabel : '待匹配'}
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

      {!subscriptionsOnly && (
        <aside className="ops-inspector discover-recent-follows" aria-label="最近追更">
          <div className="activity-panel__head">
            <div><small>自动获取</small><h2>最近追更</h2></div>
          </div>
          {sweepMessage && <p className="console-panel__hint">{sweepMessage}</p>}
          {subsLoading && <p className="console-panel__hint">正在读取追更…</p>}
          {subsError && (
            <div className="subscription-read-error" role="alert">
              <span>{subsError}</span>
              <button className="ops-action-button" disabled={subsLoading} type="button" onClick={loadSubs}>
                <RefreshCcw aria-hidden="true" size={14} />重试
              </button>
            </div>
          )}
          {!subsLoading && !subsError && recentFollows.length === 0 && (
            <p className="console-panel__hint">还没有追更内容；从左侧搜索结果加入追更后会显示在这里。</p>
          )}
          {!subsLoading && !subsError && recentFollows.map((item) => (
            <a
              className="discover-recent-follow"
              href={item.id ? `/following?subscriptionId=${encodeURIComponent(item.id)}` : '/following'}
              key={item.id ?? `${item.mediaType}:${item.tmdbId}:${item.title}`}
              onClick={(event) => {
                event.preventDefault();
                onNavigate('subscriptions', item.id ? { subscriptionId: item.id } : undefined);
              }}
            >
              <PosterImage
                className="discover-sub__poster"
                fallbackClassName="discover-sub__poster--fallback"
                src={item.posterUrl}
                title={item.title}
              />
              <span>
                <strong>{item.title}</strong>
                <small>{followScopeLabel(item)} · {fulfillmentLabel(item)}</small>
                <em>{subscriptionUpdateLabel(item.updatedAt)}</em>
              </span>
            </a>
          ))}
          <a
            className="tool-link discover-recent-follows__all"
            href="/following"
            onClick={(event) => {
              event.preventDefault();
              onNavigate('subscriptions');
            }}
          >
            查看全部追更
            <ChevronRight aria-hidden="true" size={14} />
          </a>
        </aside>
      )}
      {subscriptionsOnly && (
      <aside className="ops-inspector ops-subscription-console discover-subs discover-subs--full" aria-label="我的追更">
        <div className="activity-panel__head">
          <div><small>自动获取</small><h2>我的追更</h2></div>
          <span className="queue-count">
            {subscriptionCountsUnavailable
              ? (subsLoading ? '读取中' : '—')
              : `${workbench?.page.total ?? workbenchStats.total} 条`}
          </span>
          <div className="subscription-toolbar" aria-label="追更操作">
            <button
              className="ops-action-button ops-action-button--primary subscription-toolbar__primary"
              disabled={Boolean(subscriptionAction) || !localWriteEnabled}
              title={localWriteEnabled ? '更新已启用的自动追更来源' : '本地追更写入已关闭'}
              type="button"
              onClick={runSweep}
            >
              <RefreshCcw aria-hidden="true" size={14} />
              {subscriptionAction === 'run' ? '更新中' : '更新来源'}
            </button>
            <button aria-label="追更设置" className="ops-icon-button subscription-toolbar__icon" title="追更设置" type="button" onClick={() => onNavigate('subscription-settings')}>
              <SlidersHorizontal aria-hidden="true" size={14} />
            </button>
            <button aria-label="刷新追更状态" className="ops-icon-button subscription-toolbar__icon" disabled={subsLoading} title="重新读取工作台状态和追更链路" type="button" onClick={loadSubs}>
              <RefreshCcw aria-hidden="true" size={14} />
            </button>
          </div>
        </div>
        {subscriptionsOnly && workbench && (
          <section className="subscription-workbench-summary" aria-label="追更统计">
            <span><b>{workbenchStats.following}</b>追更中</span>
            <span><b>{workbenchStats.completed}</b>已完成</span>
            <span><b>{workbenchStats.actionRequired}</b>需要处理</span>
            <span><b>{workbenchStats.inLibrary}</b>已入库</span>
            <small>{subscriptionReadAtLabel(workbench.lastReadAt)}</small>
          </section>
        )}
        {sweepMessage && <p className="console-panel__hint">{sweepMessage}</p>}
        {subscriptionsOnly && (
          <details className="subscription-advanced">
            <summary>
              <span>高级诊断</span>
              <small>连接、对账与 Torra 镜像</small>
            </summary>
            {workbench && (
              <section className="subscription-capabilities" aria-label="追更工作台能力状态">
                {workbench.capabilities.map((capability) => (
                  <div className={`subscription-capability is-${capability.state}`} key={capability.key} title={capability.detail}>
                    <span aria-hidden="true" />
                    <div><strong>{capability.label}</strong><small>{capability.detail}</small></div>
                  </div>
                ))}
              </section>
            )}
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
          </details>
        )}
        <div className="discover-sub-tabs" role="tablist" aria-label="追更类型">
          {([
            ['movie', '电影追更', subscriptionCountsUnavailable ? '—' : subscriptionsOnly ? workbenchStats.movie : subs.filter((item) => item.mediaType === 'movie').length],
            ['tv', '电视剧追更', subscriptionCountsUnavailable ? '—' : subscriptionsOnly ? workbenchStats.tv : subs.filter((item) => item.mediaType === 'tv').length],
            ['blocked', '被屏蔽', subscriptionCountsUnavailable ? '—' : blockedTitles.length]
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
                closeDetail('replace');
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
                aria-label="搜索追更标题或关键词"
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
              <select aria-label="追更年份" value={subscriptionYear} onChange={(event) => setSubscriptionYear(event.target.value)}>
                <option value="all">全部年份</option>
                {subscriptionYears.map((year) => <option key={year} value={year}>{year}</option>)}
              </select>
              <button
                className={missingEpisodesOnly ? 'is-active' : undefined}
                type="button"
                onClick={() => setMissingEpisodesOnly((current) => !current)}
              >仅缺集</button>
            </div>
          </div>
        )}

        {subsLoading && <p className="console-panel__hint">正在读取追更工作台…</p>}
        {subsError && (
          <div className="subscription-read-error" role="alert">
            <span>{subsError}</span>
            <button className="ops-action-button" disabled={subsLoading} type="button" onClick={loadSubs}>
              <RefreshCcw aria-hidden="true" size={14} />重试
            </button>
          </div>
        )}

        {!subsLoading && !subsError && subscriptionTab === 'blocked' && blockedTitles.length === 0 && (
          <p className="console-panel__hint">暂无被屏蔽追更。</p>
        )}
        {!subsLoading && !subsError && subscriptionTab === 'blocked' && blockedTitles.map((title) => (
          <div className="discover-sub-blocked" key={title}>
            <div><Ban aria-hidden="true" size={14} /><span><strong>{title}</strong><small>自动追更会跳过这个标题</small></span></div>
            <button className="tool-link" disabled={Boolean(subscriptionAction) || !localWriteEnabled} type="button" onClick={() => unblockItem(title)}>取消屏蔽</button>
          </div>
        ))}

        {!subsLoading && !subsError && subscriptionsOnly && subscriptionTab !== 'blocked' && subs.length === 0 && (
          <section className="subscription-empty-guide" aria-label="导入 Torra 追更引导">
            <Database aria-hidden="true" size={24} />
            <div>
              <strong>本地追更台账为空</strong>
              <p>先只读预览 Torra 现有追更，确认数量和冲突后再导入 Fluxa。第一阶段不会修改或删除 Torra 远端数据。</p>
            </div>
            <ol>
              <li className={torraSyncPreview ? 'is-complete' : 'is-current'}><b>1</b><span>预览 Torra 追更</span></li>
              <li className={torraSyncPreview ? 'is-current' : undefined}><b>2</b><span>检查新增、重复和冲突</span></li>
              <li><b>3</b><span>明确确认后导入本地台账</span></li>
            </ol>
            <footer>
              <button className="ops-action-button" disabled={Boolean(torraSyncBusy)} type="button" onClick={previewTorraMirror}>
                <Database aria-hidden="true" size={14} />
                {torraSyncBusy === 'preview' ? '读取中' : '预览 Torra 追更'}
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
          <p className="console-panel__hint">当前筛选下没有追更内容。</p>
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
            ? item.torraSyncState === 'remote_missing' ? 'Torra 远端已缺失' : 'Torra 已有追更，只读同步'
            : '由 Fluxa 管理，可检查后推送';
          const userStatus = subscriptionUserStatus(item);
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
                  disabled={resourceWorkflowLocked}
                  title={resourceWorkflowLocked ? '当前追更操作完成后可切换资源' : '只读搜索资源'}
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
                  aria-label={`屏蔽追更 ${item.title}`}
                  className="tool-link"
                  disabled={Boolean(subscriptionAction) || !localWriteEnabled}
                  title="删除并屏蔽：自动追更不再加回"
                  type="button"
                  onClick={() => blockItem(item)}
                >
                  <Ban aria-hidden="true" size={14} />
                </button>}
                {!item.readOnly && <button aria-label={`删除追更 ${item.title}`} className="tool-link" disabled={Boolean(subscriptionAction) || !localWriteEnabled} title={localWriteEnabled ? '只删除，不加入屏蔽列表' : '本地追更写入已关闭'} type="button" onClick={() => removeSubscription(item)}>
                  <Trash2 aria-hidden="true" size={14} />
                </button>}
              </div>

              {subscriptionsOnly && (
                <div className="discover-sub__chain" aria-label={`${item.title} 处理状态`}>
                  <span className={item.fulfillmentState === 'completed' ? 'is-ok' : item.fulfillmentState === 'blocked' ? 'is-warn' : undefined}>
                    <b>当前进展</b><small>{fulfillmentLabel(item)}</small>
                  </span>
                  <span className={item.qb?.status === 'blocked' ? 'is-warn' : item.qb?.status === 'done' || item.qb?.status === 'active' ? 'is-ok' : undefined}>
                    <b>下载</b><small>{item.qb?.detail || (item.inLibrary ? '已完成' : '等待下载任务')}</small>
                  </span>
                  <span className={item.library?.status === 'done' || item.inLibrary ? 'is-ok' : item.library?.status === 'blocked' ? 'is-warn' : undefined}>
                    <b>入库</b><small>{item.library?.detail || libraryProgress}</small>
                  </span>
                  <span className={(item.missingEpisodes?.length ?? 0) > 0 ? 'is-warn' : undefined}>
                    <b>缺集</b><small>{item.missingEpisodes?.length ? item.missingEpisodes.join('、') : item.inLibrary ? '无' : '尚未确认'}</small>
                  </span>
                  <span>
                    <b>最近检查</b><small><RelativeTime value={item.observedAt || item.updatedAt} /></small>
                  </span>
                  {userStatus && <p className={subscriptionUserStatusTone(item)}><strong>当前状态</strong>{userStatus}</p>}
                  <details className="discover-sub__advanced">
                    <summary>高级诊断</summary>
                    <div>
                      <span className={item.reconciliationState === 'linked' ? 'is-ok' : ['conflict', 'remote_missing'].includes(item.reconciliationState ?? '') ? 'is-warn' : undefined} title={item.reasonText || item.torra?.detail}>
                        <b>Fluxa / Torra</b><small>{reconciliationLabel(item)}</small>
                      </span>
                      <span><b>范围</b><small>{item.scope || subscriptionScope}</small></span>
                      <span><b>健康证据</b><HealthBadge label={item.healthState ? undefined : '尚未确认'} state={item.healthState || 'evidence_insufficient'} /></span>
                      <span title={item.torra?.detail}><b>Torra</b><small>{item.torra?.detail || torraRoute}</small></span>
                      <span className={item.qb?.status === 'blocked' ? 'is-warn' : item.qb?.status === 'done' || item.qb?.status === 'active' ? 'is-ok' : undefined} title={item.qb?.detail}>
                        <b>qB</b><small>{item.qb?.detail || '未接入'}</small>
                      </span>
                      <span className={item.cloud115?.status === 'blocked' ? 'is-warn' : item.cloud115?.status === 'done' ? 'is-ok' : undefined} title={item.cloud115?.detail}>
                        <b>115</b><small>{item.cloud115?.detail || '暂无逐文件证据'}</small>
                      </span>
                    </div>
                  </details>
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
                    <small className="sub-detail__hint">
                      {item.readOnly ? '这是 Torra 只读追更，Fluxa 正在读取下载与入库进度。' : '没有 TMDB 匹配，暂无详情。'}
                    </small>
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
                      <section className="sub-detail__route" aria-label="追更处理轨道">
                        <header>
                          <div><strong>追更处理轨道</strong><small>从追更到入库的当前状态</small></div>
                          <span>{subscriptionUpdateLabel(item.updatedAt)}</span>
                        </header>
                        <div className="sub-detail__route-grid">
                          <span><b>01</b><strong>追更来源</strong><small>{item.readOnly ? 'Torra 镜像' : item.sourceLabel || 'Fluxa'}</small></span>
                          <span><b>02</b><strong>追更范围</strong><small>{subscriptionScope}</small></span>
                          <span><b>03</b><strong>PT / Torra</strong><small>{torraRoute}</small></span>
                          <span className={detailInfo?.inLibrary || item.inLibrary ? 'is-complete' : undefined}><b>04</b><strong>整理入库</strong><small>{libraryProgress}</small></span>
                        </div>
                        <footer>
                          <button
                            className="tool-link"
                            disabled={resourceWorkflowLocked}
                            title={resourceWorkflowLocked ? '当前追更操作完成后可切换资源' : '只读搜索资源'}
                            type="button"
                            onClick={() => searchSubscriptionResources(item)}
                          >
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
                                onClick={() => selectDetailSeason(item, seasonNumber)}
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
                            title="通过 NasEmby 原保存接口更新追更季"
                            type="button"
                            onClick={() => changeSeason(item, activeSeasonNumber, activeSeason.name)}
                          >
                            <Check aria-hidden="true" size={14} />
                            改为追更第 {activeSeasonNumber} 季
                          </button>
                      )}
                    </>
                  )}
                  <section className="sub-detail__section quality-watch-panel">
                    <div className="quality-watch-panel__head">
                      <div><strong>质量观察与人工追更</strong><small>{qualityWatch ? `${qualityWatch.policy.windowHours} 小时观察窗口` : '读取中'}</small></div>
                      {qualityWatch && (
                        <span className={qualityWatch.paused || qualityWatch.units.length === 0 ? 'state-chip' : 'state-chip state-chip--ok'}>
                          {qualityWatch.paused ? '已暂停' : qualityWatch.units.length > 0 ? '观察中' : '等待基线'}
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
                          {!qualityWatch.readOnly && !item.readOnly && (
                            <>
                              <button
                                className="tool-link"
                                disabled={qualityWatchBusy === `update:${item.id}` || Boolean(qualityAutomationAction && !terminalAutomationStates.has(qualityAutomationAction.status))}
                                type="button"
                                onClick={() => updateQualityWatch(item, { paused: !qualityWatch.paused })}
                              >
                                {qualityWatch.paused ? <Play size={13} /> : <Pause size={13} />}
                                {qualityWatch.paused ? '恢复观察' : '暂停观察'}
                              </button>
                              <button
                                className="tool-link"
                                disabled={qualityWatchBusy === `update:${item.id}` || Boolean(qualityAutomationAction && !terminalAutomationStates.has(qualityAutomationAction.status))}
                                type="button"
                                onClick={() => {
                                  const windowHours = qualityWatch.policy.windowHours === 24 ? 48 : 24;
                                  updateQualityWatch(item, { windowHours, scheduleMinutes: windowHours === 24 ? [720, 1440] : [720, 1440, 2880] });
                                }}
                              >
                                <RotateCcw size={13} />切换 {qualityWatch.policy.windowHours === 24 ? '48' : '24'} 小时窗口
                              </button>
                            </>
                          )}
                          <button
                            className="ops-action-button ops-action-button--primary"
                            disabled={Boolean(qualityWatchBusy) || Boolean(qualityAutomationAction && !terminalAutomationStates.has(qualityAutomationAction.status)) || (qualityWatch.units.length > 1 && !selectedUnitId)}
                            type="button"
                            onClick={() => startAnalysis(item)}
                          >
                            <RefreshCcw size={13} />{qualityWatchBusy === `analysis:${item.id}` ? '正在提交' : '人工分析升级'}
                          </button>
                          {qualityAutomationAction?.status === 'succeeded' && qualityAutomationAction.type === 'rewash-analysis' && (qualityAutomationAction.result?.selectedCount ?? 0) > 0 && (
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
                        {qualityAutomationAction && <p className="quality-watch-panel__status" role="status">{automationStatusLabel(qualityAutomationAction)}</p>}
                      </>
                    ) : (
                      <small className="sub-detail__hint">当前没有可操作的观察单元，等待首个版本或入库基线。</small>
                    )}
                    {qualityWatchMessage && <p className="quality-watch-panel__status" role="status">{qualityWatchMessage}</p>}
                  </section>
                  {!item.readOnly && <section className="sub-detail__section moviepilot-backup-panel">
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
                    {moviePilotPreview && <small className="sub-detail__hint">{moviePilotPreview.ready ? `模式：${moviePilotPreview.mode === 'search-existing' ? '已有追更触发搜索' : '创建追更并触发搜索'}` : moviePilotPreview.blockers.join('；')}</small>}
                    {moviePilotMessage && <p className="quality-watch-panel__status" role="status">{moviePilotMessage}</p>}
                  </section>}
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
                        <div><dt>在线查重</dt><dd>{torraPushPreview.preview.duplicate?.found ? `已存在：${torraPushPreview.preview.duplicate.name || torraPushPreview.preview.duplicate.subscriptionId}` : torraPushPreview.preview.duplicate?.checked ? '未发现重复追更' : '尚未完成'}</dd></div>
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
      )}
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
