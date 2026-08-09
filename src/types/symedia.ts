export interface SymediaTransferItem {
  title: string;
  year: string;
  mediaType: string;
  seasonEpisode: string;
  mode: string;
  status: boolean | null;
  outcome?: 'replaced' | 'archived' | 'protected' | 'failed' | 'evidence_insufficient';
  errmsg: string;
  date: string;
}

export type SymediaCapabilityState = 'available' | 'unavailable' | 'unknown';

export interface SymediaCapabilityEvidence {
  state: SymediaCapabilityState;
  reasonCode: string;
  observedAt: string;
}

export interface SymediaCapabilities {
  transferHistory: SymediaCapabilityEvidence;
  archiveMonitor: SymediaCapabilityEvidence;
  cloudDriveListener: SymediaCapabilityEvidence;
  webhook: SymediaCapabilityEvidence;
  strmGenerator: SymediaCapabilityEvidence;
  archiveScheduler: SymediaCapabilityEvidence;
  fileObserver: SymediaCapabilityEvidence;
}

export interface SymediaWashSummary {
  scope: 'today';
  evidenceState: 'verified' | 'partial' | 'insufficient';
  successfulReplacements: number | null;
  lowScoreProtected: number | null;
  versionRuleProtected: number | null;
  cancelledOverrides: number | null;
  realFailures: number | null;
  latestTarget: {
    title: string;
    seasonEpisode: string;
    mediaType: string;
    date: string;
    outcome: 'replaced' | 'archived' | 'protected' | 'failed' | 'evidence_insufficient';
  } | null;
}

export interface SymediaSummary {
  configured: boolean;
  connected: boolean;
  webUrl: string;
  lastCheckedAt: string;
  totals: {
    records: number;
    today: number;
    processedToday?: number;
    archivedToday?: number;
    protectedToday?: number;
    failedToday?: number;
    unknownToday?: number;
    failedRecent: number;
    protectedRecent?: number;
  };
  capabilities?: SymediaCapabilities;
  washSummary?: SymediaWashSummary;
  latest: SymediaTransferItem[];
  error?: string;
}
