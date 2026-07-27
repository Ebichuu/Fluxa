import type { PipelineOutcomeState, TaskChainPrimaryActionKind, TaskChainUserState } from './taskChain';

export type MediaLifecycleStatus = 'following' | 'not_following' | 'linked' | 'not_linked' | 'in_progress' | 'completed' | 'protected' | 'action_required' | 'available' | 'scheduled' | 'unknown';

export interface MediaPrimaryAction {
  kind: TaskChainPrimaryActionKind;
  label: string;
  available: boolean;
  reason: string;
}

export interface MediaLinks {
  overview: string;
  tasks: string;
  calendar: string;
  subscription: string;
  api: string;
}

export interface MediaSearchItem {
  mediaKey: string;
  chainId?: string;
  title: string;
  mediaType: 'movie' | 'tv';
  tmdbId: string;
  year?: string;
  posterUrl?: string;
  sources: string[];
  outcomeState: PipelineOutcomeState;
  userState: TaskChainUserState;
  resultText: string;
  subscriptionStatus: 'following' | 'not_following' | 'unknown';
  embyStatus: 'available' | 'unknown';
  primaryAction: MediaPrimaryAction;
  links: MediaLinks;
}

export interface MediaSearchResponse {
  ok: boolean;
  query: string;
  items: MediaSearchItem[];
  page: { total: number; limit: number };
}

export interface MediaStageProjection {
  status: MediaLifecycleStatus;
  observedAt?: string;
  activeTasks?: number;
  completedTasks?: number;
  latestEpisode?: { seasonNumber: number; episodeNumber: number; label: string };
}

export interface MediaOverviewResponse {
  ok: boolean;
  media: Pick<MediaSearchItem, 'mediaKey' | 'title' | 'mediaType' | 'tmdbId' | 'year' | 'posterUrl' | 'sources'>;
  outcomeState: PipelineOutcomeState;
  userState: TaskChainUserState;
  resultText: string;
  subscription: {
    status: 'following' | 'not_following' | 'unknown';
    torraStatus: 'linked' | 'not_linked' | 'unknown';
    lastCheckedAt: string;
    seasonNumbers: number[];
  };
  download: MediaStageProjection;
  cloud115: MediaStageProjection;
  library: MediaStageProjection;
  emby: { status: 'available' | 'unknown'; evidenceScope: 'episode' | 'title' | 'none' };
  playback: { status: 'available' | 'unknown'; directLinkAvailable: boolean };
  calendar: {
    status: MediaLifecycleStatus;
    entryCount: number;
    inLibraryCount: number;
    nextAirAt?: string;
    nextEpisodeLabel?: string;
  };
  primaryAction: MediaPrimaryAction;
  links: MediaLinks;
}
