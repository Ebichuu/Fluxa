import type { HealthResponse, HomeMediaResponse } from '../types/media';
import type { EmbyOverview, EmbyRefreshResult, EmbyRefreshStatus } from '../types/emby';
import type { QbittorrentAction, QbittorrentActionResult, QbittorrentSummary } from '../types/qbittorrent';
import type { SymediaSummary } from '../types/symedia';
import type { TorraSummary } from '../types/torra';
import type { TaskChainResponse } from '../types/taskChain';
import type { CloudCandidateResponse, CloudTransferResult, IntegrationSummary, TelegramChannel } from '../types/integrations';
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
  MediaCategory
} from '../types/subscriptions';

export interface AuthSessionResponse {
  enabled: boolean;
  authenticated: boolean;
  expiresAt: string | null;
}

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: {
      Accept: 'application/json'
    }
  });

  const body = await response.json().catch(() => ({})) as T & { error?: string };
  if (!response.ok) throw new Error(body.error || `请求失败：${response.status}`);
  return body;
}

export function getHomeMedia(libraryId?: string): Promise<HomeMediaResponse> {
  const query = libraryId ? `?libraryId=${encodeURIComponent(libraryId)}` : '';
  return readJson<HomeMediaResponse>(`/api/media/home${query}`);
}

export function getHealth(): Promise<HealthResponse> {
  return readJson<HealthResponse>('/api/health');
}

export function getIntegrationSummary(probe = false): Promise<IntegrationSummary> {
  return readJson<IntegrationSummary>(`/api/v2/integrations${probe ? '?probe=1' : ''}`);
}

export function getCloudCandidates(subscriptionId: string): Promise<CloudCandidateResponse> {
  return readJson<CloudCandidateResponse>(
    `/api/v2/acquisition/cloud/candidates?id=${encodeURIComponent(subscriptionId)}`
  );
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

export function getQbittorrentSummary(): Promise<QbittorrentSummary> {
  return readJson<QbittorrentSummary>('/api/qbittorrent/summary');
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

export function getTorraSummary(): Promise<TorraSummary> {
  return readJson<TorraSummary>('/api/torra/summary');
}

export function getSymediaSummary(): Promise<SymediaSummary> {
  return readJson<SymediaSummary>('/api/symedia/summary');
}

export function getEmbyOverview(): Promise<EmbyOverview> {
  return readJson<EmbyOverview>('/api/media/emby/overview');
}

export function getEmbyRefreshStatus(): Promise<EmbyRefreshStatus> {
  return readJson<EmbyRefreshStatus>('/api/media/emby/refresh-status');
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

export function getTaskChain(): Promise<TaskChainResponse> {
  return readJson<TaskChainResponse>('/api/tasks/chain');
}

export function getSubscriptionCalendar(
  year: number,
  month: number,
  mediaType: 'all' | 'movie' | 'tv' = 'all'
): Promise<SubscriptionCalendarResponse> {
  return readJson<SubscriptionCalendarResponse>(
    `/api/subscriptions/calendar?year=${year}&month=${month}&type=${mediaType}`
  );
}

export function getSubscriptionItems(includeProgress = false): Promise<SubscriptionListResponse> {
  return readJson<SubscriptionListResponse>(
    includeProgress ? '/api/subscriptions/items?include_progress=1' : '/api/subscriptions/items'
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

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  });

  const payload = await response.json().catch(() => ({})) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PATCH',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  });

  const payload = await response.json().catch(() => ({})) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({})) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: 'DELETE', headers: { Accept: 'application/json' } });
  const payload = await response.json().catch(() => ({})) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

export function probeCloud115(): Promise<{ ok: boolean; service: IntegrationSummary['services'][number] }> {
  return postJson('/api/v2/integrations/cloud115/probes', {});
}

export function getTelegramChannels(): Promise<{ ok: boolean; channels: TelegramChannel[] }> {
  return readJson('/api/v2/integrations/telegram/channels');
}

export function sendTelegramLoginCode(input: { phone: string; api_id: string; api_hash: string }): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/v2/integrations/telegram/login-codes', input);
}

export function signInTelegram(code: string): Promise<{ ok: boolean; authorized: boolean }> {
  return postJson('/api/v2/integrations/telegram/session', { code });
}

export function logoutTelegram(): Promise<{ ok: boolean; authorized: boolean }> {
  return deleteJson('/api/v2/integrations/telegram/session');
}

export function saveTelegramChannels(channels: Array<{ input: string }>): Promise<{ ok: boolean; channelCount: number }> {
  return putJson('/api/v2/integrations/telegram/channels', { channels, resolve: true });
}

export function getHdhiveAuthorization(): Promise<{ ok: boolean; authorizationUrl: string }> {
  return readJson('/api/v2/integrations/hdhive/authorization');
}

export function runHdhiveCheckin(): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/v2/integrations/hdhive/check-ins', {});
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

export function getSubscriptionDetail(id: string, season?: number): Promise<SubscriptionDetailResponse> {
  const query = season ? `&season=${season}` : '';
  return readJson<SubscriptionDetailResponse>(`/api/subscriptions/detail?id=${encodeURIComponent(id)}${query}`);
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

export function setSubscriptionCloudPolicy(
  id: string,
  allowCloudFallback: boolean
): Promise<{ success: boolean; item: import('../types/subscriptions').SubscriptionItem }> {
  return patchJson(`/api/v2/subscriptions/${encodeURIComponent(id)}/cloud-policy`, { allowCloudFallback });
}

export function runCloudTransfer(input: {
  candidateId: string;
  idempotencyKey: string;
}): Promise<CloudTransferResult> {
  return postJson('/api/v2/acquisition/cloud/transfers', { ...input, confirm: true });
}

export function getSubscriptionPushPreview(id: string): Promise<{ success: boolean; preview: SubscriptionPushPreview }> {
  return readJson<{ success: boolean; preview: SubscriptionPushPreview }>(
    `/api/subscriptions/push-preview?id=${encodeURIComponent(id)}`
  );
}
