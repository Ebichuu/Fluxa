export type TaskChainState = 'active' | 'blocked' | 'completed' | 'waiting';
export type TaskChainConfidence = 'strong' | 'fallback' | 'unlinked';
export type TaskChainStepStatus = 'done' | 'active' | 'blocked' | 'waiting' | 'unknown';
export type TaskChainEvidence = 'verified' | 'inferred' | 'missing';
export type TaskChainHealthState = 'normal' | 'waiting' | 'protected' | 'action_required' | 'evidence_insufficient';
export type TaskChainIdentityState = 'unidentified' | 'linked' | 'conflict';
export type TaskChainExecutionState = 'normal' | 'waiting' | 'protected' | 'suspected_blocked' | 'action_required' | 'confirmed_failed';
export type TaskChainUserState = 'action_required' | 'in_progress' | 'completed' | 'no_action';
export type TaskChainPrimaryActionKind = 'none' | 'reidentify' | 'resume_download' | 'pause_download' | 'retry_stage' | 'refresh_source' | 'open_qb' | 'open_torra' | 'view_subscription' | 'view_details';
export type PipelineStage = 'torra' | 'qb' | 'cloud115' | 'symedia' | 'strm' | 'emby';
export type PipelineFactState = 'unknown' | 'waiting' | 'active' | 'succeeded' | 'failed' | 'protected' | 'not_applicable';
export type PipelineScope = 'movie' | 'season' | 'episode' | 'file' | 'system-category';
export type PipelineOutcomeState = 'waiting' | 'in_progress' | 'protected' | 'action_required' | 'playable' | 'evidence_insufficient';

export interface PipelineFactUnit {
  unitKey: string;
  state: PipelineFactState;
  scope: PipelineScope;
  evidence: TaskChainEvidence;
  eventAt: string;
  observedAt: string;
  freshUntil: string;
  sourceRef: string;
  reasonCode: string;
  reasonText: string;
  plannedRetryAt: string;
  retryEligible: boolean;
}

export interface PipelineFact {
  stage: PipelineStage;
  state: PipelineFactState;
  scope: PipelineScope;
  evidence: TaskChainEvidence;
  eventAt: string;
  observedAt: string;
  freshUntil: string;
  source: string;
  sourceRef: string;
  unitKey: string;
  reasonCode: string;
  reasonText: string;
  plannedRetryAt: string;
  retryEligible: boolean;
  isStale: boolean;
  firstConfirmedPlayableAt: string;
  units: PipelineFactUnit[];
}

export interface PipelineOutcome {
  state: PipelineOutcomeState;
  stage: PipelineStage | '';
  reasonCode: string;
  reasonText: string;
  observedAt: string;
  playableAt: string;
}

export interface TorraSecuploadRun {
  runId: string;
  taskKey: string;
  targetItemId: string;
  trigger: string;
  status: string;
  message: string;
  counts: { success: number | null; failed: number | null };
  startedAt: string;
  finishedAt: string;
  createdAt: string;
}

export interface TorraSecuploadBatch {
  batchKey: string;
  taskKey: string;
  trigger: string;
  status: string;
  runCount: number;
  targetItemIds: string[];
  counts: { success: number | null; failed: number | null };
  startedAt: string;
  finishedAt: string;
}

export interface TorraSecuploadSummary {
  configured: boolean;
  connected: boolean;
  pluginKey: string;
  pluginEnabled: boolean;
  readable: boolean;
  perFileEvidence: boolean;
  activeRuns?: number;
  latestRun?: TorraSecuploadRun | null;
  latestBatch?: TorraSecuploadBatch | null;
  recentBatches?: TorraSecuploadBatch[];
  lastRunAt?: string;
  nextRunAt?: string;
  lastCheckedAt: string;
  error?: string;
}

export type SystemIssueState = 'normal' | 'recovering' | 'action_required' | 'unknown';

export interface SystemIssueCategory {
  id: string;
  label: string;
  latest: { success: number | null; failed: number | null; finishedAt: string };
  recentFailedCounts: number[];
  retryPolicyText: string;
  nextRunAt?: string;
  fileEvidenceAvailable: boolean;
  fileEvidenceCount?: number;
}

export interface SecuploadFailureFile {
  ref: string;
  batchRef: string;
  categoryId: string;
  displayName: string;
  errorCategory: string;
  errorLabel: string;
  retryCount: number | null;
  observedAt: string;
}

export interface SystemIssueSummary {
  id: string;
  state: SystemIssueState;
  stateReason: string;
  failedTotal: number | null;
  nextRunAt: string;
  observedAt?: string;
  scheduleGraceSeconds: number;
  maxScheduleHorizonSeconds?: number;
  categories: SystemIssueCategory[];
  fileEvidenceAvailable: boolean;
  evidenceLimitText?: string;
  files?: SecuploadFailureFile[];
  fileFacts?: PipelineFact[];
  manualRetry?: {
    supported: boolean;
    allowed: boolean;
    reason: string;
  };
  primaryAction?: {
    kind: 'none' | 'wait_for_retry' | 'retry_failed_queue' | string;
    label: string;
    available: boolean;
  };
}

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
  userReasonText?: string;
  technicalReasonText?: string;
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
  confirmedStageCount?: number;
  currentStep: TaskChainStep['key'];
  steps: TaskChainStep[];
  embyIndexed: boolean;
  embyEvidenceScope: 'none' | 'title' | 'episode';
  suggestion: { label: string; url: string } | null;
  qbControl: {
    total: number;
    active?: number;
    paused: number;
    canPause: boolean;
    canResume: boolean;
  };
  activeDownloadTasks?: number;
  completedDownloadTasks?: number;
  concurrentDownloadCount?: number;
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
  userReasonText?: string;
  technicalReasonText?: string;
  identityState?: TaskChainIdentityState;
  executionState?: TaskChainExecutionState;
  outcomeState?: PipelineOutcomeState;
  playableAt?: string;
  userState?: TaskChainUserState;
  resultText?: string;
  completedAt?: string;
  primaryAction?: {
    kind: TaskChainPrimaryActionKind;
    label: string;
    available: boolean;
    reason: string;
  };
  recommendedAction?: string;
  retryEligible?: boolean;
  plannedRetryAt?: string;
  stages?: TaskChainStage[];
  stageSummary?: TaskChainStageSummary[];
  origins?: string[];
  relatedRecords?: number;
  pipelineFacts?: PipelineFact[];
  pipelineOutcome?: PipelineOutcome;
}

export type TaskChainListItem = Omit<TaskChainItem, 'steps' | 'sourceIds' | 'suggestion' | 'artifactKeys' | 'stages'> & {
  steps?: TaskChainStep[];
  sourceIds?: TaskChainItem['sourceIds'];
  suggestion?: TaskChainItem['suggestion'];
  artifactKeys?: string[];
  stages?: TaskChainStage[];
  stageSummary: TaskChainStageSummary[];
};

export interface TaskProblemGroupSummary {
  actionRequiredGroups: number;
  actionRequiredResources: number;
  actionRequiredIdentityUnconfirmedResources: number;
}

export interface TaskProblemGroupMember {
  chainId: string;
  targetKey?: string;
  title: string;
  mediaType: 'movie' | 'tv' | 'unknown';
  tmdbId: string;
  seasonNumber: number;
  episodeNumber: number;
  identityState?: TaskChainIdentityState;
  reasonCode?: string;
  reasonText?: string;
  userReasonText?: string;
  resultText?: string;
  primaryAction?: TaskChainItem['primaryAction'];
}

export interface TaskProblemGroup {
  groupId: string;
  title: string;
  mediaType: 'movie' | 'tv' | 'unknown';
  tmdbId: string;
  seasonNumber: number;
  stage: PipelineStage | string;
  reasonCode: string;
  reasonText: string;
  resourceCount: number;
  identityUnconfirmedResources: number;
  episodeNumbers: number[];
  members: TaskProblemGroupMember[];
}

export interface TaskChainResponse {
  contractVersion?: number;
  generatedAt: string;
  items: TaskChainListItem[];
  problemGroups?: TaskProblemGroup[];
  problemGroupSummary?: TaskProblemGroupSummary;
  archiveSummary?: ArchiveSummary;
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
    artifactMigrations?: number;
    transientEventCleanup?: {
      migrationId: string;
      status: 'success' | 'failed';
      applied: boolean;
      alreadyApplied: boolean;
      backupCreated: boolean;
      backupId: string;
      deletedEvents: number;
      deletedByStage: Record<string, number>;
      reasonCode?: string;
      errorType?: string;
    };
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
    torra: { connected: boolean; error: string; total: number; webUrl: string; secupload115?: TorraSecuploadSummary };
    symedia: { connected: boolean; error: string; total: number; sampled: number; webUrl: string };
    emby: { connected: boolean; error: string; indexedMovies: number; indexedSeries: number; evidenceScope?: 'none' | 'title' | 'episode'; webUrl: string };
  };
  healthCounts?: Record<TaskChainHealthState, number>;
  identityCounts?: Record<TaskChainIdentityState, number>;
  executionCounts?: Record<TaskChainExecutionState, number>;
  userCounts?: Record<TaskChainUserState, number>;
  outcomeCounts?: Record<PipelineOutcomeState, number>;
  originCounts?: Record<'subscription' | 'download' | 'library', number>;
  stageCounts?: Record<string, Record<string, number>>;
  systemIssues?: SystemIssueSummary[];
}

export interface ArchiveSummary {
  date: string;
  timezone: 'Asia/Shanghai';
  archivedFiles: number;
  linkedFiles: number;
  linkedTasks: number;
  unlinkedFiles: number;
}

export interface TaskChainDetailResponse extends Omit<TaskChainResponse, 'items' | 'page'> {
  item: TaskChainItem;
}

export type TaskChainSummaryResponse = Omit<TaskChainResponse, 'items' | 'page'>;

export interface TaskChainQuery {
  healthState?: TaskChainHealthState;
  identityState?: TaskChainIdentityState;
  identityStates?: TaskChainIdentityState[];
  executionState?: TaskChainExecutionState;
  outcomeStates?: PipelineOutcomeState[];
  userState?: TaskChainUserState;
  completedDate?: string;
  archivedDate?: string;
  qbActive?: boolean;
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
