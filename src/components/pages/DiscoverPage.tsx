import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Ban, Check, ChevronLeft, ChevronRight, CloudOff, Database, ExternalLink, FileSearch, Plus, RefreshCcw, RotateCcw, Search, SlidersHorizontal, Trash2, X } from 'lucide-react';
import {
  blockSubscription,
  browseDiscover,
  deleteSubscription,
  getSubscriptionDetail,
  getSubscriptionItems,
  runSubscriptionSweep,
  saveSubscription,
  searchDiscover,
  searchDiscoverResources,
  setSubscriptionSeason,
  unblockSubscription
} from '../../services/api';
import type {
  DiscoverBrowseParams,
  DiscoverResourceItem,
  DiscoverResourceResponse,
  DiscoverResult,
  SubscriptionDetailResponse,
  SubscriptionItem
} from '../../types/subscriptions';
import type { PageId } from '../layout/AppTopNav';

interface DiscoverPageProps {
  onNavigate: (page: PageId) => void;
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

function safeExternalUrl(value: string | undefined) {
  if (!value) return '';
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : '';
  } catch {
    return '';
  }
}

function resourceLinks(item: DiscoverResourceItem) {
  return Array.from(new Set([
    item.share_url,
    item.url,
    item.preview_url,
    ...(item.links ?? [])
  ].map(safeExternalUrl).filter(Boolean)));
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

type SubscriptionTab = 'movie' | 'tv' | 'blocked';
type SubscriptionStatusFilter = 'all' | 'pending' | 'done';
type SubscriptionUpdateFilter = 'all' | 'today' | '3' | '7';

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

export function DiscoverPage({ onNavigate }: DiscoverPageProps) {
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
  const [subsError, setSubsError] = useState('');
  const [subscriptionTab, setSubscriptionTab] = useState<SubscriptionTab>('tv');
  const [subscriptionKeyword, setSubscriptionKeyword] = useState('');
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
  const [resourceSource, setResourceSource] = useState('all');
  const [resourcePreview, setResourcePreview] = useState<DiscoverResourceItem | null>(null);

  const loadSubs = useCallback(() => {
    setSubsLoading(true);
    setSubsError('');
    getSubscriptionItems(true)
      .then((payload) => {
        if (payload.subscriptions) {
          setSubs(payload.subscriptions.items);
          setBlockedTitles(payload.blockedTitles ?? []);
        }
      })
      .catch(() => setSubsError('订阅引擎当前不可用，没有回退到旧订阅台账。'))
      .finally(() => setSubsLoading(false));
  }, []);

  useEffect(() => {
    loadSubs();
  }, [loadSubs]);

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
  }, [activeSearch, applyPayload, filters]);

  useEffect(() => {
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
  }, [activeSearch, applyPayload, searchPage]);

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
    if (!window.confirm(`确认订阅《${payload.title}》？\n将保存到 NasEmby 订阅中枢，获取通道仍按 PT 优先策略执行。`)) return;
    setSubscriptionAction(`save:${payload.mediaType}:${payload.tmdbId}`);
    saveSubscription(payload)
      .then(() => {
        setSweepMessage(`已保存订阅：${payload.title}`);
        loadSubs();
      })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '保存订阅失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const runSweep = () => {
    if (!window.confirm('确认让 NasEmby 订阅中枢立即执行一轮？')) return;
    setSubscriptionAction('run');
    runSubscriptionSweep()
      .then(() => {
        setSweepMessage('已触发 NasEmby 执行一轮，列表正在重新读取。');
        loadSubs();
      })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '执行失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const removeSubscription = (item: SubscriptionItem) => {
    if (!item.id || !window.confirm(`确认删除订阅《${item.title}》？\n删除后不会加入屏蔽列表。`)) return;
    setSubscriptionAction(`delete:${item.id}`);
    deleteSubscription(item.id)
      .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已删除订阅：${item.title}`); })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '删除失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const blockItem = (item: SubscriptionItem) => {
    if (!item.id || !window.confirm(`确认删除并屏蔽《${item.title}》？\n自动订阅后续会跳过这个标题。`)) return;
    setSubscriptionAction(`block:${item.id}`);
    blockSubscription({ id: item.id, title: item.title })
      .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已屏蔽订阅：${item.title}`); })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '屏蔽失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const unblockItem = (title: string) => {
    setSubscriptionAction(`unblock:${title}`);
    unblockSubscription(title)
      .then(() => { loadSubs(); setSweepMessage(`已取消屏蔽：${title}`); })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '取消屏蔽失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const changeSeason = (item: SubscriptionItem, seasonNumber: number, seasonName?: string) => {
    if (!item.id || !window.confirm(`确认把《${item.title}》改为订阅第 ${seasonNumber} 季？`)) return;
    setSubscriptionAction(`season:${item.id}`);
    setSubscriptionSeason(item.id, seasonNumber, seasonName)
      .then(() => { closeDetail(); loadSubs(); setSweepMessage(`已更新订阅季：${item.title} · S${seasonNumber}`); })
      .catch((error: unknown) => setSweepMessage(error instanceof Error ? error.message : '更新订阅季失败'))
      .finally(() => setSubscriptionAction(''));
  };

  const closeDetail = () => {
    setDetailId(null);
    setDetail(null);
    setDetailSeason(null);
  };

  const openDetail = (item: SubscriptionItem) => {
    if (!item.id) {
      return;
    }
    if (detailId === item.id) {
      closeDetail();
      return;
    }
    setDetailId(item.id);
    setDetail(null);
    setDetailSeason(null);
    setDetailLoading(true);
    getSubscriptionDetail(item.id)
      .then((payload) => {
        setDetail(payload);
        const firstSeason = payload.seasons[0];
        setDetailSeason(firstSeason?.seasonNumber ?? firstSeason?.season_number ?? null);
      })
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
  };

  const openResourceSearch = (result: DiscoverResult) => {
    setResourceTarget(result);
    setResourceData(null);
    setResourceError('');
    setResourceSource('all');
    setResourcePreview(null);
    setResourceLoading(true);
    searchDiscoverResources(result)
      .then(setResourceData)
      .catch((error: unknown) => {
        setResourceError(error instanceof Error ? error.message : '资源搜索失败');
      })
      .finally(() => setResourceLoading(false));
  };

  const searchSubscriptionResources = (item: SubscriptionItem) => {
    openResourceSearch({
      id: Number(item.tmdbId) || 0,
      mediaType: item.mediaType === 'tv' ? 'tv' : 'movie',
      title: item.title,
      year: item.year ?? '',
      posterUrl: item.posterUrl,
      overview: '',
      rating: 0,
      source: 'subscription',
      sourceLabel: item.sourceLabel || '我的订阅',
      tmdbId: item.tmdbId
    });
  };

  const closeResourceSearch = () => {
    setResourceTarget(null);
    setResourceData(null);
    setResourceError('');
    setResourcePreview(null);
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

  return (
    <main className="work-page ops-page ops-page--discover">
      <section className="ops-hero ops-hero--discover">
        <div>
          <p className="ops-eyebrow">DISCOVER / SUBSCRIPTION HUB</p>
          <h1>从内容发现开始，但所有自动获取最终都回到 PT 主链。</h1>
          <p className="ops-deck">榜单、筛选和搜索负责选片；订阅中枢负责去重、分类和等待 Torra 获取。云盘只保留人工补资源入口。</p>
        </div>
        <div className="ops-discover-policy">
          <span><Database size={15} />默认通道</span>
          <strong>PT / Torra</strong>
          <small><CloudOff size={13} />自动云盘兜底关闭</small>
        </div>
      </section>

      <div className="ops-discover-layout">
      <div>
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
            return (
              <article className="ops-panel discover-card" key={`${result.mediaType}-${result.source || 'tmdb'}-${result.sourceId || result.id}`}>
                {result.posterUrl ? (
                  <img alt="" className="discover-card__poster" loading="lazy" src={result.posterUrl} />
                ) : (
                  <span aria-hidden="true" className="discover-card__poster discover-card__poster--fallback">
                    {result.title.charAt(0)}
                  </span>
                )}
                <div className="discover-card__body">
                  <strong title={result.title}>{result.title}</strong>
                  <small>{resultMeta(result)}</small>
                  {result.overview && <p>{result.overview}</p>}
                  <div className="discover-card__actions">
                    <button className="tool-link discover-card__action" type="button" onClick={() => openResourceSearch(result)}>
                      <FileSearch aria-hidden="true" size={14} />
                      资源
                    </button>
                    <button
                      className={subscribed ? 'tool-link discover-card__action discover-card__action--done' : 'tool-link discover-card__action'}
                      disabled={subscribed || !canSubscribe}
                      title={canSubscribe ? '保存到 NasEmby 订阅中枢' : '未匹配到 TMDB，暂不能订阅'}
                      type="button"
                      onClick={() => subscribe(result)}
                    >
                      {subscribed ? <Check aria-hidden="true" size={14} /> : <Plus aria-hidden="true" size={14} />}
                      {subscribed ? '已订阅' : subscriptionAction === `save:${result.mediaType}:${tmdbIdForResult(result)}` ? '保存中' : canSubscribe ? '订阅' : '待匹配'}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>

        {resourceTarget && (
          <section className="discover-resource-panel" aria-label={`${resourceTarget.title} 资源搜索结果`}>
            <header className="discover-resource-panel__head">
              <div>
                <small>RESOURCE SEARCH</small>
                <h2>{resourceTarget.title}</h2>
                <p>{resourceLoading ? '正在读取资源…' : `${visibleResources.length} 条资源`}</p>
              </div>
              <button aria-label="关闭资源搜索" className="tool-link" title="关闭" type="button" onClick={closeResourceSearch}>
                <X aria-hidden="true" size={16} />
              </button>
            </header>

            {resourceLoading && <div className="discover-resource-empty">正在读取资源来源…</div>}
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
                        type="button"
                        onClick={() => {
                          setResourceSource(source.key);
                          setResourcePreview(null);
                        }}
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

                {resourceData.errors.length > 0 && (
                  <p className="discover-resource-notice">{resourceData.errors[0]}</p>
                )}

                <div className="discover-resource-list">
                  {visibleResources.map((item, index) => {
                    const links = resourceLinks(item);
                    const previewText = resourcePreviewText(item);
                    const activePreview = resourcePreview === item;
                    return (
                      <article className="discover-resource-row" key={`${item.source_key || item.source || 'resource'}-${item.url || item.title || index}`}>
                        <div>
                          <strong>{resourceTitle(item)}</strong>
                          <small>{resourceMeta(item) || '来源信息未提供'}</small>
                        </div>
                        <div className="discover-resource-row__actions">
                          {links[0] && (
                            <a className="tool-link" href={links[0]} rel="noreferrer" target="_blank">
                              <ExternalLink aria-hidden="true" size={14} />
                              打开
                            </a>
                          )}
                          <button
                            aria-expanded={activePreview}
                            className="tool-link"
                            disabled={!previewText && links.length === 0}
                            type="button"
                            onClick={() => setResourcePreview(activePreview ? null : item)}
                          >
                            <FileSearch aria-hidden="true" size={14} />
                            预览
                          </button>
                        </div>
                        {activePreview && (
                          <div className="discover-resource-preview">
                            {previewText && <pre>{previewText}</pre>}
                            {links.length > 0 && (
                              <div>
                                {links.map((link) => <a href={link} key={link} rel="noreferrer" target="_blank">{link}</a>)}
                              </div>
                            )}
                          </div>
                        )}
                      </article>
                    );
                  })}
                  {visibleResources.length === 0 && <div className="discover-resource-empty">没有找到资源。</div>}
                </div>
              </>
            )}
          </section>
        )}

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
      </div>

      <aside className="ops-inspector ops-subscription-console discover-subs" aria-label="订阅中枢">
        <div className="activity-panel__head">
          <div><small>SUBSCRIPTION HUB</small><h2>我的订阅</h2></div>
          <span className="queue-count">{subs.length} 条</span>
          <button className="ops-action-button" type="button" onClick={() => onNavigate('subscription-settings')}>
            <SlidersHorizontal aria-hidden="true" size={14} />
            订阅设置
          </button>
          <button className="ops-action-button" disabled={subscriptionAction === 'run'} title="由 NasEmby Core 执行" type="button" onClick={runSweep}>
            <RefreshCcw aria-hidden="true" size={14} />
            {subscriptionAction === 'run' ? '执行中' : '执行一轮'}
          </button>
        </div>
        <div className="ops-subscription-policy"><strong>PT 优先</strong><span>Torra 推送保持安全开关控制</span></div>
        {sweepMessage && <p className="console-panel__hint">{sweepMessage}</p>}
        <div className="discover-sub-tabs" role="tablist" aria-label="订阅类型">
          {([
            ['movie', '电影订阅', subs.filter((item) => item.mediaType === 'movie').length],
            ['tv', '电视剧订阅', subs.filter((item) => item.mediaType === 'tv').length],
            ['blocked', '被屏蔽', blockedTitles.length]
          ] as const).map(([key, label, count]) => (
            <button
              aria-selected={subscriptionTab === key}
              className={subscriptionTab === key ? 'discover-sub-tab discover-sub-tab--active' : 'discover-sub-tab'}
              key={key}
              role="tab"
              type="button"
              onClick={() => {
                setSubscriptionTab(key);
                closeDetail();
              }}
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

        {subsLoading && <p className="console-panel__hint">正在读取 NasEmby 订阅台账…</p>}
        {subsError && <p className="console-panel__hint console-panel__hint--error">{subsError}</p>}

        {!subsLoading && !subsError && subscriptionTab === 'blocked' && blockedTitles.length === 0 && (
          <p className="console-panel__hint">暂无被屏蔽订阅。</p>
        )}
        {!subsLoading && !subsError && subscriptionTab === 'blocked' && blockedTitles.map((title) => (
          <div className="discover-sub-blocked" key={title}>
            <div><Ban aria-hidden="true" size={14} /><span><strong>{title}</strong><small>自动订阅会跳过这个标题</small></span></div>
            <button className="tool-link" disabled={subscriptionAction === `unblock:${title}`} type="button" onClick={() => unblockItem(title)}>取消屏蔽</button>
          </div>
        ))}

        {!subsLoading && !subsError && subscriptionTab !== 'blocked' && visibleSubscriptions.length === 0 && (
          <p className="console-panel__hint">当前筛选下没有订阅内容。</p>
        )}
        {subscriptionTab !== 'blocked' && visibleSubscriptions.map((item) => {
          const seasons = detailId === item.id ? detail?.seasons ?? [] : [];
          const activeSeason = seasons.find((season) =>
            (season.seasonNumber ?? season.season_number ?? 0) === detailSeason
          ) ?? seasons[0];
          const activeSeasonNumber = activeSeason?.seasonNumber ?? activeSeason?.season_number ?? 0;
          const detailInfo = detailId === item.id ? detail?.detail : null;
          return (
            <div className="discover-sub" key={item.id ?? item.title}>
              <div className="activity-row">
                {item.posterUrl ? (
                  <img alt="" aria-hidden="true" className="discover-sub__poster" src={item.posterUrl} />
                ) : (
                  <span aria-hidden="true" className="discover-sub__poster discover-sub__poster--fallback">{item.title.charAt(0)}</span>
                )}
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
                  <em>{item.sourceLabel || 'NasEmby'} · {subscriptionUpdateLabel(item.updatedAt)}</em>
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
                <button
                  aria-label={`屏蔽订阅 ${item.title}`}
                  className="tool-link"
                  disabled={subscriptionAction === `block:${item.id}`}
                  title="删除并屏蔽：自动订阅不再加回"
                  type="button"
                  onClick={() => blockItem(item)}
                >
                  <Ban aria-hidden="true" size={14} />
                </button>
                <button aria-label={`删除订阅 ${item.title}`} className="tool-link" disabled={subscriptionAction === `delete:${item.id}`} title="只删除，不加入屏蔽列表" type="button" onClick={() => removeSubscription(item)}>
                  <Trash2 aria-hidden="true" size={14} />
                </button>
              </div>

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
                                {person.profileUrl ? <img alt="" aria-hidden="true" src={person.profileUrl} /> : <span>{person.name.charAt(0)}</span>}
                                <strong>{person.name}</strong><small>{person.character || '演员'}</small>
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
                                type="button"
                                onClick={() => setDetailSeason(seasonNumber)}
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
                      {item.mediaType === 'tv' &&
                        activeSeasonNumber !== (item.seasonNumber ?? activeSeasonNumber) && (
                          <button
                            className="tool-link"
                            disabled={subscriptionAction === `season:${item.id}`}
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
                </div>
              )}
            </div>
          );
        })}
      </aside>
      </div>
    </main>
  );
}
