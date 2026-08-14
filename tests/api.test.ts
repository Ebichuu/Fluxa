import assert from 'node:assert/strict';
import { ApiError, getActivityLogs, getHealth } from '../src/services/api.ts';

const originalFetch = globalThis.fetch;
const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
let assignedLocation = '';

Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    location: {
      pathname: '/tasks',
      search: '?outcomeState=action_required',
      hash: '',
      assign: (value: string) => {
        assignedLocation = value;
      }
    }
  }
});

try {
  globalThis.fetch = async () => new Response(JSON.stringify({
    error: '需要登录',
    code: 'AUTH_REQUIRED',
    request_id: 'request-from-body'
  }), {
    status: 401,
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': 'request-from-header'
    }
  });

  await assert.rejects(getHealth(), (reason: unknown) => {
    assert.ok(reason instanceof ApiError);
    assert.equal(reason.status, 401);
    assert.equal(reason.code, 'AUTH_REQUIRED');
    assert.equal(reason.requestId, 'request-from-body');
    return true;
  });
  assert.equal(
    assignedLocation,
    '/auth/login?next=%2Ftasks%3FoutcomeState%3Daction_required'
  );

  let activityUrl = '';
  globalThis.fetch = async (input) => {
    activityUrl = String(input);
    const logs = Array.from({ length: 21 }, (_, index) => ({
      time: `2026-08-15 00:00:${String(index).padStart(2, '0')}`,
      ts: index,
      category: 'system',
      action: 'test',
      status: 'info',
      message: `记录 ${index}`
    }));
    return new Response(JSON.stringify({ ok: true, view: 'important', logs }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  };

  const activity = await getActivityLogs('', { view: 'important', limit: 20 });
  assert.match(activityUrl, /limit=21/);
  assert.match(activityUrl, /view=important/);
  assert.equal(activity.logs.length, 20);
  assert.equal(activity.hasMore, true);

  globalThis.fetch = async () => {
    throw new TypeError('connection refused');
  };
  await assert.rejects(getHealth(), (reason: unknown) => {
    assert.ok(reason instanceof ApiError);
    assert.equal(reason.status, 0);
    assert.equal(reason.code, 'NETWORK_ERROR');
    return true;
  });

  globalThis.fetch = async (_input, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'));
    }, { once: true });
  });
  await assert.rejects(getHealth({ timeoutMs: 1 }), (reason: unknown) => {
    assert.ok(reason instanceof ApiError);
    assert.equal(reason.status, 0);
    assert.equal(reason.code, 'REQUEST_TIMEOUT');
    return true;
  });
} finally {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) {
    delete (globalThis as typeof globalThis & { window?: unknown }).window;
  } else {
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow });
  }
}
