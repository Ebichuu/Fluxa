export interface RssSource {
  id: string;
  name: string;
  domain: string;
  feedConfigured: boolean;
  enabled: boolean;
  intervalMinutes: number;
  retentionDays: number;
  allowHttp: boolean;
  lastSuccessAt: string;
  lastError: string;
  failureCount: number;
  backoffUntil: string;
  nextPollAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface RssLibrarySummary {
  enabled: boolean;
  sources: number;
  activeSources: number;
  errorSources: number;
  items: number;
  lastSuccessAt: string;
  identityBackfillRan?: boolean;
  identityBackfillStatus?: string;
  lastIdentityBackfillAt?: string;
  lastIdentityBackfillStatus?: string;
  lastIdentityBackfillScanned?: number;
  lastIdentityBackfillIdentified?: number;
  lastIdentityBackfillConflicts?: number;
  lastIdentityBackfillUnchanged?: number;
  lastIdentityBackfillRemaining?: number;
  lastIdentityBackfillLimit?: number;
  matcherRan?: boolean;
  matcherStatus?: string;
  lastMatchAt?: string;
  lastMatchStatus?: string;
  lastMatchScanned?: number;
  lastMatchCreated?: number;
}

export interface RssSourceListResponse {
  items: RssSource[];
  summary: RssLibrarySummary;
}

export interface RssSeedItem {
  id: string;
  sourceId: string;
  sourceName: string;
  sourceDomain: string;
  title: string;
  description: string;
  publishedAt: string;
  category: string;
  sizeBytes: number;
  mediaType: 'movie' | 'tv' | '';
  seasonNumber: number | null;
  episodeStart: number | null;
  episodeEnd: number | null;
  versionSummary: string;
  tmdbId: string;
  imdbId: string;
  identityStatus: 'identified' | 'conflict' | 'unidentified';
  identitySource: string;
  identityConfidence: string;
  identityUpdatedAt: string;
  matchMethod?: 'tmdb_exact' | 'title_media_season' | 'title_media_year' | 'title_scoped' | string;
  matchConfidence?: 'strong' | 'fallback' | string;
  seasonScopeState?: 'confirmed' | 'unknown' | 'not_applicable' | string;
  hasDownload: boolean;
  lastSeenAt: string;
}

export type RssIdentityStatus = '' | RssSeedItem['identityStatus'];

// 三类互斥的资源范围口径：每条资源只允许归入唯一分类，
// 且 明确单集 + 季包 + 范围待确认 = 资源总数（由 classifyRssResourceScope 的全覆盖分支保证）。
export type RssResourceScope = 'explicit_episode' | 'season_pack' | 'scope_pending';

export interface RssResourceScopeCounts {
  total: number;
  explicitEpisode: number;
  seasonPack: number;
  scopePending: number;
}

export function classifyRssResourceScope(
  item: Pick<RssSeedItem, 'seasonNumber' | 'episodeStart' | 'episodeEnd' | 'seasonScopeState'>
): RssResourceScope {
  const seasonConfirmed = item.seasonNumber != null && item.seasonScopeState !== 'unknown';
  if (!seasonConfirmed) return 'scope_pending';
  if (item.episodeStart != null) {
    const end = item.episodeEnd ?? item.episodeStart;
    return end === item.episodeStart ? 'explicit_episode' : 'scope_pending';
  }
  return item.seasonScopeState === 'confirmed' ? 'season_pack' : 'scope_pending';
}

export function countRssResourceScopes(scopes: RssResourceScope[]): RssResourceScopeCounts {
  return {
    total: scopes.length,
    explicitEpisode: scopes.filter((scope) => scope === 'explicit_episode').length,
    seasonPack: scopes.filter((scope) => scope === 'season_pack').length,
    scopePending: scopes.filter((scope) => scope === 'scope_pending').length
  };
}

export function rssResourceScopeLabel(scope: RssResourceScope) {
  if (scope === 'explicit_episode') return '明确单集';
  if (scope === 'season_pack') return '季包';
  return '范围待确认';
}

export function rssResourceScopeSummaryText(counts: RssResourceScopeCounts) {
  return `${counts.total} 个资源 · 明确单集 ${counts.explicitEpisode} · 季包 ${counts.seasonPack} · 范围待确认 ${counts.scopePending}`;
}

export function rssMatchMethodLabel(method?: string, confidence?: string) {
  if (method === 'tmdb_exact') return 'TMDB 精确';
  if (method === 'title_media_season') return '标题与类型/季号匹配';
  if (method) return '回退匹配';
  return confidence ? '回退匹配' : '尚未建立匹配';
}

export interface RssSeedListResponse {
  items: RssSeedItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface RssIdentityBackfillResponse {
  ok: boolean;
  scanned: number;
  identified: number;
  conflicts: number;
  unchanged: number;
  remaining: number;
  limit: number;
}

export interface RssMatchRunResponse {
  ok: boolean;
  scanned: number;
  created: number;
  evaluated?: number;
  remaining: number;
  limit: number;
}

export interface RssSourceInput {
  name: string;
  feedUrl?: string;
  enabled: boolean;
  intervalMinutes: number;
  retentionDays: 3 | 7 | 14;
  allowHttp: boolean;
}

export interface AutomationAction {
  id: string;
  subscriptionId?: string;
  unitId?: string;
  provider: string;
  type: string;
  status: string;
  externalJobId?: string;
  createdAt?: string;
  updatedAt?: string;
  completedAt?: string;
  result: {
    message?: string;
    items?: number;
    title?: string;
    selectedCount?: number;
    upgradeOptions?: Array<{
      currentScore: number;
      upgradeScore: number;
      scoreGain: number;
      quality?: string;
      size?: number | string;
    }>;
    [key: string]: unknown;
  } | null;
  error?: { code?: string; message?: string } | null;
}

export interface RssMatch {
  id: string;
  itemId: string;
  subscriptionId: string;
  unitId: string;
  status: 'candidate' | 'ignored' | 'triggered' | 'confirmed' | 'expired' | string;
  reason?: Record<string, unknown>;
  triggerActionId?: string;
  torraLinked?: boolean;
  targetKey?: string;
  artifactKey?: string;
  ruleId?: string;
  ruleHash?: string;
  candidateScore?: number | null;
  baselineScore?: number | null;
  evaluationStatus?: 'pending' | 'scored' | 'blocked' | string;
  decision?: string;
  evaluationReason?: string;
  evaluationActionId?: string;
  downloadActionId?: string;
  candidateSummary?: RssScoreSummary;
  baselineSummary?: RssScoreSummary;
  bestCandidate?: boolean;
  evaluatedAt?: string;
  itemTitle?: string;
  subscriptionTitle?: string;
  episodeLabel?: string;
  createdAt?: string;
  updatedAt?: string;
  expiresAt?: string;
  identity?: Record<string, unknown>;
}

export interface RssScoreSummary {
  versionSummary?: string;
  versionName?: string;
  artifactKey?: string;
  sources?: Array<'torra' | 'qb' | 'symedia' | string>;
  scoreBreakdown?: Array<{
    field: string;
    label: string;
    score: number;
  }>;
}

export interface RssMatchGroup {
  id: string;
  subscriptionId: string;
  unitId: string;
  title: string;
  episodeLabel: string;
  state: 'waiting_baseline' | 'monitoring_rss' | 'upgrade_available' | 'protected' | 'blocked' | string;
  candidateCount: number;
  bestMatchId?: string;
  bestArtifactKey?: string;
  bestCandidateScore?: number | null;
  baselineScore?: number | null;
  baselineSummary?: RssScoreSummary;
  lastCandidateAt?: string;
  candidates: RssMatch[];
}

export interface RssMatchListResponse {
  items: RssMatch[];
  total: number;
  limit: number;
  offset: number;
}

export interface RssMatchGroupListResponse {
  groups: RssMatchGroup[];
  total: number;
  limit: number;
  offset: number;
}

export interface RssExactDownloadPreview {
  status: 'blocked' | 'ready' | string;
  ready: boolean;
  capabilityState: 'unsupported' | 'available' | string;
  matchId: string;
  targetKey?: string;
  versionSummary?: string;
  candidateScore?: number | null;
  baselineScore?: number | null;
  scoreGain?: number | null;
  blockers: Array<{
    code: string;
    message: string;
  }>;
  observedAt: string;
}

export interface CreateRssMatchInput {
  rssItemId: string;
  subscriptionId: string;
  unitId: string;
}
