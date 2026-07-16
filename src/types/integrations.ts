export type IntegrationId = 'cloud115' | 'telegram' | 'hdhive' | 'moviepilot';

export interface IntegrationService {
  id: IntegrationId;
  name: string;
  role: string;
  configured: boolean;
  connected: boolean | null;
  detail: string;
}

export interface IntegrationSummary {
  ok: boolean;
  services: IntegrationService[];
  managementEnabled: boolean;
  probeEnabled: boolean;
  checkedAt: string;
}

export interface TelegramChannel {
  name: string;
  input: string;
  enabled: boolean;
}

export interface CloudCandidate {
  id: string;
  source: string;
  sourceLabel: string;
  title: string;
  subtitle: string;
  quality: string;
  size: string;
  season: string;
  requiresUnlock: boolean;
}

export interface CloudCandidateResponse {
  ok: boolean;
  subscription: {
    id: string;
    title: string;
    tmdbId: string;
    mediaType: 'movie' | 'tv';
  };
  candidates: CloudCandidate[];
  errors: string[];
  expiresInSeconds: number;
}

export interface CloudTransferResult {
  ok: boolean;
  status: 'pending' | 'completed' | 'failed' | 'unknown';
  subscriptionId: string;
  candidateId: string;
  requestId: string;
  replayed: boolean;
}
