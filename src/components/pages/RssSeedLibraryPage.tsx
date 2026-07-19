import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Edit3,
  Plus,
  RefreshCcw,
  Rss,
  Search,
  Send,
  ServerCog,
  Trash2,
  X
} from 'lucide-react';
import {
  deleteRssSource,
  getAutomationAction,
  getRssSeedItems,
  getRssMatches,
  getRssSources,
  saveRssSource,
  startRssMatchAnalysis,
  testRssSource
} from '../../services/api';
import type { AutomationAction, RssLibrarySummary, RssMatch, RssSeedItem, RssSource, RssSourceInput } from '../../types/rssSeedLibrary';
import { formatTimeAgo } from '../../utils/formatters';
import { ConfirmDialog } from '../layout/ConfirmDialog';

type WindowFilter = '' | '1h' | '24h' | '7d';

const emptySummary: RssLibrarySummary = {
  enabled: false,
  sources: 0,
  activeSources: 0,
  errorSources: 0,
  items: 0,
  lastSuccessAt: ''
};

const defaultForm: RssSourceInput = {
  name: '',
  feedUrl: '',
  enabled: true,
  intervalMinutes: 5,
  retentionDays: 7,
  allowHttp: false
};

function sizeLabel(bytes: number) {
  if (!bytes) return '大小未知';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function episodeLabel(item: RssSeedItem) {
  if (item.mediaType !== 'tv') return '电影';
  const season = item.seasonNumber == null ? '' : `S${String(item.seasonNumber).padStart(2, '0')}`;
  const episodes = item.episodeStart == null
    ? ''
    : item.episodeEnd && item.episodeEnd !== item.episodeStart
      ? `E${String(item.episodeStart).padStart(2, '0')}-${String(item.episodeEnd).padStart(2, '0')}`
      : `E${String(item.episodeStart).padStart(2, '0')}`;
  return `${season}${episodes}` || '剧集';
}

export function RssSeedLibraryPage() {
  const [sources, setSources] = useState<RssSource[]>([]);
  const [summary, setSummary] = useState<RssLibrarySummary>(emptySummary);
  const [items, setItems] = useState<RssSeedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [matches, setMatches] = useState<RssMatch[]>([]);
  const [matchesTotal, setMatchesTotal] = useState(0);
  const [matchesOffset, setMatchesOffset] = useState(0);
  const [matchesLoading, setMatchesLoading] = useState(false);
  const [matchActions, setMatchActions] = useState<Record<string, AutomationAction>>({});
  const [matchBusy, setMatchBusy] = useState('');
  const [query, setQuery] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [windowFilter, setWindowFilter] = useState<WindowFilter>('24h');
  const [offset, setOffset] = useState(0);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState<{ tone: 'ok' | 'error'; message: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<RssSource | null>(null);
  const [form, setForm] = useState<RssSourceInput>(defaultForm);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RssSource | null>(null);
  const itemsRequestRef = useRef<AbortController | null>(null);
  const matchesRequestRef = useRef<AbortController | null>(null);
  const matchPollRef = useRef<AbortController | null>(null);
  const pageSize = 50;

  const loadSources = () => getRssSources().then((payload) => {
    setSources(payload.items);
    setSummary(payload.summary);
  });

  const loadItems = async (input: { query?: string; offset?: number } = {}) => {
    itemsRequestRef.current?.abort();
    const controller = new AbortController();
    itemsRequestRef.current = controller;
    setItemsLoading(true);
    try {
      const payload = await getRssSeedItems(
        {
          query: input.query ?? query,
          sourceId,
          window: windowFilter,
          limit: pageSize,
          offset: input.offset ?? offset
        },
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      setItems(payload.items);
      setTotal(payload.total);
      setOffset(payload.offset);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '种子库读取失败' });
      }
    } finally {
      if (!controller.signal.aborted) setItemsLoading(false);
    }
  };

  const loadMatches = async (nextOffset = matchesOffset) => {
    matchesRequestRef.current?.abort();
    const controller = new AbortController();
    matchesRequestRef.current = controller;
    setMatchesLoading(true);
    try {
      const payload = await getRssMatches({ status: 'candidate', limit: 10, offset: nextOffset }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setMatches(payload.items);
      setMatchesTotal(payload.total);
      setMatchesOffset(payload.offset);
    } catch (reason) {
      if (!controller.signal.aborted) setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配读取失败' });
    } finally {
      if (!controller.signal.aborted) setMatchesLoading(false);
    }
  };

  const refresh = async () => {
    setLoading(true);
    setFeedback(null);
    try {
        await Promise.all([loadSources(), loadItems({ offset: 0 }), loadMatches(0)]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '种子库读取失败' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, windowFilter]);

  useEffect(() => () => {
    itemsRequestRef.current?.abort();
    matchesRequestRef.current?.abort();
    matchPollRef.current?.abort();
  }, []);

  const pollMatchAction = async (matchId: string, actionId: string) => {
    matchPollRef.current?.abort();
    const controller = new AbortController();
    matchPollRef.current = controller;
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const action = await getAutomationAction(actionId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setMatchActions((current) => ({ ...current, [matchId]: action }));
        if (['succeeded', 'failed', 'cancelled'].includes(action.status)) {
          void loadMatches(matchesOffset);
          return;
        }
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 1500);
          controller.signal.addEventListener('abort', () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
      }
    } catch (reason) {
      if (!controller.signal.aborted) setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配动作读取失败' });
    } finally {
      if (matchPollRef.current === controller) matchPollRef.current = null;
    }
  };

  const analyzeMatch = (match: RssMatch) => {
    setMatchBusy(match.id);
    startRssMatchAnalysis(match.id, window.crypto.randomUUID())
      .then((action) => {
        setMatchActions((current) => ({ ...current, [match.id]: action }));
        void pollMatchAction(match.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配分析提交失败' }))
      .finally(() => setMatchBusy(''));
  };

  const timeline = useMemo(() => items.map((item) => ({
    ...item,
    timeLabel: item.publishedAt ? formatTimeAgo(item.publishedAt) : formatTimeAgo(item.lastSeenAt)
  })), [items]);

  const openCreate = () => {
    setEditing(null);
    setForm(defaultForm);
    setFormOpen(true);
    setFeedback(null);
  };

  const openEdit = (source: RssSource) => {
    setEditing(source);
    setForm({
      name: source.name,
      feedUrl: '',
      enabled: source.enabled,
      intervalMinutes: source.intervalMinutes as 1 | 3 | 5,
      retentionDays: source.retentionDays as 3 | 7 | 14,
      allowHttp: source.allowHttp
    });
    setFormOpen(true);
    setFeedback(null);
  };

  const submitSource = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const payload = { ...form };
      if (editing && !payload.feedUrl) delete payload.feedUrl;
      await saveRssSource(payload, editing?.id);
      setFormOpen(false);
      setEditing(null);
      setForm(defaultForm);
      setFeedback({ tone: 'ok', message: editing ? '来源设置已保存' : 'RSS 来源已加入种子库' });
      await loadSources();
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 来源保存失败' });
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (source: RssSource) => {
    setFeedback(null);
    try {
      const action = await testRssSource(source.id);
      setFeedback({
        tone: action.status === 'succeeded' ? 'ok' : 'error',
        message: action.status === 'succeeded'
          ? `RSS 可读取，识别到 ${action.result?.items ?? 0} 条内容`
          : action.result?.message || 'RSS 测试失败'
      });
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 测试失败' });
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      await deleteRssSource(deleteTarget.id);
      if (sourceId === deleteTarget.id) setSourceId('');
      setDeleteTarget(null);
      setFeedback({ tone: 'ok', message: '来源和对应本地索引已删除' });
       await Promise.all([loadSources(), loadItems({ offset: 0 })]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '来源删除失败' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="work-page ops-page rss-library-page">
      <section className="ops-hero rss-library-hero">
        <div>
          <p className="ops-eyebrow">PT 本地索引</p>
          <h1>种子库</h1>
          <p className="ops-page-subtitle">在本地汇总和筛选最近发布的种子。</p>
          <p className="ops-deck">集中保存最近发布的 PT RSS 内容，在本地完成搜索和筛选，再由 Torra 判断是否需要下载。</p>
        </div>
        <button className={summary.enabled ? 'ops-command ops-command--ok' : 'ops-command'} type="button" onClick={refresh}>
          <Rss aria-hidden="true" size={18} />
          <span>
            <small>RSS 收集</small>
            <strong>{summary.enabled ? '已开启' : '当前关闭'}</strong>
          </span>
          <RefreshCcw aria-hidden="true" className={loading ? 'rss-spin' : ''} size={15} />
        </button>
      </section>

      <section className="rss-ledger-strip" aria-label="种子库状态">
        <div><span>本地种子</span><strong>{summary.items}</strong></div>
        <div><span>RSS 来源</span><strong>{summary.activeSources}/{summary.sources}</strong></div>
        <div><span>异常来源</span><strong className={summary.errorSources ? 'rss-value--warn' : ''}>{summary.errorSources}</strong></div>
        <div><span>最近收集</span><strong>{summary.lastSuccessAt ? formatTimeAgo(summary.lastSuccessAt) : '尚未收集'}</strong></div>
      </section>

      {feedback && (
        <div className={feedback.tone === 'error' ? 'rss-feedback rss-feedback--error' : 'rss-feedback'} role="status">
          {feedback.tone === 'error' ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
          {feedback.message}
        </div>
      )}

      <section className="rss-library-layout">
        <div className="rss-index-panel">
          <div className="rss-toolbar">
            <form
              className="rss-search"
              onSubmit={(event) => {
                event.preventDefault();
                void loadItems({ offset: 0 });
              }}
            >
              <Search aria-hidden="true" size={16} />
              <input aria-label="搜索本地种子" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="片名、制作组、HDR、2160P…" />
              {query && <button aria-label="清空搜索" title="清空搜索" type="button" onClick={() => { setQuery(''); void loadItems({ query: '', offset: 0 }); }}><X aria-hidden="true" size={14} /></button>}
            </form>
            <div className="rss-window-tabs" aria-label="更新时间范围">
              {([
                ['', '全部'], ['1h', '1 小时'], ['24h', '24 小时'], ['7d', '7 天']
              ] as Array<[WindowFilter, string]>).map(([value, label]) => (
                <button className={windowFilter === value ? 'rss-window-tab rss-window-tab--active' : 'rss-window-tab'} key={label} type="button" onClick={() => setWindowFilter(value)}>{label}</button>
              ))}
            </div>
          </div>

          <div className="rss-index-head">
            <span>{loading || itemsLoading ? '正在读取本地索引' : `找到 ${total} 条内容`}</span>
            <select aria-label="按 RSS 来源筛选" value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
              <option value="">全部来源</option>
              {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
            </select>
          </div>

          <div className="rss-timeline">
            {!loading && timeline.length === 0 && (
              <div className="ops-empty rss-empty">
                <Database aria-hidden="true" size={22} />
                <strong>本地种子库还是空的</strong>
                <span>先添加 RSS 来源；开启收集后，新发布内容会保存在这里。</span>
              </div>
            )}
            {timeline.map((item) => (
              <article className="rss-seed-row" key={item.id}>
                <div className="rss-seed-time"><span /> <time>{item.timeLabel}</time></div>
                <div className="rss-seed-body">
                  <div className="rss-seed-meta">
                    <span>{item.sourceName}</span>
                    <span>{episodeLabel(item)}</span>
                    <span>{sizeLabel(item.sizeBytes)}</span>
                  </div>
                  <h2>{item.title}</h2>
                  <div className="rss-version-line">
                    {item.versionSummary
                      ? item.versionSummary.split(' · ').map((value) => <span key={value}>{value}</span>)
                      : <span className="rss-version-muted">等待版本信息</span>}
                  </div>
                </div>
                <div className="rss-seed-state">
                  <span className={item.hasDownload ? 'state-chip state-chip--ok' : 'state-chip'}>{item.hasDownload ? '可交给 Torra' : '仅详情'}</span>
                  <small>{item.sourceDomain}</small>
                </div>
              </article>
            ))}
          </div>
          {total > pageSize && (
            <nav className="rss-pagination" aria-label="种子库分页">
              <button
                aria-label="上一页"
                disabled={itemsLoading || offset <= 0}
                title="上一页"
                type="button"
                onClick={() => void loadItems({ offset: Math.max(0, offset - pageSize) })}
              >
                <ChevronLeft aria-hidden="true" size={15} />
              </button>
              <span>第 {Math.floor(offset / pageSize) + 1} / {Math.ceil(total / pageSize)} 页</span>
              <button
                aria-label="下一页"
                disabled={itemsLoading || offset + pageSize >= total}
                title="下一页"
                type="button"
                onClick={() => void loadItems({ offset: offset + pageSize })}
              >
                <ChevronRight aria-hidden="true" size={15} />
              </button>
            </nav>
          )}
          <section className="rss-match-panel" aria-label="RSS 候选匹配">
            <header className="rss-match-panel__head">
              <div><strong>待人工分析匹配</strong><small>{matchesTotal ? `${matchesTotal} 条候选` : '只展示活动观察窗口内的匹配'}</small></div>
              <button className="ops-link" disabled={matchesLoading} type="button" onClick={() => void loadMatches(matchesOffset)}><RefreshCcw size={13} />刷新</button>
            </header>
            {matchesLoading && <small className="sub-detail__hint">正在读取 RSS 匹配…</small>}
            {!matchesLoading && matches.length === 0 && <small className="sub-detail__hint">当前没有待人工分析的 RSS 匹配。</small>}
            <div className="rss-match-list">
              {matches.map((match) => {
                const seed = items.find((item) => item.id === match.itemId);
                const action = matchActions[match.id];
                const actionRunning = action && !['succeeded', 'failed', 'cancelled'].includes(action.status);
                return (
                  <article className="rss-match-row" key={match.id}>
                    <div><strong>{seed?.title || `种子 ${match.itemId.slice(0, 8)}`}</strong><small>{match.subscriptionId} · {match.unitId}</small></div>
                    <span>{action ? (action.status === 'succeeded' ? '已完成' : action.status === 'failed' ? '失败' : '分析中') : '待分析'}</span>
                    <button className="tool-link" disabled={Boolean(actionRunning) || matchBusy === match.id} type="button" onClick={() => analyzeMatch(match)}>
                      <Send size={12} />{matchBusy === match.id ? '提交中' : '分析'}
                    </button>
                  </article>
                );
              })}
            </div>
            {matchesTotal > 10 && (
              <nav className="rss-pagination rss-pagination--compact" aria-label="RSS 匹配分页">
                <button aria-label="上一页匹配" title="上一页匹配" disabled={matchesLoading || matchesOffset <= 0} type="button" onClick={() => void loadMatches(Math.max(0, matchesOffset - 10))}><ChevronLeft aria-hidden="true" size={14} /></button>
                <span>第 {Math.floor(matchesOffset / 10) + 1} / {Math.ceil(matchesTotal / 10)} 页</span>
                <button aria-label="下一页匹配" title="下一页匹配" disabled={matchesLoading || matchesOffset + 10 >= matchesTotal} type="button" onClick={() => void loadMatches(matchesOffset + 10)}><ChevronRight aria-hidden="true" size={14} /></button>
              </nav>
            )}
          </section>
        </div>

        <aside className="rss-source-panel">
          <div className="rss-source-head">
            <div><ServerCog size={17} /><span><small>来源管理</small><strong>{sources.length} 个 RSS</strong></span></div>
            <button className="ops-link" type="button" onClick={openCreate}><Plus size={14} />添加来源</button>
          </div>

          {formOpen && (
            <div className="rss-source-form">
              <div className="rss-source-form__title"><strong>{editing ? '编辑来源' : '添加 RSS 来源'}</strong><button aria-label="关闭来源表单" title="关闭来源表单" type="button" onClick={() => setFormOpen(false)}><X aria-hidden="true" size={15} /></button></div>
              <label>来源名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：主站 RSS" /></label>
              <label>私人 RSS 地址<input autoComplete="off" className="monospace-text" type="password" value={form.feedUrl || ''} onChange={(event) => setForm({ ...form, feedUrl: event.target.value })} placeholder={editing ? '留空保持原地址' : 'https://…?passkey=…'} /></label>
              <div className="rss-source-form__pair">
                <label>轮询周期<select value={form.intervalMinutes} onChange={(event) => setForm({ ...form, intervalMinutes: Number(event.target.value) as 1 | 3 | 5 })}><option value={1}>1 分钟</option><option value={3}>3 分钟</option><option value={5}>5 分钟</option></select></label>
                <label>保留时间<select value={form.retentionDays} onChange={(event) => setForm({ ...form, retentionDays: Number(event.target.value) as 3 | 7 | 14 })}><option value={3}>3 天</option><option value={7}>7 天</option><option value={14}>14 天</option></select></label>
              </div>
              <label className="rss-source-check"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用这个来源</label>
              <label className="rss-source-check"><input type="checkbox" checked={form.allowHttp} onChange={(event) => setForm({ ...form, allowHttp: event.target.checked })} />允许 HTTP 或非标准端口</label>
              <button className="ops-action-button ops-action-button--primary" disabled={saving || !form.name.trim() || (!editing && !form.feedUrl?.trim())} type="button" onClick={submitSource}>{saving ? '正在保存' : '保存来源'}</button>
              <p>RSS 地址会明文保存在 SQLite，但页面、接口和日志不会回显完整 Passkey。</p>
            </div>
          )}

          <div className="rss-source-list">
            {sources.length === 0 && !formOpen && <div className="ops-empty">还没有 RSS 来源。添加后也不会自动访问，直到收集开关开启。</div>}
            {sources.map((source) => (
              <article className="rss-source-card" key={source.id}>
                <div className="rss-source-card__top">
                  <span className={source.lastError ? 'rss-source-light rss-source-light--error' : source.enabled ? 'rss-source-light' : 'rss-source-light rss-source-light--off'} />
                  <div><strong>{source.name}</strong><small>{source.domain}</small></div>
                  <span>{source.intervalMinutes}m</span>
                </div>
                <div className="rss-source-card__meta">
                  <span><Clock3 size={12} />保留 {source.retentionDays} 天</span>
                  <span>{source.lastSuccessAt ? formatTimeAgo(source.lastSuccessAt) : '尚未收集'}</span>
                </div>
                {source.lastError && <p>{source.lastError}</p>}
                <div className="rss-source-card__actions">
                  <button type="button" onClick={() => runTest(source)}>测试</button>
                  <button type="button" onClick={() => openEdit(source)}><Edit3 size={12} />编辑</button>
                  <button className="rss-source-delete" type="button" onClick={() => setDeleteTarget(source)}><Trash2 size={12} />删除</button>
                </div>
              </article>
            ))}
          </div>
        </aside>
      </section>

      <ConfirmDialog busy={saving} labelledBy="rss-delete-title" describedBy="rss-delete-description" open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)}>
        {deleteTarget && (
          <>
          <span className="ops-confirm-dialog__signal">删除本地来源</span>
          <h2 id="rss-delete-title">删除“{deleteTarget.name}”？</h2>
          <p id="rss-delete-description">这会删除该来源在 Fluxa 内保存的种子索引，不会修改 PT 站点上的任何数据。</p>
          <div className="ops-confirm-dialog__meta"><span>来源</span><strong>{deleteTarget.domain}</strong><span>影响</span><strong>本地索引</strong></div>
          <div className="ops-confirm-dialog__actions"><button className="ops-action-button" disabled={saving} type="button" onClick={() => setDeleteTarget(null)}>取消</button><button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus disabled={saving} type="button" onClick={confirmDelete}>{saving ? '正在删除' : '确认删除'}</button></div>
          </>
        )}
      </ConfirmDialog>
    </main>
  );
}
