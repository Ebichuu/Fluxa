import type { AutomationAction, RssResourceDownloadPreview } from '../types/rssSeedLibrary';

export type RssResourceDownloadFeedback = {
  tone: 'pending' | 'ok' | 'error';
  message: string;
};

export function rssResourcePreviewFeedback(
  preview: RssResourceDownloadPreview
): RssResourceDownloadFeedback {
  const primary = preview.blockers[0];
  if (!preview.ready || !preview.previewToken) {
    return {
      tone: 'error',
      message: `未提交：${primary?.message || '资源下载预检未通过'}`
    };
  }
  return {
    tone: 'ok',
    message: `预检通过：将自动归入 ${preview.categoryDirectory || preview.categoryLabel || '已确认分类'}，请确认提交`
  };
}

export function rssResourceActionFeedback(
  action: AutomationAction
): RssResourceDownloadFeedback {
  if (action.status === 'failed') {
    return {
      tone: 'error',
      message: `提交失败：${action.error?.message || 'qB 未接收该资源'}`
    };
  }
  if (action.status === 'cancelled') {
    return { tone: 'error', message: '提交已取消，qB 未接收该资源' };
  }
  if (action.status === 'succeeded') {
    return {
      tone: 'ok',
      message: action.result?.alreadyPresent === true
        ? 'qB 已有相同资源，无需重复提交'
        : 'qB 已接收，下载任务已建立'
    };
  }
  return { tone: 'pending', message: '已发出提交请求，正在确认 qB 接收结果…' };
}
