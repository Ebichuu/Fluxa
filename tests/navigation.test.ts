import assert from 'node:assert/strict';
import { pathForNavigation, readNavigation } from '../src/app/navigation.ts';

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
