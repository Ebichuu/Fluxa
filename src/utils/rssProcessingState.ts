import type { RssSeedItem } from '../types/rssSeedLibrary';

/**
 * Returns the resource-level state when no candidate group is available.
 * A linked resource is not evidence that scoring has started.
 */
export function rssSeedFollowStateLabel(
  followState: RssSeedItem['followState'] | undefined,
  hasMatch: boolean
) {
  if (hasMatch) return undefined;
  return followState === 'linked' ? '已关联' : '未关联';
}
