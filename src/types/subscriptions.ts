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
  airAt?: string;
  acquiredAt?: string;
  acquisitionSource?: string;
  libraryAt?: string;
  librarySource?: string;
  chainId?: string;
  targetKey?: string;
  healthState?: SubscriptionHealthState;
  reasonCode?: string;
  reasonText?: string;
  observedAt?: string;
  freshUntil?: string;
}

export interface SubscriptionCalendarDayPreview {
  key?: string;
  title: string;
  episodeLabel: string;
  posterUrl: string;
  mediaType: string;
  healthState?: SubscriptionHealthState;
  status: 'upcoming' | 'acquiring' | 'library' | 'missing';
}

export interface SubscriptionCalendarDaySummary {
  date: string;
  total: number;
  statusCounts: {
    upcoming: number;
    acquiring: number;
    library: number;
    missing: number;
  };
  preview: SubscriptionCalendarDayPreview[];
  hasMore: boolean;
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
    acquired?: number;
    libraryEvidence?: number;
    actionRequired?: number;
  };
  timeZone?: 'Asia/Shanghai' | string;
  mediaType?: string;
  errors?: string[];
  errorCount?: number;
  view?: 'legacy' | 'summary' | 'detail';
  days?: SubscriptionCalendarDaySummary[];
}

export interface SubscriptionCalendarResponse {
  configured: boolean;
  calendar: SubscriptionCalendar | null;
}

export interface SubscriptionCalendarTimelineResponse {
  ok: boolean;
  version: string;
  calendar: SubscriptionCalendar;
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
  origin?: 'manual' | 'auto' | 'torra' | 'unknown';
  readOnly?: boolean;
  torraSyncState?: 'current' | 'remote_missing' | 'error' | string;
  torraMappingStatus?: 'mapped' | 'partial' | 'unmapped' | string;
  reconciliationState?: SubscriptionReconciliationState;
  fulfillmentState?: SubscriptionFulfillmentState;
  healthState?: SubscriptionHealthState;
  reasonCode?: string;
  reasonText?: string;
  observedAt?: string;
  freshUntil?: string;
  scope?: string;
  missingEpisodes?: string[];
  torra?: SubscriptionWorkbenchStage & { remoteId?: string };
  qb?: SubscriptionWorkbenchStage & { hashes?: string[] };
  cloud115?: SubscriptionWorkbenchStage & { ids?: string[] };
  library?: SubscriptionWorkbenchStage;
  blockingReason?: string;
  chainState?: 'active' | 'blocked' | 'completed' | 'waiting' | string;
  chainProgress?: number;
}

export type SubscriptionCapabilityState = 'ready' | 'disabled' | 'error' | 'unknown';

export interface SubscriptionWorkbenchCapability {
  key: 'local_write' | 'torra_connection' | 'torra_mirror' | 'rss' | 'scheduler';
  label: string;
  state: SubscriptionCapabilityState;
  enabled: boolean;
  configured: boolean;
  detail: string;
  checkedAt: string;
}

export interface SubscriptionWorkbenchStage {
  status: string;
  detail: string;
}

export interface SubscriptionWorkbenchResponse {
  ok: boolean;
  lastReadAt: string;
  capabilities: SubscriptionWorkbenchCapability[];
  stats: {
    total: number;
    movie: number;
    tv: number;
    pending: number;
    inLibrary: number;
  };
  items: SubscriptionItem[];
  page: {
    total: number;
    limit: number;
    offset: number;
    nextOffset: number | null;
    hasMore: boolean;
  };
  blockedTitles: string[];
  errors: string[];
  torraSync: TorraSubscriptionSyncStatus;
  rss: {
    enabled: boolean;
    sources: number;
    activeSources: number;
    errorSources: number;
    items: number;
    lastSuccessAt: string;
    matches?: number;
    matcherRan?: boolean;
    lastMatchAt?: string;
    lastMatchStatus?: string;
    lastMatchScanned?: number;
    lastMatchCreated?: number;
  };
  scheduler: {
    enabled: boolean;
    state?: SubscriptionCapabilityState;
    taskTime: string;
    lastRunAt: string;
    lastError?: string;
  };
  reconciliation?: SubscriptionReconciliationResponse;
}

export interface SubscriptionCapabilitiesResponse {
  ok: boolean;
  checkedAt: string;
  localWrite: { enabled: boolean };
  torraPush: { enabled: boolean };
  scheduler: {
    configured: boolean;
    enabled: boolean;
    started: boolean;
    running: boolean;
    lastRunAt: string;
    lastError: string;
  };
}

export type SubscriptionReconciliationState = 'linked' | 'only_fluxa' | 'only_torra' | 'conflict' | 'remote_missing';
export type SubscriptionFulfillmentState = 'pending_sync' | 'following' | 'completed' | 'paused' | 'blocked';
export type SubscriptionHealthState = 'normal' | 'waiting' | 'protected' | 'action_required' | 'evidence_insufficient';

export interface SubscriptionReconciliationItem {
  id: string;
  localId: string;
  remoteRef: string;
  title: string;
  mediaType: 'movie' | 'tv' | 'unknown';
  tmdbId: string;
  seasonNumber: number;
  reconciliationState: SubscriptionReconciliationState;
  fulfillmentState: SubscriptionFulfillmentState;
  healthState: SubscriptionHealthState;
  observedAt: string;
  freshUntil: string;
  source: string;
  reasonCode: string;
  reasonText: string;
  local: { present: boolean; readOnly: boolean; sourceLabel: string };
  torra: { present: boolean; enabled: boolean; completed: boolean; mappingStatus: string };
}

export interface SubscriptionReconciliationResponse {
  ok: boolean;
  configured: boolean;
  sourceError: string;
  observedAt: string;
  freshUntil: string;
  summary: {
    localTotal: number;
    remoteTotal: number;
    reconciliation: Record<SubscriptionReconciliationState, number>;
    fulfillment: Record<SubscriptionFulfillmentState, number>;
    health: Record<SubscriptionHealthState, number>;
  };
  items: SubscriptionReconciliationItem[];
}

export interface TorraSubscriptionSyncStatus {
  ok: boolean;
  enabled: boolean;
  linked: number;
  current: number;
  remoteMissing: number;
  errors: number;
  lastSyncedAt: string;
}

export interface TorraSubscriptionSyncSummary {
  total: number;
  new: number;
  linked: number;
  duplicates: number;
  unmapped: number;
  conflicts: number;
  importable: number;
  imported?: number;
  updated?: number;
  skipped?: number;
  remoteMissing?: number;
}

export interface TorraSubscriptionSyncPreview {
  ok: boolean;
  enabled: boolean;
  summary: TorraSubscriptionSyncSummary;
  conflictItems: Array<{ subscriptionKey: string; remoteRefs: string[]; title: string }>;
  checkedAt: string;
}

export interface TorraSubscriptionSyncResult {
  ok: boolean;
  success?: boolean;
  replayed?: boolean;
  ran?: boolean;
  summary: TorraSubscriptionSyncSummary;
  syncedAt: string;
  requestId: string;
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

export interface TorraPushPreviewResponse {
  ok: boolean;
  subscription: {
    id: string;
    title: string;
  };
  preview: SubscriptionPushPreview;
}

export interface TorraPushResult {
  ok: boolean;
  success: boolean;
  pushed: boolean;
  alreadyExists: boolean;
  searchTriggered: boolean;
  subscriptionId: string;
  message: string;
  requestId: string;
  replayed: boolean;
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
    originalTitle?: string;
    englishTitle?: string;
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

export interface QualityWatchUnit {
  id: string;
  state: string;
  seasonNumber: number | null;
  episodeNumber: number | null;
  windowHours: number;
  baselineReadyAt: string;
  nextCheckAt: string;
  observationEndsAt: string;
  attemptCount: number;
  currentOffsetIndex: number;
  lastResult: {
    reason?: string;
    actionId?: string;
    selectedCount?: number;
    offsetIndex?: number;
    window?: number;
    limit?: string;
  };
}

export interface QualityWatchResponse {
  subscriptionId: string;
  policy: {
    windowHours: 24 | 48;
    scheduleMinutes: number[];
  };
  paused: boolean;
  units: QualityWatchUnit[];
}

export interface MoviePilotPreview {
  subscriptionId: string;
  ready: boolean;
  mode: 'search-existing' | 'create-and-search' | string;
  title: string;
  mediaType: 'movie' | 'tv' | string;
  tmdbId: string;
  seasons: number[];
  blockers: string[];
}

export interface MoviePilotPushResult {
  ok: boolean;
  mode: string;
  alreadyExists: boolean;
  searchTriggered: boolean;
  message: string;
  actionId: string;
}

export interface SubscriptionAutomationSettings {
  enabled: boolean;
  environmentEnabled: boolean;
  downloadEnvironmentEnabled: boolean;
  defaultWindowHours: 24 | 48;
  scheduleMinutes: number[];
  minIntervalMinutes: number;
  hourlyLimit: number;
  dailyLimit: number;
  batchSize: number;
}
