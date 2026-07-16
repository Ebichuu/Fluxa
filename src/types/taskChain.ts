export type TaskChainState = 'active' | 'blocked' | 'completed' | 'waiting';
export type TaskChainConfidence = 'strong' | 'fallback' | 'unlinked';
export type TaskChainStepStatus = 'done' | 'active' | 'blocked' | 'waiting' | 'unknown';
export type TaskChainEvidence = 'verified' | 'inferred' | 'missing';

export interface TaskChainStep {
  key: 'subscription' | 'download' | 'cloud115' | 'library';
  label: string;
  status: TaskChainStepStatus;
  evidence: TaskChainEvidence;
  detail: string;
  timestamp: string;
  source: string;
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
    torraId: string;
    qbHashes: string[];
    symediaIds: string[];
  };
  updatedAt: string;
}

export interface TaskChainResponse {
  generatedAt: string;
  items: TaskChainItem[];
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
}
