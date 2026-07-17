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
  provider: string;
  type: string;
  status: 'running' | 'succeeded' | 'failed';
  result: { message?: string; items?: number; title?: string } | null;
}
