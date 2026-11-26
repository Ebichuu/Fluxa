export type MediaType = 'movie' | 'tv';
export type IntentMode = 'follow' | 'collect';
export type StepStatus = 'ok' | 'active' | 'warn' | 'error' | 'protected' | 'unconfirmed' | 'idle';

export interface AuthSession {
  initialized: boolean;
  authenticated: boolean;
  username: string;
}

export interface ServiceHealth {
  service: string;
  name: string;
  configured: boolean;
  connected: boolean;
  status: 'ok' | 'warn' | 'error' | 'unconfigured';
  message: string;
  count?: number;
  webUrl: string;
}

export interface RetryActionSpec {
  actionType: string;
  label: string;
  payload: Record<string, unknown>;
}

export interface ActionableIssue {
  issueKey: string;
  severity: 'error' | 'warn';
  stage: string;
  stageLabel: string;
  title: string;
  subtitle?: string;
  mediaKey: string;
  episodeLabel: string;
  errorText: string;
  retryAction: RetryActionSpec | null;
  appUrl: string;
  appLabel: string;
}

export interface QbTask {
  hash: string;
  name: string;
  progress: number;
  progressPercent: number;
  status: string;
  statusLabel: string;
  rawState: string;
  dlspeed: number;
  upspeed: number;
  eta: number;
  size: number;
  category: string;
  tags: string;
  savePath: string;
  isSeeding: boolean;
  seedingProtected: boolean;
  webUrl: string;
}

export interface EmbyRecentItem {
  mediaKey: string;
  mediaType: MediaType;
  tmdbId: string;
  title: string;
  episodeLabel: string;
  isStrm: boolean;
  dateCreated: string;
  webUrl: string;
}

export interface CalendarEpisodeItem {
  date: string;
  mediaKey: string;
  mediaType: MediaType;
  tmdbId: string;
  title: string;
  posterUrl: string;
  episodeLabel: string;
  episodeName: string;
  state: 'upcoming' | 'processing' | 'in_emby' | 'issue' | 'unconfirmed';
  stateLabel: string;
  intent: IntentMode;
}

export interface CalendarDay {
  date: string;
  isToday: boolean;
  items: CalendarEpisodeItem[];
}

export interface DashboardResponse {
  generatedAt: string;
  services: ServiceHealth[];
  counts: {
    todayUpdates: number;
    inEmbyToday: number;
    activeDownloads: number;
    activeUploads: number;
    issues: number;
    subscriptions: number;
  };
  issues: ActionableIssue[];
  calendar: {
    today: string;
    startDate: string;
    endDate: string;
    todayItems: CalendarEpisodeItem[];
    days: CalendarDay[];
  };
  activeDownloads: QbTask[];
  activeUploads: Array<{
    runId: string;
    taskKey: string;
    status: string;
    message: string;
    startedAt: string;
  }>;
  recentEmby: EmbyRecentItem[];
}

export interface DecisionLog {
  id: number;
  mediaKey: string;
  title: string;
  decisionType: string;
  outcome: string;
  sourceLabel: string;
  ruleName: string;
  reasonText: string;
  createdAt: string;
}

export interface MediaPipelineStep {
  step: 'subscription' | 'qbittorrent' | 'cloud115' | 'symedia' | 'emby';
  label: string;
  status: StepStatus;
  statusLabel: string;
  summary: string;
  webUrl?: string;
  intent?: IntentMode;
  duplicateProviders?: boolean;
  sources?: Array<{
    source: string;
    sourceLabel: string;
    subscriptionId: string;
    seasonNumber: number | null;
    ruleSummary: string;
    originReason: string;
    webUrl: string;
  }>;
  recentSelections?: Array<{
    torrentName: string;
    torrentSite: string;
    seasons: string;
    episodes: string;
    date: string;
  }>;
  decisionLogs?: DecisionLog[];
  tasks?: QbTask[];
  failedFiles?: Array<{ fileName: string; error: string; taskKey?: string; targetItemId?: string; observedAt?: string }>;
  successFiles?: Array<{ fileName: string; destPath?: string; observedAt?: string }>;
  records?: Array<{
    id: string;
    title: string;
    fileName: string;
    seasons: string;
    episodes: string;
    outcome: string;
    outcomeLabel: string;
    reasonText: string;
    date: string;
    webUrl: string;
  }>;
  episodeCount?: number;
}

export interface MediaPipelineResponse {
  generatedAt: string;
  media: {
    mediaKey: string;
    mediaType: MediaType;
    tmdbId: string;
    title: string;
    originalTitle: string;
    year: string;
    posterUrl: string;
    overview: string;
    intent: IntentMode;
    notes: string;
  };
  steps: MediaPipelineStep[];
  episodes: Array<{
    seasonNumber: number;
    episodeNumber: number;
    episodeLabel: string;
    name: string;
    airDate: string;
    state: string;
    stateLabel: string;
  }>;
}

export interface UnifiedSubscription {
  mediaKey: string;
  groupKey: string;
  mediaType: MediaType;
  tmdbId: string;
  title: string;
  year: string;
  posterUrl: string;
  intent: IntentMode;
  notes: string;
  seasons: number[];
  duplicateProviders: boolean;
  ruleSummary: string;
  originReason: string;
  updatedAt: string;
  sources: Array<{
    source: string;
    sourceLabel: string;
    subscriptionId: string;
    seasonNumber: number | null;
    ruleSummary: string;
    originReason: string;
    webUrl: string;
  }>;
}

export interface GuardrailResult {
  category: string;
  categoryLabel: string;
  allowed: boolean;
  recommendedSeason: number | null;
  recommendedIntent: IntentMode;
  ruleName: string;
  reasonText: string;
}

export interface DiscoverItem {
  mediaKey: string;
  mediaType: MediaType;
  tmdbId: string;
  title: string;
  originalTitle?: string;
  releaseDate?: string;
  year: string;
  rating?: number;
  overview?: string;
  posterUrl: string;
  guardrail?: GuardrailResult;
}

export interface ReleaseGroupCheck {
  releaseGroup: string;
  sourceSite: string;
  sitePriority?: number;
  matchMode?: 'group_list' | 'official_tag' | 'unmapped';
  versionTags: string[];
  classification: 'site_official' | 'cross_site_repost' | 'non_official' | 'site_unmapped' | 'unknown_group';
  classificationLabel: string;
  ownerSite: string;
  allowed: boolean;
  ruleName: string;
  reasonText: string;
}

export interface SiteOfficialGroup {
  priority: number;
  siteId: string;
  siteName: string;
  matchMode: 'group_list' | 'official_tag';
  domains: string[];
  officialGroups: string[];
}

export interface AppSettings {
  services: {
    moviepilot: { baseUrl: string; webUrl: string; apiToken: string; _hasSecrets?: Record<string, boolean> };
    torra: { baseUrl: string; webUrl: string; token: string; username: string; password: string; _hasSecrets?: Record<string, boolean> };
    qbittorrent: { baseUrl: string; webUrl: string; username: string; password: string; protectSeeding: boolean; _hasSecrets?: Record<string, boolean> };
    symedia: { baseUrl: string; webUrl: string; token: string; username: string; password: string; _hasSecrets?: Record<string, boolean> };
    emby: { baseUrl: string; webUrl: string; apiKey: string; userId: string; username: string; password: string; _hasSecrets?: Record<string, boolean> };
    tmdb: { apiKey: string; bearerToken: string; _hasSecrets?: Record<string, boolean> };
    telegram: { enabled: boolean; botToken: string; chatId: string; fluxaPublicUrl: string; notifyOnFailure: boolean; _hasSecrets?: Record<string, boolean> };
  };
  guardrails: {
    newReleaseMaxAgeDays: number;
    newSeasonMaxAgeDays: number;
    blockOldMoviesFromCharts: boolean;
    blockFinishedOldSeriesFromCharts: boolean;
    onlySubscribeLatestSeasonForReturning: boolean;
    requireSiteOfficialGroup: boolean;
    blockCrossSiteRepost: boolean;
    noFallbackToNonOfficial: boolean;
    defaultSubscribeTarget: 'moviepilot' | 'torra';
    defaultIntent: IntentMode;
    siteOfficialGroups: SiteOfficialGroup[];
  };
  safety: {
    requireConfirmForSeedingActions: boolean;
    requireConfirmForRewashOrSwitch: boolean;
    requireConfirmForDelete: boolean;
  };
}

export interface ActionImpactPreview {
  actionType: string;
  allowed: boolean;
  requiresConfirm: boolean;
  riskLevel: 'low' | 'medium' | 'high';
  title: string;
  impactSummary: string;
  affectedItems: Array<{ label: string; value: string }>;
}

export interface RssSource {
  id: string;
  name: string;
  siteId: string;
  url: string;
  enabled: boolean;
}

export interface RssPreviewItem {
  title: string;
  sourceName: string;
  siteName: string;
  releaseGroup: string;
  versionTags: string[];
  tmdbId: string;
  seasonNumber: number | null;
  episodeNumber: number | null;
  sizeBytes: number;
  detailUrl: string;
  publishedAt: string;
  groupCheck: ReleaseGroupCheck;
}

export interface AuditRecord {
  id: number;
  actionType: string;
  targetService: string;
  targetId: string;
  mediaKey: string;
  summary: string;
  status: string;
  errorText: string;
  createdAt: string;
}
