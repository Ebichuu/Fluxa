export type QbittorrentTaskStatus = 'downloading' | 'stalled' | 'completed' | 'paused' | 'queued';

export interface QbittorrentTask {
  hash: string;
  name: string;
  progress: number;
  state: string;
  stateLabel: string;
  status: QbittorrentTaskStatus;
  dlspeed: number;
  upspeed: number;
  eta: number;
  size: number;
  downloaded: number;
  savePath: string;
  category: string;
  tags: string;
  addedOn: number;
  completionOn: number;
}

export interface QbittorrentSummary {
  configured: boolean;
  connected: boolean;
  webUrl: string;
  lastCheckedAt: string;
  version: string;
  transfer: {
    downloadSpeed: number;
    uploadSpeed: number;
  };
  counts: {
    total: number;
    active: number;
    downloading: number;
    stalled: number;
    completed: number;
    paused: number;
  };
  tasks: QbittorrentTask[];
  error?: string;
}

export type QbittorrentAction = 'pause' | 'resume';

export interface QbittorrentActionResult {
  action: QbittorrentAction;
  requested: number;
  submitted: number;
  succeeded: number;
  failed: number;
  skipped: number;
  confirmed: boolean;
  tasks: Array<{
    hash: string;
    status: string;
    state: string;
    outcome: 'success' | 'failed' | 'skipped';
  }>;
}
