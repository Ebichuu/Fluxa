export interface SystemMetricsResponse {
  ok: boolean;
  cached: boolean;
  checkedAt: string;
  cpu: { percent: number };
  memory: { total: number; used: number; available: number; percent: number };
  disk: { total: number; used: number; free: number; percent: number };
  network: { downBps: number; upBps: number; received: number; sent: number };
}

export interface ActivityLogItem {
  time: string;
  ts: number;
  category: string;
  action: string;
  status: 'start' | 'success' | 'error' | 'skip' | 'info' | string;
  message: string;
  meta?: Record<string, unknown>;
}

export interface ActivityLogResponse {
  ok: boolean;
  logs: ActivityLogItem[];
}
