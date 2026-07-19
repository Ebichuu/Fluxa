import type { HealthResponse, HomeMediaResponse } from '../types/media';
import type { EmbyOverview, EmbyRefreshResult, EmbyRefreshStatus } from '../types/emby';
import type { QbittorrentAction, QbittorrentActionResult, QbittorrentSummary } from '../types/qbittorrent';
import type { SymediaSummary } from '../types/symedia';
import type { TorraSummary } from '../types/torra';
import type { TaskChainResponse } from '../types/taskChain';
import type { IntegrationSummary } from '../types/integrations';
import type { ActivityLogResponse, SystemMetricsResponse } from '../types/operations';
import type {
  AutomationAction,
  RssMatchListResponse,
  RssSeedListResponse,
  RssSource,
  RssSourceInput,
  RssSourceListResponse
} from '../types/rssSeedLibrary';
import type {
  DiscoverBrowseParams,
  DiscoverResourceResponse,
  DiscoverResult,
  DiscoverResponse,
  SubscriptionCalendarResponse,
  SubscriptionConfigResponse,
  SubscriptionDetailResponse,
  SubscriptionHubConfig,
  SubscriptionListResponse,
  SubscriptionPushPreview,
  TorraPushPreviewResponse,
  TorraPushResult,
  MediaCategory
} from '../types/subscriptions';
import type {
  MoviePilotPreview,
  MoviePilotPushResult,
  QualityWatchResponse,
  SubscriptionAutomationSettings
} from '../types/subscriptions';

export interface AuthSessionResponse {
  enabled: boolean;
  authenticated: boolean;
  expiresAt: string | null;
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 20_000;

function requestSignal(signal: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = window.setTimeout(() => {
    timedOut = true;
    abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    cleanup: () => {
      window.clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    }
  };
}

async function requestJson<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<T> {
  const request = requestSignal(options.signal, options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...init,
      signal: request.signal,
      headers: {
        Accept: 'application/json',
        ...(init.headers ?? {})
      }
    });

    if (response.status === 204) return undefined as T;
    const body = await response.json().catch(() => ({})) as T & { error?: string };
    if (!response.ok) throw new Error(body.error || `请求失败：${response.status}`);
    return body;
  } catch (reason) {
    if (request.timedOut()) throw new Error('请求超时，请稍后重试');
    throw reason;
  } finally {
    request.cleanup();
  }
}

async function readJson<T>(path: string, options?: RequestOptions): Promise<T> {
  return requestJson<T>(path, { headers: { Accept: 'application/json' } }, options);
}

export function getHomeMedia(libraryId?: string, options?: RequestOptions): Promise<HomeMediaResponse> {
  const query = libraryId ? `?libraryId=${encodeURIComponent(libraryId)}` : '';
  return readJson<HomeMediaResponse>(`/api/media/home${query}`, options);
}

export function getHealth(options?: RequestOptions): Promise<HealthResponse> {
  return readJson<HealthResponse>('/api/health', options);
}

export function getIntegrationSummary(probe = false, options?: RequestOptions): Promise<IntegrationSummary> {
  return readJson<IntegrationSummary>(`/api/v2/integrations${probe ? '?probe=1' : ''}`, options);
}

export function getAuthSession(): Promise<AuthSessionResponse> {
  return readJson<AuthSessionResponse>('/api/auth/session');
}

export async function logoutAuthSession(): Promise<void> {
  const response = await fetch('/auth/logout', {
    method: 'POST',
    headers: { Accept: 'text/html' }
  });
  if (!response.ok) throw new Error(`退出失败：${response.status}`);
}

export function getQbittorrentSummary(options?: RequestOptions): Promise<QbittorrentSummary> {
  return readJson<QbittorrentSummary>('/api/qbittorrent/summary', options);
}

export async function runQbittorrentAction(input: {
  action: QbittorrentAction;
  hashes: string[];
  taskId: string;
  title: string;
}): Promise<QbittorrentActionResult> {
  const response = await fetch(`/api/qbittorrent/actions/${input.action}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ hashes: input.hashes, taskId: input.taskId, title: input.title })
  });
  const body = await response.json().catch(() => ({})) as QbittorrentActionResult & { error?: string };
  if (!response.ok) {
    throw new Error(body.error || `qBittorrent 操作失败：${response.status}`);
  }
  return body;
}

export function getTorraSummary(options?: RequestOptions): Promise<TorraSummary> {
  return readJson<TorraSummary>('/api/torra/summary', options);
}

export function getSymediaSummary(options?: RequestOptions): Promise<SymediaSummary> {
  return readJson<SymediaSummary>('/api/symedia/summary', options);
}

export function getEmbyOverview(options?: RequestOptions): Promise<EmbyOverview> {
  return readJson<EmbyOverview>('/api/media/emby/overview', options);
}

export function getEmbyRefreshStatus(options?: RequestOptions): Promise<EmbyRefreshStatus> {
  return readJson<EmbyRefreshStatus>('/api/media/emby/refresh-status', options);
}

export async function triggerEmbyRefresh(): Promise<EmbyRefreshResult> {
  const response = await fetch('/api/media/emby/refresh', {
    method: 'POST',
    headers: { Accept: 'application/json' }
  });
  const body = await response.json().catch(() => ({})) as EmbyRefreshResult & { error?: string };
  if (!response.ok) {
    throw new Error(body.error || `Emby 刷新失败：${response.status}`);
  }
  return body;
}

export function getTaskChain(options?: RequestOptions): Promise<TaskChainResponse> {
  return readJson<TaskChainResponse>('/api/tasks/chain', options);
}

export function getSystemMetrics(options?: RequestOptions): Promise<SystemMetricsResponse> {
  return readJson<SystemMetricsResponse>('/api/v2/system/metrics', options);
}

export function getActivityLogs(category = '', options?: RequestOptions): Promise<ActivityLogResponse> {
  const query = new URLSearchParams({ limit: '100' });
  if (category) query.set('category', category);
  return readJson<ActivityLogResponse>(`/api/activity/logs?${query.toString()}`, options);
}

export function getSubscriptionCalendar(
  year: number,
  month: number,
  mediaType: 'all' | 'movie' | 'tv' = 'all',
  options?: RequestOptions
): Promise<SubscriptionCalendarResponse> {
  return readJson<SubscriptionCalendarResponse>(
    `/api/subscriptions/calendar?year=${year}&month=${month}&type=${mediaType}`,
    options
  );
}

export function getSubscriptionItems(includeProgress = false, options?: RequestOptions): Promise<SubscriptionListResponse> {
  return readJson<SubscriptionListResponse>(
    includeProgress ? '/api/subscriptions/items?include_progress=1' : '/api/subscriptions/items',
    options
  );
}

export function getDiscoverTrending(type: 'movie' | 'tv'): Promise<DiscoverResponse> {
  return readJson<DiscoverResponse>(`/api/discover/trending?type=${type}`);
}

export function browseDiscover(params: DiscoverBrowseParams): Promise<DiscoverResponse> {
  const query = new URLSearchParams({
    source: params.source,
    type: params.type,
    trend: params.trend,
    sort: params.sort,
    language: params.language,
    year: params.year,
    genre: params.genre,
    page: String(params.page),
    limit: String(params.limit ?? 16)
  });
  if (params.provider) {
    query.set('provider', params.provider);
  }
  return readJson<DiscoverResponse>(`/api/discover/browse?${query.toString()}`);
}

export function searchDiscover(query: string, page = 1): Promise<DiscoverResponse> {
  return readJson<DiscoverResponse>(`/api/discover/search?query=${encodeURIComponent(query)}&page=${page}`);
}

export function searchDiscoverResources(result: DiscoverResult): Promise<DiscoverResourceResponse> {
  const query = new URLSearchParams({
    title: result.title,
    type: result.mediaType
  });
  if (/^\d{4}$/.test(result.year)) query.set('year', result.year);
  if (result.tmdbId && /^\d+$/.test(result.tmdbId)) query.set('tmdb_id', result.tmdbId);
  if (result.source && /^[a-z0-9_-]{1,40}$/i.test(result.source)) query.set('source', result.source);
  return readJson<DiscoverResourceResponse>(`/api/discover/resources/search?${query.toString()}`);
}

async function postJson<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return requestJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }, options);
}

async function patchJson<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return requestJson<T>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }, options);
}

async function deleteRequest(path: string): Promise<void> {
  const response = await fetch(path, {
    method: 'DELETE',
    headers: { Accept: 'application/json' }
  });
  if (response.status === 204) return;
  const payload = await response.json().catch(() => ({})) as { error?: string };
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
}

export function getRssSources(): Promise<RssSourceListResponse> {
  return readJson<RssSourceListResponse>('/api/v2/rss-sources');
}

export function saveRssSource(input: RssSourceInput, id?: string): Promise<RssSource> {
  return id
    ? patchJson<RssSource>(`/api/v2/rss-sources/${encodeURIComponent(id)}`, input)
    : postJson<RssSource>('/api/v2/rss-sources', input);
}

export function deleteRssSource(id: string): Promise<void> {
  return deleteRequest(`/api/v2/rss-sources/${encodeURIComponent(id)}`);
}

export function testRssSource(id: string): Promise<AutomationAction> {
  return postJson<AutomationAction>(`/api/v2/rss-sources/${encodeURIComponent(id)}/tests`, {});
}

export function getRssSeedItems(input: {
  query?: string;
  sourceId?: string;
  window?: '' | '1h' | '24h' | '7d';
  limit?: number;
  offset?: number;
} = {}, options?: RequestOptions): Promise<RssSeedListResponse> {
  const query = new URLSearchParams();
  if (input.query) query.set('query', input.query);
  if (input.sourceId) query.set('sourceId', input.sourceId);
  if (input.window) query.set('window', input.window);
  query.set('limit', String(input.limit ?? 50));
  query.set('offset', String(input.offset ?? 0));
  return readJson<RssSeedListResponse>(`/api/v2/rss-items?${query.toString()}`, options);
}

export function getRssMatches(input: { status?: string; limit?: number; offset?: number } = {}, options?: RequestOptions): Promise<RssMatchListResponse> {
  const query = new URLSearchParams();
  if (input.status) query.set('status', input.status);
  query.set('limit', String(input.limit ?? 20));
  query.set('offset', String(input.offset ?? 0));
  return readJson<RssMatchListResponse>(`/api/v2/rss-matches?${query.toString()}`, options);
}

export function startRssMatchAnalysis(
  matchId: string,
  idempotencyKey: string,
  options?: RequestOptions
): Promise<AutomationAction> {
  return postJson<AutomationAction>(
    `/api/v2/rss-matches/${encodeURIComponent(matchId)}/torra-rewash-analyses`,
    { idempotencyKey },
    options
  );
}

export function saveSubscription(input: {
  title: string;
  mediaType: 'movie' | 'tv';
  tmdbId: string;
  posterUrl?: string;
  year?: string;
  originalLanguage?: string;
  genreIds?: number[];
  originCountry?: string[];
}): Promise<{ success: boolean }> {
  return postJson('/api/subscriptions/save', input);
}

export function deleteSubscription(id: string): Promise<{ success: boolean }> {
  return postJson('/api/subscriptions/delete', { id });
}

export function runSubscriptionSweep(): Promise<{ added: number; skipped: number; pushed: number; errors: string[] }> {
  return postJson('/api/subscriptions/run', {});
}

export function getSubscriptionConfig(): Promise<SubscriptionConfigResponse> {
  return readJson<SubscriptionConfigResponse>('/api/subscriptions/config');
}

export function saveSubscriptionConfig(config: SubscriptionHubConfig): Promise<SubscriptionConfigResponse> {
  return postJson('/api/subscriptions/config', config);
}

export function blockSubscription(input: { id?: string; title?: string }): Promise<{ success: boolean; blocked_titles: string[] }> {
  return postJson('/api/subscriptions/block', input);
}

export function unblockSubscription(title: string): Promise<{ success: boolean; blocked_titles: string[] }> {
  return postJson('/api/subscriptions/unblock', { title });
}

export function clearSubscriptions(): Promise<{ success: boolean }> {
  return postJson('/api/subscriptions/clear', {});
}

export function getSubscriptionDetail(id: string, season?: number, options?: RequestOptions): Promise<SubscriptionDetailResponse> {
  const query = season ? `&season=${season}` : '';
  return readJson<SubscriptionDetailResponse>(`/api/subscriptions/detail?id=${encodeURIComponent(id)}${query}`, options);
}

export function getSubscriptionQualityWatch(id: string, options?: RequestOptions): Promise<QualityWatchResponse> {
  return readJson<QualityWatchResponse>(`/api/v2/subscriptions/${encodeURIComponent(id)}/quality-watch`, options);
}

export function updateSubscriptionQualityWatch(
  id: string,
  input: { paused?: boolean; windowHours?: 24 | 48; scheduleMinutes?: number[] },
  options?: RequestOptions
): Promise<QualityWatchResponse> {
  return patchJson<QualityWatchResponse>(`/api/v2/subscriptions/${encodeURIComponent(id)}/quality-watch`, input, options);
}

export function getSubscriptionAutomationSettings(options?: RequestOptions): Promise<SubscriptionAutomationSettings> {
  return readJson<SubscriptionAutomationSettings>('/api/v2/subscription-automation/settings', options);
}

export function updateSubscriptionAutomationSettings(
  input: Partial<Pick<SubscriptionAutomationSettings, 'enabled' | 'defaultWindowHours' | 'scheduleMinutes' | 'minIntervalMinutes' | 'hourlyLimit' | 'dailyLimit' | 'batchSize'>>,
  options?: RequestOptions
): Promise<SubscriptionAutomationSettings> {
  return patchJson<SubscriptionAutomationSettings>('/api/v2/subscription-automation/settings', input, options);
}

export function getAutomationAction(id: string, options?: RequestOptions): Promise<AutomationAction> {
  return readJson<AutomationAction>(`/api/v2/automation-actions/${encodeURIComponent(id)}`, options);
}

export function startTorraRewashAnalysis(
  id: string,
  input: { idempotencyKey: string; unitId?: string },
  options?: RequestOptions
): Promise<AutomationAction> {
  return postJson<AutomationAction>(`/api/v2/subscriptions/${encodeURIComponent(id)}/torra-rewash-analyses`, input, options);
}

export function startTorraRewashDownload(
  id: string,
  input: { confirm: true; idempotencyKey: string; analysisActionId: string; unitId?: string },
  options?: RequestOptions
): Promise<AutomationAction> {
  return postJson<AutomationAction>(`/api/v2/subscriptions/${encodeURIComponent(id)}/torra-rewashes`, input, options);
}

export function getMoviePilotPreview(id: string, options?: RequestOptions): Promise<MoviePilotPreview> {
  return postJson<MoviePilotPreview>(`/api/v2/subscriptions/${encodeURIComponent(id)}/moviepilot-previews`, {}, options);
}

export function pushToMoviePilot(
  id: string,
  idempotencyKey: string,
  options?: RequestOptions
): Promise<MoviePilotPushResult> {
  return postJson<MoviePilotPushResult>(
    `/api/v2/subscriptions/${encodeURIComponent(id)}/moviepilot-pushes`,
    { confirm: true, idempotencyKey },
    options
  );
}

export function setSubscriptionSeason(id: string, seasonNumber: number, seasonName?: string): Promise<{ success: boolean }> {
  return postJson('/api/subscriptions/season', { id, seasonNumber, seasonName });
}

export function setSubscriptionCategory(
  id: string,
  category: MediaCategory | null
): Promise<{ success: boolean }> {
  return patchJson(`/api/subscriptions/${encodeURIComponent(id)}/category`, { category });
}

export function getSubscriptionPushPreview(id: string): Promise<{ success: boolean; preview: SubscriptionPushPreview }> {
  return readJson<{ success: boolean; preview: SubscriptionPushPreview }>(
    `/api/subscriptions/push-preview?id=${encodeURIComponent(id)}`
  );
}

export function getTorraPushPreview(id: string): Promise<TorraPushPreviewResponse> {
  return readJson<TorraPushPreviewResponse>(
    `/api/v2/subscriptions/${encodeURIComponent(id)}/torra-push-preview`
  );
}

export function pushSubscriptionToTorra(
  id: string,
  idempotencyKey: string
): Promise<TorraPushResult> {
  return postJson<TorraPushResult>(
    `/api/v2/subscriptions/${encodeURIComponent(id)}/torra-pushes`,
    { confirm: true, idempotencyKey }
  );
}
