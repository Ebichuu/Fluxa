import assert from 'node:assert/strict';
import {
  rssCandidateGroupScope,
  rssCandidateScoreGain,
  rssMinorUpgradeWarning,
  rssResourceClassificationBlocker,
  rssTorraAnalysisPresentation,
  summarizeRssOwnerships
} from '../src/utils/rssLibraryPresentation.ts';

assert.deepEqual(rssResourceClassificationBlocker({
  identityStatus: 'identified',
  mediaType: 'unknown'
}), {
  title: '媒体类型未确认，暂不能自动分类',
  actionLabel: '需确认类型'
});

assert.deepEqual(rssResourceClassificationBlocker({
  identityStatus: 'unidentified',
  mediaType: 'tv'
}), {
  title: '媒体身份未确认，暂不能自动分类',
  actionLabel: '需先识别'
});

assert.equal(rssResourceClassificationBlocker({
  identityStatus: 'identified',
  mediaType: 'tv'
}), null);

assert.deepEqual(summarizeRssOwnerships([
  {
    matchId: 'match-1',
    subscriptionId: 'torra:public-owner',
    unitId: 'unit-1',
    state: 'invalid',
    reasonCode: 'subscription_missing'
  },
  {
    matchId: 'match-2',
    subscriptionId: 'torra:public-owner',
    unitId: 'unit-1',
    state: 'invalid',
    reasonCode: 'subscription_missing'
  }
]), [{
  key: 'invalid:subscription_missing:torra:public-owner',
  state: 'invalid',
  label: '原 Torra 订阅已删除',
  count: 2,
  subscriptionId: 'torra:public-owner',
  reasonCode: 'subscription_missing'
}]);

assert.equal(rssCandidateGroupScope('scoring', false), 'scoreable');
assert.equal(rssCandidateGroupScope('cleanup', false), 'decision');
assert.equal(rssCandidateGroupScope('scoring', true), undefined);

assert.deepEqual(rssTorraAnalysisPresentation({
  status: 'candidate',
  evaluationStatus: 'scored',
  evaluationReason: ''
}), {
  enabled: false,
  label: '评分已完成',
  title: '当前候选已完成评分，无需重新分析整条订阅',
  primary: false
});

assert.equal(rssTorraAnalysisPresentation({
  status: 'candidate',
  evaluationStatus: 'blocked',
  evaluationReason: 'candidate_scope_mismatch'
}).enabled, false);

assert.equal(rssTorraAnalysisPresentation({
  status: 'candidate',
  evaluationStatus: 'blocked',
  evaluationReason: 'torra_unavailable'
}).enabled, true);

assert.equal(rssCandidateScoreGain({ bestCandidateScore: 50.05, baselineScore: 49.87 }), 0.17999999999999972);
assert.equal(rssMinorUpgradeWarning({
  state: 'upgrade_available',
  bestCandidateScore: 50.05,
  baselineScore: 49.87
}), '仅提升 0.18 分，属于小幅提升；建议先核对版本差异。');
