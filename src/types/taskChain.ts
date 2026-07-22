export type TaskChainState = 'active' | 'blocked' | 'completed' | 'waiting';
export type TaskChainConfidence = 'strong' | 'fallback' | 'unlinked';
export type TaskChainStepStatus = 'done' | 'active' | 'blocked' | 'waiting' | 'unknown';
export type TaskChainEvidence = 'verified' | 'inferred' | 'missing';
export type TaskChainHealthState = 'normal' | 'waiting' | 'protected' | 'action_required' | 'evidence_insufficient';

export interface TaskChainStep {
  key: 'subscription' | 'download' | 'cloud115' | 'library';
  label: string;
  status: TaskChainStepStatus;
  evidence: TaskChainEvidence;
  detail: string;
  timestamp: string;
  source: string;
}

export interface TaskChainStage {
  stage: string;
  label: string;
  status: TaskChainStepStatus | string;
  healthState: TaskChainHealthState | string;
  evidence: TaskChainEvidence | string;
  observedAt: string;
  freshUntil: string;
  source: string;
  reasonCode: string;
  reasonText: string;
  recommendedAction: string;
  retryEligible: boolean;
  plannedRetryAt: string;
  actions: { preview: boolean; retry: boolean };
}

export interface TaskChainStageSummary {
  stage: string;
  label: string;
  status: string;
  healthState: TaskChainHealthState | string;
}

export interface TaskChainItem {
  id: string;
  title: string;
  mediaType: 'movie' | 'tv' | 'unknown';
  tmdbId: string;
  seasonNumber: number;
  posterUrl: string;
  origin: 'subscription' | 'download' | 'library';
  channel: 'PT';
  state: TaskChainState;
  confidence: TaskChainConfidence;
  progress: number;
  currentStep: TaskChainStep['key'];
  steps: TaskChainStep[];
  embyIndexed: boolean;
  suggestion: { label: string; url: string } | null;
  qbControl: {
    total: number;
    paused: number;
    canPause: boolean;
    canResume: boolean;
  };
  sourceIds: {
    subscriptionId: string;
    subscriptionIds?: string[];
    torraId: string;
    torraIds?: string[];
    qbHashes: string[];
    symediaIds: string[];
  };
  acquisition?: {
    primary: 'pt';
    cloudState: 'disabled' | 'subscription_disabled' | 'manual_only' | 'pt_waiting' | 'cloud_allowed' | 'blocked_by_pt' | 'completed';
    cloudDetail: string;
    cloudEnabled: boolean;
    subscriptionCloudEnabled: boolean;
    autoFallbackEnabled: boolean;
    manualActionsEnabled: boolean;
  };
  updatedAt: string;
  chainId?: string;
  mediaKey?: string;
  targetKey?: string;
  artifactKeys?: string[];
  subscriptionId?: string;
  healthState?: TaskChainHealthState;
  observedAt?: string;
  freshUntil?: string;
  source?: string;
  reasonCode?: string;
  reasonText?: string;
  recommendedAction?: string;
  retryEligible?: boolean;
  plannedRetryAt?: string;
  stages?: TaskChainStage[];
  stageSummary?: TaskChainStageSummary[];
  origins?: string[];
  relatedRecords?: number;
}

export type TaskChainListItem = Omit<TaskChainItem, 'steps' | 'sourceIds' | 'suggestion' | 'artifactKeys' | 'stages'> & {
  steps?: TaskChainStep[];
  sourceIds?: TaskChainItem['sourceIds'];
  suggestion?: TaskChainItem['suggestion'];
  artifactKeys?: string[];
  stages?: TaskChainStage[];
  stageSummary: TaskChainStageSummary[];
};

export interface TaskChainResponse {
  contractVersion?: number;
  generatedAt: string;
  items: TaskChainListItem[];
  version?: string;
  page?: {
    total: number;
    offset: number;
    limit: number;
    nextOffset: number | null;
    hasMore: boolean;
  };
  ledger?: {
    persisted: boolean;
    chains: number;
    artifacts: number;
    events: number;
    artifactConflicts: number;
    observedAt: string;
  };
  counts: {
    total: number;
    active: number;
    blocked: number;
    completed: number;
    waiting: number;
    unlinked: number;
  };
  services: {
    qb: { connected: boolean; error: string; total: number; active: number; downloadSpeed: number; webUrl: string };
    torra: { connected: boolean; error: string; total: number; webUrl: string };
    symedia: { connected: boolean; error: string; total: number; sampled: number; webUrl: string };
    emby: { connected: boolean; error: string; indexedMovies: number; indexedSeries: number; webUrl: string };
  };
  healthCounts?: Record<TaskChainHealthState, number>;
  originCounts?: Record<'subscription' | 'download' | 'library', number>;
  stageCounts?: Record<string, Record<string, number>>;
}

export interface TaskChainDetailResponse extends Omit<TaskChainResponse, 'items' | 'page'> {
  item: TaskChainItem;
}

export type TaskChainSummaryResponse = Omit<TaskChainResponse, 'items' | 'page'>;

export interface TaskChainQuery {
  healthState?: TaskChainHealthState;
  chainId?: string;
  targetKey?: string;
  subscriptionId?: string;
  tmdbId?: string;
  title?: string;
  seasonNumber?: number;
  updatedAfter?: string;
  offset?: number;
  limit?: number;
  refresh?: boolean;
}
