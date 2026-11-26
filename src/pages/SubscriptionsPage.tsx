import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ExternalLink, RefreshCw, Rss, ShieldCheck, Trash2 } from 'lucide-react';
import { api } from '../services/api';
import type {
  AppSettings,
  DecisionLog,
  IntentMode,
  MediaType,
  ReleaseGroupCheck,
  RssPreviewItem,
  SaIdentifyMapping,
  SaRecoveryJob,
  UnifiedSubscription,
} from '../types/api';
import { RssAutoDownloadPanel } from '../components/RssAutoDownloadPanel';
import { StatusPill } from '../components/StatusPill';

interface SubscriptionsPageProps {
  onOpenMedia: (type: MediaType, tmdbId: string) => void;
}

export function SubscriptionsPage({ onOpenMedia }: SubscriptionsPageProps) {
  const [tab, setTab] = useState<'list' | 'rss-auto' | 'checker' | 'mappings' | 'logs'>('list');
  const [items, setItems] = useState<UnifiedSubscription[]>([]);
  const [logs, setLogs] = useState<DecisionLog[]>([]);
  const [saMappings, setSaMappings] = useState<SaIdentifyMapping[]>([]);
  const [saJobs, setSaJobs] = useState<SaRecoveryJob[]>([]);
  const [guardrails, setGuardrails] = useState<AppSettings['guardrails'] | null>(null);
  const [filterIntent, setFilterIntent] = useState<'all' | IntentMode | 'dup'>('all');
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  // Release group checker state
  const [testSiteId, setTestSiteId] = useState('mteam');
  const [testTitle, setTestTitle] = useState('Show.Name.S01E01.2026.2160p.WEB-DL.H265-FRDS');
  const [testOfficialTag, setTestOfficialTag] = useState(false);
  const [checkResult, setCheckResult] = useState<ReleaseGroupCheck | null>(null);
  const [rssItems, setRssItems] = useState<RssPreviewItem[]>([]);
  const [rssErrors, setRssErrors] = useState<string[]>([]);
  const [rssLoading, setRssLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [res, saRes] = await Promise.all([api.getSubscriptions(), api.getSaMappings()]);
      setItems(res.items);
      setLogs(res.decisionLogs);
      setGuardrails(res.guardrails);
      setSaMappings(saRes.mappings);
      setSaJobs(saRes.jobs);
      if (res.guardrails.siteOfficialGroups.length) {
        setTestSiteId((cur) => cur || res.guardrails.siteOfficialGroups[0].siteId);
      }
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '加载订阅失败' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleIntent = async (sub: UnifiedSubscription, next: IntentMode) => {
    if (!sub.tmdbId) return;
    try {
      await api.updateMediaPreference(sub.mediaType, sub.tmdbId, { intent: next });
      setToast({ tone: 'ok', text: `已将《${sub.title}》设为「${next === 'collect' ? '收藏' : '追更'}」` });
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '更新意图失败' });
    }
  };

  const runCheck = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const res = await api.checkReleaseGroup({
        siteId: testSiteId,
        title: testTitle,
        hasOfficialTag: testOfficialTag,
      });
      setCheckResult(res);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '校验失败' });
    }
  };

  const loadRss = async () => {
    setRssLoading(true);
    try {
      const res = await api.getRssPreview();
      setRssItems(res.items);
      setRssErrors(res.errors);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : 'RSS 读取失败' });
    } finally {
      setRssLoading(false);
    }
  };

  const filtered = items.filter((sub) => {
    if (filterIntent === 'dup') return sub.duplicateProviders;
    if (filterIntent === 'follow' || filterIntent === 'collect') return sub.intent === filterIntent;
    return true;
  });

  return (
    <div className="stack">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div className="tabs" style={{ marginBottom: 0 }}>
          <button className={`tab-btn ${tab === 'list' ? 'active' : ''}`} type="button" onClick={() => setTab('list')}>
            统一订阅（{items.length}）
          </button>
          <button className={`tab-btn ${tab === 'rss-auto' ? 'active' : ''}`} type="button" onClick={() => setTab('rss-auto')}>
            官组 RSS 自动下载
          </button>
          <button className={`tab-btn ${tab === 'checker' ? 'active' : ''}`} type="button" onClick={() => setTab('checker')}>
            站点官组校验 & RSS 透视
          </button>
          <button className={`tab-btn ${tab === 'mappings' ? 'active' : ''}`} type="button" onClick={() => setTab('mappings')}>
            SA 篇章映射（{saMappings.length}）
          </button>
          <button className={`tab-btn ${tab === 'logs' ? 'active' : ''}`} type="button" onClick={() => setTab('logs')}>
            决策解释记录（{logs.length}）
          </button>
        </div>
        <button className="btn btn-sm" type="button" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} /> 刷新
        </button>
      </div>

      {toast && <div className={`banner ${toast.tone}`}>{toast.text}</div>}

      {tab === 'list' && (
        <section className="card stack">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {([
                ['all', '全部'],
                ['follow', '追更'],
                ['collect', '收藏'],
                ['dup', 'MP + Torra 重复订阅'],
              ] as const).map(([key, label]) => (
                <button
                  key={key}
                  className={`btn btn-sm ${filterIntent === key ? 'btn-primary' : ''}`}
                  type="button"
                  onClick={() => setFilterIntent(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="muted">规则保留在 MoviePilot Neo / Torra 原软件维护，此处统一聚合透视</span>
          </div>

          {!filtered.length ? (
            <div className="muted">暂无匹配的订阅记录。</div>
          ) : (
            <div className="list">
              {filtered.map((sub) => (
                <div key={sub.groupKey} className={`list-row ${sub.duplicateProviders ? 'list-row--warn' : ''}`}>
                  <div style={{ display: 'flex', gap: 12, flex: 1, minWidth: 0 }}>
                    {sub.posterUrl && <img className="poster-thumb" src={sub.posterUrl} alt={sub.title} />}
                    <div className="row-main">
                      <div className="row-title">
                        <span>{sub.title}</span>
                        {sub.year && <span className="muted">{sub.year}</span>}
                        {sub.seasons.length > 0 && <span className="pill idle">S{sub.seasons.join(' / S')}</span>}
                        <StatusPill status={sub.intent === 'collect' ? 'protected' : 'active'} label={sub.intent === 'collect' ? '收藏' : '追更'} />
                        {sub.duplicateProviders && <StatusPill status="warn" label="MP Neo + Torra 同时订阅" />}
                      </div>
                      <div style={{ fontSize: 13, marginTop: 4 }}>
                        来源：{sub.sources.map((s) => s.sourceLabel).join('、')} · 规则：{sub.ruleSummary}
                      </div>
                      <div className="muted">{sub.originReason}</div>
                    </div>
                  </div>
                  <div className="row-actions">
                    {sub.tmdbId && (
                      <button className="btn btn-sm btn-primary" type="button" onClick={() => onOpenMedia(sub.mediaType, sub.tmdbId)}>
                        看 5 步链路
                      </button>
                    )}
                    {sub.tmdbId && (
                      <button
                        className="btn btn-sm"
                        type="button"
                        onClick={() => void toggleIntent(sub, sub.intent === 'collect' ? 'follow' : 'collect')}
                      >
                        切为{sub.intent === 'collect' ? '追更' : '收藏'}
                      </button>
                    )}
                    {sub.sources[0]?.webUrl && (
                      <a className="btn btn-sm" href={sub.sources[0].webUrl} target="_blank" rel="noreferrer">
                        原软件 <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {tab === 'rss-auto' && <RssAutoDownloadPanel />}

      {tab === 'checker' && (
        <div className="stack">
          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title"><ShieldCheck size={17} /> 站点 × 本站官组校验器</h2>
              <span className="muted">按「来源站 + 该站官组」判定，精准拦截跨站转载组</span>
            </div>
            <form className="form-grid" onSubmit={runCheck}>
              <div className="field">
                <label>来源站点（按优先级排序）</label>
                <select value={testSiteId} onChange={(e) => setTestSiteId(e.target.value)}>
                  {(guardrails?.siteOfficialGroups || []).map((s) => (
                    <option key={s.siteId} value={s.siteId}>
                      #{s.priority} {s.siteName}（{s.matchMode === 'official_tag' ? '本站官方标签' : s.officialGroups.slice(0, 4).join(', ')}）
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>种子标题 / 文件名</label>
                <input type="text" value={testTitle} onChange={(e) => setTestTitle(e.target.value)} />
              </div>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                <input type="checkbox" checked={testOfficialTag} onChange={(e) => setTestOfficialTag(e.target.checked)} />
                模拟该种子带有「本站官方标签」（用于高清视界 / AGSVPT 等标签站）
              </label>
              <div>
                <button className="btn btn-primary" type="submit">检验归属与拦截结果</button>
              </div>
            </form>
            {checkResult && (
              <div className={`list-row ${checkResult.classification === 'cross_site_repost' ? 'list-row--error' : ''}`}>
                <div className="row-main">
                  <div className="row-title">
                    <StatusPill status={checkResult.classification} label={checkResult.classificationLabel} />
                    <span>来源站：{checkResult.sourceSite}</span>
                    {checkResult.releaseGroup && <span className="pill idle">发布组：{checkResult.releaseGroup}</span>}
                    <StatusPill status={checkResult.allowed ? 'ok' : 'error'} label={checkResult.allowed ? '允许选种' : '规则拦截'} />
                  </div>
                  <div style={{ marginTop: 6, fontSize: 13 }}>{checkResult.reasonText}</div>
                  {checkResult.versionTags.length > 0 && (
                    <div className="muted" style={{ marginTop: 4 }}>版本特征：{checkResult.versionTags.join(' · ')}</div>
                  )}
                </div>
              </div>
            )}
          </section>

          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title"><Rss size={17} /> 私人 PT RSS 实时选种透视</h2>
              <button className="btn btn-sm btn-primary" type="button" onClick={() => void loadRss()} disabled={rssLoading}>
                {rssLoading ? '拉取中…' : '立即拉取并检验官组'}
              </button>
            </div>
            {rssErrors.map((e, i) => <div key={i} className="banner error">{e}</div>)}
            {!rssItems.length ? (
              <div className="muted">在「设置 → 选种护栏」添加私人 RSS 源后，点击上方按钮即可实时预览每个种子的「本站官组 / 跨站转载组」判定结果。</div>
            ) : (
              <div className="list">
                {rssItems.map((item, idx) => (
                  <div key={idx} className={`list-row ${item.groupCheck.classification === 'cross_site_repost' ? 'list-row--error' : ''}`}>
                    <div className="row-main">
                      <div className="row-title">
                        <span className="pill idle">{item.sourceName}</span>
                        <StatusPill status={item.groupCheck.classification} label={item.groupCheck.classificationLabel} />
                        {item.groupCheck.releaseGroup && <span className="pill idle">{item.groupCheck.releaseGroup}</span>}
                      </div>
                      <div style={{ marginTop: 4, fontSize: 13, wordBreak: 'break-all' }}>{item.title}</div>
                      <div className="muted" style={{ marginTop: 2 }}>{item.groupCheck.reasonText}</div>
                    </div>
                    {item.detailUrl && (
                      <a className="btn btn-sm" href={item.detailUrl} target="_blank" rel="noreferrer">
                        站点详情 <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {tab === 'mappings' && (
        <div className="stack">
          <section className="card">
            <div className="card-header">
              <h2 className="card-title">已确认的 SA 篇章限定映射</h2>
              <span className="muted">严格限定到具体篇章指纹，后续同类集数可复用，绝不误改其他作品的 S01</span>
            </div>
            {!saMappings.length ? (
              <div className="muted">暂无已保存的篇章映射。在首页 SA 识别失败条目中点击「诊断与恢复」确认一次后即会沉淀于此。</div>
            ) : (
              <div className="list">
                {saMappings.map((m) => (
                  <div key={m.id} className="list-row">
                    <div className="row-main">
                      <div className="row-title">
                        <strong>{m.arcTitle}</strong>
                        <StatusPill
                          status="protected"
                          label={`S${String(m.sourceSeason).padStart(2, '0')} → TMDB #${m.tmdbId} S${String(m.targetSeason).padStart(2, '0')}`}
                        />
                        <span>{m.canonicalTitle}{m.seasonName ? ` · ${m.seasonName}` : ''}</span>
                      </div>
                      <div className="muted" style={{ marginTop: 4 }}>
                        确认样本：{m.confirmedBySample || '-'} · 更新于 {m.updatedAt}
                      </div>
                    </div>
                    <button
                      className="btn btn-sm"
                      type="button"
                      onClick={async () => {
                        await api.deleteSaMapping(m.id);
                        setToast({ tone: 'ok', text: '已删除该条篇章映射' });
                        await load();
                      }}
                    >
                      <Trash2 size={13} /> 删除
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="card-title">最近 SA 单文件恢复记录</h2>
              <span className="muted">核验新 SA 成功记录、TMDB/季集、目标视频与 STRM 后才标验证成功</span>
            </div>
            {!saJobs.length ? (
              <div className="muted">暂无 SA 恢复执行记录。</div>
            ) : (
              <div className="list">
                {saJobs.map((j) => (
                  <div key={j.id} className="list-row">
                    <div className="row-main">
                      <div className="row-title">
                        <StatusPill
                          status={
                            j.status === 'resolved'
                              ? 'ok'
                              : j.status === 'failed'
                                ? 'error'
                                : j.status === 'skipped_existing'
                                  ? 'protected'
                                  : j.status === 'not_processed'
                                    ? 'warn'
                                    : 'active'
                          }
                          label={
                            j.status === 'resolved'
                              ? '验证成功'
                              : j.status === 'failed'
                                ? '再次失败'
                                : j.status === 'skipped_existing'
                                  ? '已有版本，无需补库'
                                  : j.status === 'not_processed'
                                    ? '尚未处理'
                                    : '待处理 / 核验中'
                          }
                        />
                        <span>{j.canonicalTitle}</span>
                        {j.targetSeason && j.targetEpisode && (
                          <span className="pill idle">S{String(j.targetSeason).padStart(2, '0')}E{String(j.targetEpisode).padStart(2, '0')}</span>
                        )}
                      </div>
                      <div className="muted" style={{ marginTop: 4, wordBreak: 'break-all' }}>{j.fileName}</div>
                      <div style={{ fontSize: 13, marginTop: 4 }}>{j.verification.detail || j.lastError}</div>
                    </div>
                    {j.status !== 'resolved' && (
                      <button
                        className="btn btn-sm btn-primary"
                        type="button"
                        onClick={async () => {
                          const r = await api.verifySaRecovery(j.id);
                          setToast({ tone: r.resolved ? 'ok' : 'error', text: r.message });
                          await load();
                        }}
                      >
                        核验结果
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {tab === 'logs' && (
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">订阅与选种决策解释</h2>
            <span className="muted">清晰记录「为什么订这部、为什么拦掉老片、用了哪条规则」</span>
          </div>
          {!logs.length ? (
            <div className="muted">暂无决策记录。通过「发现」页订阅或拦截老片后会自动记录在此。</div>
          ) : (
            <div className="list">
              {logs.map((log) => (
                <div key={log.id} className="list-row">
                  <div className="row-main">
                    <div className="row-title">
                      <StatusPill status={log.outcome === 'blocked' ? 'warn' : 'ok'} label={log.ruleName} />
                      <span>{log.title}</span>
                      <span className="muted">{log.sourceLabel}</span>
                    </div>
                    <div style={{ marginTop: 4, fontSize: 13 }}>{log.reasonText}</div>
                    <div className="muted">{log.createdAt}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
