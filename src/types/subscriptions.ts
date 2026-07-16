export interface SubscriptionCalendarEntry {
  date: string;
  key?: string;
  title: string;
  episodeLabel: string;
  episodeTitle?: string;
  episodeNumber?: number;
  seasonName?: string;
  seasonNumber?: number;
  tmdbId?: string;
  posterUrl: string;
  inLibrary: boolean;
  mediaType: string;
  sourceLabel: string;
  progressText?: string;
  libraryPaths?: string[];
}

export interface SubscriptionCalendar {
  year: number;
  month: number;
  entries: SubscriptionCalendarEntry[];
  stats: {
    entries: number;
    titles: number;
    inLibrary: number;
    pending: number;
  };
  mediaType?: string;
  errors?: string[];
  errorCount?: number;
}

export interface SubscriptionCalendarResponse {
  configured: boolean;
  calendar: SubscriptionCalendar | null;
}

export interface SubscriptionItem {
  id?: string;
  title: string;
  seasonName: string;
  seasonNumber?: number;
  mediaType: string;
  tmdbId?: string;
  mediaCategory?: MediaCategory;
  allowCloudFallback?: boolean;
  posterUrl: string;
  backdropUrl?: string;
  progressText: string;
  inLibrary: boolean;
  updatedAt: string;
  createdAt?: string;
  year?: string;
  sourceLabel?: string;
  status?: 'pending' | 'done';
  metadataPending?: boolean;
  origin?: 'manual' | 'auto' | 'unknown';
}

export type MediaCategory =
  | 'anime_jp'
  | 'anime_cn'
  | 'tv_cn'
  | 'tv_asia'
  | 'tv_western'
  | 'tv_hk_tw'
  | 'variety'
  | 'movie';

export interface SubscriptionPushPreview {
  ready: boolean;
  blockers: string[];
  warnings: string[];
  category: {
    key: MediaCategory;
    label: string;
    directory: string;
    isAnime: boolean;
  } | null;
  categoryReason: string;
  savePath: string;
  payload: Record<string, unknown> | null;
  duplicate: {
    checked: boolean;
    found: boolean;
    subscriptionId: string;
    name: string;
    error?: string;
  } | null;
}

export interface DiscoverResult {
  id: number;
  mediaType: 'movie' | 'tv';
  title: string;
  year: string;
  posterUrl: string;
  overview: string;
  rating: number;
  originalLanguage?: string;
  genreIds?: number[];
  originCountry?: string[];
  source?: string;
  sourceLabel?: string;
  sourceId?: string;
  tmdbId?: string;
}

export interface DiscoverBrowseParams {
  source: 'tmdb' | 'daily' | 'douban' | 'tencent' | 'youku' | 'iqiyi' | 'mango' | 'streaming';
  type: 'movie' | 'tv';
  trend: 'all' | 'day' | 'week';
  sort: string;
  language: string;
  year: string;
  genre: string;
  provider?: string;
  page: number;
  limit?: number;
}

export interface DiscoverResponse {
  configured: boolean;
  results: DiscoverResult[];
  page?: number;
  totalPages?: number;
  totalResults?: number;
  hasNext?: boolean;
  hasPrev?: boolean;
  sourceLabel?: string;
}

export interface DiscoverResourceItem {
  source?: string;
  source_key?: string;
  source_label?: string;
  drive?: string;
  title?: string;
  subtitle?: string;
  quality?: string;
  size?: string;
  date?: string;
  url?: string;
  preview_url?: string;
  share_url?: string;
  full_text?: string;
  password?: string;
  season?: string | number;
  episodes?: number[];
  links?: string[];
}

export interface DiscoverResourceSource {
  key: string;
  label: string;
  count: number;
}

export interface DiscoverResourceSeason {
  season: string;
  episodes: number[];
  resource_episodes?: number[];
  library_episodes?: number[];
  missing_episodes?: number[];
  notice?: string;
}

export interface DiscoverResourceResponse {
  success: boolean;
  title: string;
  media_type: 'movie' | 'tv';
  items: DiscoverResourceItem[];
  sources: DiscoverResourceSource[];
  seasons: DiscoverResourceSeason[];
  errors: string[];
  cache_hits: string[];
}

export interface SubscriptionList {
  lastRunAt: string;
  items: SubscriptionItem[];
  stats: {
    total: number;
    movie: number;
    tv: number;
  };
}

export interface SubscriptionListResponse {
  configured: boolean;
  subscriptions: SubscriptionList | null;
  blockedTitles?: string[];
  errors?: string[];
  errorCount?: number;
}

export interface SubscriptionHubConfig {
  mode: string;
  cloud_acquisition?: {
    enabled: boolean;
    auto_fallback_enabled: boolean;
    manual_actions_enabled: boolean;
    wait_minutes: number;
    sources: Array<'telegram' | 'hdhive' | 'pansou'>;
    auto_select: boolean;
    policy_version?: number;
  };
  resource_rules?: {
    enabled: boolean;
    auto_transfer: boolean;
    max_per_run: number;
    groups: Record<string, { require: string[]; reject: string[] }>;
  };
  douban: {
    enabled: boolean;
    movie_enabled: boolean;
    tv_enabled: boolean;
    movie_years: string[];
    tv_min_rating: number;
    exclude_titles: string[];
    sources: string[];
    daily_only: boolean;
    task_time: string;
    task_enabled: boolean;
    updated_at: string;
    last_run_at: string;
  };
}

export interface SubscriptionConfigResponse {
  success: boolean;
  config: SubscriptionHubConfig;
  sources?: Array<{ key: string; label: string; mediaType: 'movie' | 'tv' }>;
  error?: string;
}

export interface SubscriptionDetailEpisode {
  episodeNumber?: number;
  episode_number?: number;
  title?: string;
  name?: string;
  overview?: string;
  airDate?: string;
  air_date?: string;
  runtime?: string;
  inLibrary?: boolean;
  libraryPaths?: string[];
}

export interface SubscriptionDetailResponse {
  success: boolean;
  item?: SubscriptionItem;
  detail: {
    title: string;
    tmdbId?: string;
    imdbId?: string;
    year?: string;
    rating?: string;
    overview?: string;
    posterUrl?: string;
    backdropUrl?: string;
    genres?: string[];
    runtime?: string;
    status?: string;
    date?: string;
    country?: string;
    language?: string;
    seasonCount?: number;
    episodeCount?: number;
    mediaType?: string;
    cast?: Array<{ name: string; character: string; profileUrl: string }>;
    inLibrary?: boolean;
    libraryEpisodeCount?: number;
    libraryPaths?: string[];
    release_date?: string;
    first_air_date?: string;
    number_of_seasons?: number;
  } | null;
  seasons: Array<{
    seasonNumber?: number;
    season_number?: number;
    name: string;
    overview?: string;
    posterUrl?: string;
    airDate?: string;
    episodeCount?: number;
    libraryCount?: number;
    episodes: SubscriptionDetailEpisode[];
  }>;
  cacheHit?: boolean;
  error?: string;
}
