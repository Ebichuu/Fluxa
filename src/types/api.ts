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

export interface SaRecordRef {
  id: string;
  mediaKey: string;
  mediaType: MediaType;
  tmdbId: string;
  title: string;
  seasons: string;
  episodes: string;
  seasonEpisode?: string;
  src: string;
  dest: string;
  fileName: string;
  fileSize?: number | null;
  outcome: string;
  outcomeLabel: string;
  reasonText: string;
  date: string;
  webUrl: string;
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
  saRecord?: SaRecordRef | null;
  appUrl: string;
  appLabel: string;
  ignoredKind?: 'manual' | 'auto_existing_version';
  ignoredLabel?: string;
  ignoredReason?: string;
  canRestore?: boolean;
}

export interface SaExistingVersionInfo {
  exists: boolean;
  confirmedInLibrary?: boolean;
  autoIgnoreEligible?: boolean;
  noRecoveryNeeded: boolean;
  needsComparison?: boolean;
  exception?: { isException: boolean; exceptionType: string; reason: string };
  badgeLabel: string;
  shortBanner?: string;
  autoIgnoreSummary?: string;
  source: string;
  existingSourceTag: string;
  failedSourceTag: string;
  existingFileName: string;
  existingPath: string;
  summary: string;
}

export interface SaRecoveryProposal {
  mediaType: MediaType;
  tmdbId: string;
  canonicalTitle: string;
  originalTitle: string;
  year: string;
  posterUrl: string;
  sourceSeason: number | null;
  sourceEpisode: number | null;
  targetSeason: number | null;
  targetEpisode: number | null;
  seasonName: string;
  seasonAirDate: string;
  episodeName: string;
  episodeAirDate: string;
  seasonChanged: boolean;
  sequelConflict: { hasConflict: boolean; reasons: string[]; warningText: string };
  alreadyInEmby?: boolean;
  existingVersion?: SaExistingVersionInfo;
  score: number;
  confidence: 'high' | 'medium' | 'low';
  reasons: string[];
  suggestedFileName: string;
}

export interface SaIdentifyMapping {
  id: number;
  arcKey: string;
  arcTitle: string;
  sourceSeason: number;
  mediaType: MediaType;
  tmdbId: string;
  canonicalTitle: string;
  targetSeason: number;
  seasonName: string;
  episodeOffset: number;
  transferTaskId: string;
  autoApply: boolean;
  confirmedBySample: string;
  updatedAt: string;
}

export interface SaRecoveryJob {
  id: number;
  saRecordId: string;
  fileName: string;
  srcPath: string;
  failedDate: string;
  tmdbId: string;
  canonicalTitle: string;
  mediaType: MediaType;
  sourceSeason: number | null;
  sourceEpisode: number | null;
  targetSeason: number | null;
  targetEpisode: number | null;
  strategy: string;
  renamedFrom: string;
  renamedTo: string;
  status: 'draft' | 'file_prepared' | 'submitted' | 'processing' | 'not_processed' | 'skipped_existing' | 'resolved' | 'failed';
  attemptCount: number;
  maxAttempts: number;
  lastError: string;
  verification: {
    saSuccessConfirmed?: boolean;
    saSuccessRecordId?: string;
    newFailureRecordId?: string;
    movedSrcPath?: string;
    tmdbAndEpisodeMatched?: boolean;
    destVideoConfirmed?: boolean;
    destPath?: string;
    strmOrEmbyConfirmed?: boolean;
    embyEntryConfirmed?: boolean;
    existingVersion?: SaExistingVersionInfo | null;
    detail?: string;
    checkedAt?: string;
  };
  submittedAt: string;
  resolvedAt: string;
}

export interface SaDiagnoseResponse {
  record: {
    id: string;
    fileName: string;
    src: string;
    date: string;
    outcome: string;
    reasonText: string;
    fileSize?: number | null;
  };
  recognitionStatus?: 'matched' | 'unreliable';
  statusMessage?: string;
  noRecoveryNeeded?: boolean;
  existingVersion?: SaExistingVersionInfo | null;
  filteredOutCount?: number;
  parsed: {
    fileName: string;
    inferredMediaType: MediaType;
    arcTitle: string;
    cleanedTitle: string;
    arcKey: string;
    sourceSeason: number | null;
    sourceEpisode: number | null;
    year: string;
    specialMarkers: string[];
    hasSpecialMarkers: boolean;
    specsTail: string;
    searchQueries: string[];
  };
  fileCheck: { checked: boolean; exists: boolean | null; sizeBytes: number | null; detail: string };
  searchedQueries: Array<{ query: string; hitCount: number }>;
  savedMapping: SaIdentifyMapping | null;
  canAutoApply: boolean;
  requiresConfirm: boolean;
  proposals: SaRecoveryProposal[];
  saTransferTasks: Array<{ id: string; name: string; sourcePath: string; targetPath: string }>;
  saCapabilities: {
    transferManual?: boolean;
    manualEndpointPath?: string;
    manualBodyFields?: string[];
    manualItemFields?: string[];
    manualIdentifyParams?: string[];
    supportsTmdbParam?: boolean;
    supportsSeasonEpisodeParam?: boolean;
    supportsExplicitIdentify?: boolean;
    openapiChecked?: boolean;
  };
  latestJob: SaRecoveryJob | null;
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

export interface SnapshotCacheMeta {
  fromCache: boolean;
  stale: boolean;
  refreshing: boolean;
  cacheAgeSeconds: number;
  updatedAt: string;
}

export interface DashboardResponse {
  generatedAt: string;
  cache?: SnapshotCacheMeta;
  services: ServiceHealth[];
  counts: {
    todayUpdates: number;
    inEmbyToday: number;
    activeDownloads: number;
    activeUploads: number;
    issues: number;
    ignoredIssues?: number;
    subscriptions: number;
  };
  issues: ActionableIssue[];
  ignoredIssues?: ActionableIssue[];
  calendar: {
    today: string;
    startDate: string;
    endDate: string;
    todayItems: CalendarEpisodeItem[];
    days: CalendarDay[];
    totalTvSubscriptions?: number;
    checkedTvSubscriptions?: number;
    truncated?: boolean;
    omittedCount?: number;
    truncationWarning?: string;
    embyTruncated?: boolean;
    embyTruncationWarning?: string;
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

export interface QualityPolicySpec {
  id: string;
  name: string;
  shortName: string;
  summary: string;
}

export interface RssAutoRule {
  id: string;
  siteId: string;
  siteName: string;
  officialGroup: string;
  urlMasked: string;
  hasUrl: boolean;
  savePath: string;
  category: string;
  qualityPolicyId: string;
  qualityPolicyName?: string;
  enabled: boolean;
  pollIntervalMinutes: number;
  baselineInitialized: boolean;
  lastCheckedAt: string;
  lastStatus: 'idle' | 'ok' | 'warn' | 'error';
  lastMessage: string;
  addedCount: number;
}

export interface RssAutoItemLog {
  id: number;
  ruleId: string;
  torrentHash: string;
  title: string;
  status:
    | 'baseline_skipped'
    | 'duplicate_in_qb'
    | 'duplicate_seen'
    | 'confirmed_in_qb'
    | 'submitted_unconfirmed'
    | 'quality_rejected'
    | 'insufficient_evidence'
    | 'quality_outperformed'
    | 'quality_existing_sufficient'
    | 'failed';
  errorText: string;
  qbCategory: string;
  qbSavePath: string;
  workEpisodeKey?: string;
  qualityPolicyId?: string;
  qualityMeta?: {
    specs?: {
      specSummary?: string;
      scopeLabel?: string;
      resolution?: string;
      source?: string;
      codec?: string;
      hasEffectsSub?: boolean;
    };
    eval?: {
      policyName?: string;
      decision?: string;
      qualityTier?: number;
      reason?: string;
    };
  };
  createdAt: string;
}

export interface RssAutoOverviewResponse {
  rules: RssAutoRule[];
  items: RssAutoItemLog[];
  qbCategories: Array<{ name: string; savePath: string }>;
  qbConfigured: boolean;
  sites: SiteOfficialGroup[];
  qualityPolicies: QualityPolicySpec[];
}
