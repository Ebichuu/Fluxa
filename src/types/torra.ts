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
  error?: string;
}
