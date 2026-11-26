import type {
  ActionImpactPreview,
  AppSettings,
  AuditRecord,
  AuthSession,
  DashboardResponse,
  DecisionLog,
  DiscoverItem,
  IntentMode,
  MediaPipelineResponse,
  MediaType,
  ReleaseGroupCheck,
  RssAutoOverviewResponse,
  RssAutoRule,
  RssPreviewItem,
  RssSource,
  SaDiagnoseResponse,
  SaIdentifyMapping,
  SaRecordRef,
  SaRecoveryJob,
  UnifiedSubscription,
} from '../types/api';

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = (data && (data.error || data.message)) || `请求失败 (${resp.status})`;
    const err = new Error(msg) as Error & { status?: number; body?: unknown };
    err.status = resp.status;
    err.body = data;
    throw err;
  }
  return data as T;
}

export const api = {
  getSession: () => requestJson<AuthSession>('/api/auth/session'),
  setupAdmin: (username: string, password: string) =>
    requestJson<{ ok: boolean; username: string }>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  login: (username: string, password: string) =>
    requestJson<{ ok: boolean; username: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => requestJson<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),

  getDashboard: (days = 7, startDate = '', refresh = false) => {
    const qs = new URLSearchParams({ days: String(days) });
    if (startDate) qs.set('startDate', startDate);
    if (refresh) qs.set('refresh', '1');
    return requestJson<DashboardResponse>(`/api/dashboard?${qs.toString()}`);
  },

  getMediaPipeline: (mediaType: MediaType, tmdbId: string) =>
    requestJson<MediaPipelineResponse>(`/api/media/${mediaType}/${tmdbId}`),

  updateMediaPreference: (
    mediaType: MediaType,
    tmdbId: string,
    patch: { intent?: IntentMode; notes?: string; preferredTarget?: string }
  ) =>
    requestJson<{ ok: boolean }>(`/api/media/${mediaType}/${tmdbId}/preference`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  muteIssue: (issueKey: string) =>
    requestJson<{ ok: boolean }>('/api/issues/mute', {
      method: 'POST',
      body: JSON.stringify({ issueKey }),
    }),

  search: (q: string) => requestJson<{ items: DiscoverItem[] }>(`/api/search?q=${encodeURIComponent(q)}`),

  getSubscriptions: (refresh = false) =>
    requestJson<{
      generatedAt: string;
      items: UnifiedSubscription[];
      decisionLogs: DecisionLog[];
      guardrails: AppSettings['guardrails'];
    }>(refresh ? '/api/subscriptions?refresh=1' : '/api/subscriptions'),

  checkReleaseGroup: (payload: { siteId?: string; siteName?: string; title: string; hasOfficialTag?: boolean }) =>
    requestJson<ReleaseGroupCheck>('/api/guardrails/check-release', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getRssPreview: () =>
    requestJson<{ items: RssPreviewItem[]; errors: string[]; sourceCount: number }>('/api/rss/preview'),
  getRssSources: () => requestJson<{ items: RssSource[] }>('/api/rss/sources'),
  saveRssSource: (source: Partial<RssSource> & { name: string; url: string }) =>
    requestJson<{ ok: boolean; item: RssSource }>('/api/rss/sources', {
      method: 'POST',
      body: JSON.stringify(source),
    }),
  deleteRssSource: (id: string) =>
    requestJson<{ ok: boolean }>(`/api/rss/sources/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  discover: (category: string) =>
    requestJson<{ configured: boolean; category: string; message?: string; items: DiscoverItem[] }>(
      `/api/discover?category=${encodeURIComponent(category)}`
    ),

  subscribe: (payload: {
    mediaType: MediaType;
    tmdbId: string;
    title: string;
    year?: string;
    seasonNumber?: number | null;
    intent: IntentMode;
    target: 'moviepilot' | 'torra';
    sourceLabel?: string;
    fromChart?: boolean;
    overrideGuardrail?: boolean;
  }) =>
    requestJson<{ ok: boolean; message: string; decisionLog?: DecisionLog }>('/api/subscribe', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  previewAction: (actionType: string, payload: Record<string, unknown>) =>
    requestJson<ActionImpactPreview>('/api/actions/preview', {
      method: 'POST',
      body: JSON.stringify({ actionType, payload }),
    }),

  executeAction: (actionType: string, payload: Record<string, unknown>, confirmed = false) =>
    requestJson<{ ok: boolean; message?: string; requiresConfirm?: boolean; preview?: ActionImpactPreview }>(
      '/api/actions/execute',
      {
        method: 'POST',
        body: JSON.stringify({ actionType, payload, confirmed }),
      }
    ),

  getAudit: () => requestJson<{ items: AuditRecord[] }>('/api/audit'),
  getSettings: () => requestJson<AppSettings>('/api/settings'),
  saveSettings: (patch: Partial<AppSettings>) =>
    requestJson<{ ok: boolean; settings: AppSettings }>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  testService: (service: string) =>
    requestJson<{ ok: boolean; message: string }>(`/api/settings/test/${encodeURIComponent(service)}`, {
      method: 'POST',
    }),

  diagnoseSaFailure: (record: SaRecordRef, manualTmdbId = '', manualMediaType = '') =>
    requestJson<SaDiagnoseResponse>('/api/sa-recovery/diagnose', {
      method: 'POST',
      body: JSON.stringify({ record, manualTmdbId, manualMediaType }),
    }),
  executeSaRecovery: (payload: Record<string, unknown>) =>
    requestJson<{ ok: boolean; message: string; job: SaRecoveryJob; mapping?: SaIdentifyMapping | null }>(
      '/api/sa-recovery/execute',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    ),
  verifySaRecovery: (jobId: number) =>
    requestJson<{ ok: boolean; resolved: boolean; status: string; message: string; job: SaRecoveryJob }>(
      `/api/sa-recovery/jobs/${jobId}/verify`,
      { method: 'POST' }
    ),
  getSaMappings: () =>
    requestJson<{ mappings: SaIdentifyMapping[]; jobs: SaRecoveryJob[] }>('/api/sa-recovery/mappings'),
  deleteSaMapping: (mappingId: number) =>
    requestJson<{ ok: boolean }>(`/api/sa-recovery/mappings/${mappingId}`, { method: 'DELETE' }),

  getRssAutoOverview: () => requestJson<RssAutoOverviewResponse>('/api/rss-auto/rules'),
  saveRssAutoRule: (payload: {
    id?: string;
    siteId: string;
    siteName?: string;
    officialGroup: string;
    url: string;
    savePath: string;
    category: string;
    qualityPolicyId: string;
    enabled: boolean;
    pollIntervalMinutes: number;
  }) =>
    requestJson<{ ok: boolean; rule: RssAutoRule; message: string }>('/api/rss-auto/rules', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  toggleRssAutoRule: (ruleId: string, enabled: boolean, qualityPolicyId?: string) =>
    requestJson<{ ok: boolean; rule: RssAutoRule }>(`/api/rss-auto/rules/${encodeURIComponent(ruleId)}/toggle`, {
      method: 'PATCH',
      body: JSON.stringify(qualityPolicyId ? { enabled, qualityPolicyId } : { enabled }),
    }),
  runRssAutoRuleNow: (ruleId: string) =>
    requestJson<{ ok: boolean; message: string; rule: RssAutoRule }>(
      `/api/rss-auto/rules/${encodeURIComponent(ruleId)}/run`,
      { method: 'POST' }
    ),
  deleteRssAutoRule: (ruleId: string) =>
    requestJson<{ ok: boolean }>(`/api/rss-auto/rules/${encodeURIComponent(ruleId)}`, { method: 'DELETE' }),
};
