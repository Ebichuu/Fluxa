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
  hasDownload: boolean;
  lastSeenAt: string;
}

export interface RssSeedListResponse {
  items: RssSeedItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface RssSourceInput {
  name: string;
  feedUrl?: string;
  enabled: boolean;
  intervalMinutes: 1 | 3 | 5;
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
  itemTitle?: string;
  subscriptionTitle?: string;
  episodeLabel?: string;
  createdAt?: string;
  updatedAt?: string;
  expiresAt?: string;
  identity?: Record<string, unknown>;
}

export interface RssMatchListResponse {
  items: RssMatch[];
  total: number;
  limit: number;
  offset: number;
}
