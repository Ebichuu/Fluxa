export interface EmbyOverviewCounts {
  movies: number;
  series: number;
  episodes: number;
}

export interface EmbyRecentItem {
  id: string;
  title: string;
  type: 'Movie' | 'Series' | 'Episode';
  seriesName: string;
  dateCreated: string;
}

export interface EmbyOverview {
  configured: boolean;
  connected?: boolean;
  counts?: EmbyOverviewCounts;
  recent?: EmbyRecentItem[];
  serverUrl?: string;
  lastCheckedAt?: string;
  error?: string;
}

export type EmbyRefreshState = 'ready' | 'up_to_date' | 'cooldown' | 'service_unavailable' | 'insufficient_evidence';

export interface EmbyRefreshStatus {
  configured: boolean;
  connected: boolean;
  state: EmbyRefreshState;
  canRefresh: boolean;
  reason: string;
  latestSymediaAt: string;
  latestEmbyAt: string;
  lastTriggeredAt: string;
  cooldownUntil: string;
}

export interface EmbyRefreshResult {
  triggered: boolean;
  message: string;
  triggeredAt: string;
  cooldownUntil: string;
}
