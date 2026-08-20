import assert from 'node:assert/strict';
import { pathForNavigation, readNavigation } from '../src/app/navigation.ts';
import { subscriptionYearOptions } from '../src/utils/discoverSubscriptions.ts';

const path = pathForNavigation('rss-library', {
  mediaType: 'tv',
  tmdbId: '322072',
  title: '你在夏日之中',
  seasonNumber: 1,
  episodeNumber: 8,
  rssWindow: 'all'
});

assert.match(path, /^\/rss-library\?/);
assert.equal(new URL(path, 'http://fluxa.local').searchParams.get('window'), 'all');

const restored = readNavigation(new URL(`http://fluxa.local${path}`) as unknown as Location);
assert.equal(restored.page, 'rss-library');
assert.equal(restored.target?.rssWindow, 'all');
assert.equal(restored.target?.episodeNumber, 8);

const controlPath = pathForNavigation('control', { service: 'symedia' });
assert.equal(controlPath, '/control?service=symedia');
const restoredControl = readNavigation(new URL(`http://fluxa.local${controlPath}`) as unknown as Location);
assert.equal(restoredControl.page, 'control');
assert.equal(restoredControl.target?.service, 'symedia');

assert.equal(pathForNavigation('media'), '/hall');
const legacyMedia = readNavigation(new URL('http://fluxa.local/media') as unknown as Location);
assert.equal(legacyMedia.page, 'hall');
assert.equal(legacyMedia.canonical, false);
const mediaDetailPath = pathForNavigation('media', { mediaType: 'tv', tmdbId: '296003' });
assert.equal(mediaDetailPath, '/media/tv/296003');
const restoredMediaDetail = readNavigation(new URL(`http://fluxa.local${mediaDetailPath}`) as unknown as Location);
assert.equal(restoredMediaDetail.page, 'media');
assert.equal(restoredMediaDetail.target?.mediaType, 'tv');
assert.equal(restoredMediaDetail.target?.tmdbId, '296003');

assert.deepEqual(subscriptionYearOptions([
  { year: '2026' },
  { year: '1988' },
  { year: '2026' },
  { year: '' },
  { year: '1968-from-url' }
], '2024', 2027), ['2026', '2024', '1988']);
