import assert from 'node:assert/strict';
import { rssSeedFollowStateLabel } from '../src/utils/rssProcessingState.ts';

assert.equal(rssSeedFollowStateLabel('linked', false), '已关联');
assert.equal(rssSeedFollowStateLabel('unlinked', false), '未关联');
assert.equal(rssSeedFollowStateLabel(undefined, false), '未关联');
assert.equal(rssSeedFollowStateLabel('linked', true), undefined);
