import assert from 'node:assert/strict';
import {
  rssResourceActionFeedback,
  rssResourcePreviewFeedback
} from '../src/utils/rssResourceDownloadFeedback.ts';
import type { AutomationAction, RssResourceDownloadPreview } from '../src/types/rssSeedLibrary.ts';

const preview = (patch: Partial<RssResourceDownloadPreview>): RssResourceDownloadPreview => ({
  status: 'ready',
  ready: true,
  capabilityState: 'ready',
  itemId: 'rss-item-1',
  categoryDirectory: '02-国产剧',
  previewToken: 'preview-token',
  blockers: [],
  observedAt: '2026-08-16T08:00:00Z',
  ...patch
});

const action = (patch: Partial<AutomationAction>): AutomationAction => ({
  id: 'action-1',
  provider: 'qbittorrent',
  type: 'rss-resource-download',
  status: 'submitted',
  result: null,
  ...patch
});

assert.deepEqual(rssResourcePreviewFeedback(preview({
  status: 'blocked',
  ready: false,
  capabilityState: 'blocked',
  previewToken: '',
  blockers: [{ code: 'RSS_RESOURCE_TORRA_BUSY', message: 'Torra 当前正在处理该订阅' }]
})), {
  tone: 'error',
  message: '未提交：Torra 当前正在处理该订阅'
});

assert.deepEqual(rssResourcePreviewFeedback(preview({})), {
  tone: 'ok',
  message: '预检通过：将自动归入 02-国产剧，请确认提交'
});

assert.deepEqual(rssResourceActionFeedback(action({ status: 'submitted' })), {
  tone: 'pending',
  message: '已发出提交请求，正在确认 qB 接收结果…'
});

assert.deepEqual(rssResourceActionFeedback(action({
  status: 'succeeded',
  result: { alreadyPresent: false }
})), {
  tone: 'ok',
  message: 'qB 已接收，下载任务已建立'
});

assert.deepEqual(rssResourceActionFeedback(action({
  status: 'failed',
  error: { message: 'qB 连接失败' }
})), {
  tone: 'error',
  message: '提交失败：qB 连接失败'
});
