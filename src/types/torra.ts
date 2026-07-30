export interface TorraSummary {
  configured: boolean;
  connected: boolean;
  webUrl: string;
  lastCheckedAt: string;
  counts: {
    total: number;
    active: number;
    completed: number;
    running: number;
  };
  searchAutomation?: {
    capabilityState: 'partial' | 'unsupported' | 'unknown';
    subscriptionModes: {
      state: 'confirmed' | 'unsupported' | 'unknown';
      counts: {
        rssPreferred: number | null;
        automaticSearch: number | null;
        unknown: number;
      };
      reasonCode: string;
      reasonText: string;
    };
    schedules: {
      state: 'confirmed' | 'unsupported' | 'unknown';
      rss: TorraSearchSchedule | null;
      automaticSearch: TorraSearchSchedule | null;
      reasonCode: string;
    };
    recentBatchState: 'confirmed' | 'unsupported' | 'unknown';
    recentBatch: TorraSearchBatch | null;
    recentBatchReasonCode: string;
    adjustmentPreview: {
      state: 'blocked' | 'ready';
      canApply: boolean;
      eligibleSubscriptions: number;
      blockedSubscriptions: number;
      reasonCode: string;
      reasonText: string;
    };
  };
  error?: string;
}

export interface TorraSearchSchedule {
  registered: boolean;
  enabled: boolean | null;
  lastRunAt: string;
  nextRunAt: string;
}

export interface TorraSearchBatch {
  mode: 'rss' | 'automatic_search' | 'unknown';
  status: 'pending' | 'queued' | 'running' | 'success' | 'failed' | 'cancelled' | 'unknown';
  trigger: 'manual' | 'scheduler' | 'unknown';
  startedAt: string;
  finishedAt: string;
  subscriptionCount: number | null;
  estimatedSiteRequests: number | null;
}
