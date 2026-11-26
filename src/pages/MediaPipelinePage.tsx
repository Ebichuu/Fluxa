import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, ExternalLink, Pause, Play, RefreshCw, Search } from 'lucide-react';
import { api } from '../services/api';
import type { ActionImpactPreview, IntentMode, MediaPipelineResponse, MediaType } from '../types/api';
import { ConfirmImpactModal } from '../components/ConfirmImpactModal';
import { StatusPill } from '../components/StatusPill';

interface MediaPipelinePageProps {
  mediaType: MediaType;
  tmdbId: string;
  onBack: () => void;
}

export function MediaPipelinePage({ mediaType, tmdbId, onBack }: MediaPipelinePageProps) {
  const [data, setData] = useState<MediaPipelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    actionType: string;
    payload: Record<string, unknown>;
    preview: ActionImpactPreview;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.getMediaPipeline(mediaType, tmdbId);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : '作品链路加载失败');
    } finally {
      setLoading(false);
    }
  }, [mediaType, tmdbId]);

  useEffect(() => {
    void load();
  }, [load]);

  const setIntent = async (intent: IntentMode) => {
    try {
      await api.updateMediaPreference(mediaType, tmdbId, { intent });
      setToast({ tone: 'ok', text: `已标记为「${intent === 'collect' ? '收藏' : '追更'}」` });
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '更新失败' });
    }
  };

  const triggerAction = async (actionType: string, payload: Record<string, unknown>) => {
    setToast(null);
    try {
      const preview = await api.previewAction(actionType, { ...payload, mediaKey: `${mediaType}:${tmdbId}` });
      if (preview.requiresConfirm) {
        setPendingAction({ actionType, payload: { ...payload, mediaKey: `${mediaType}:${tmdbId}` }, preview });
        return;
      }
      setBusy(true);
      const res = await api.executeAction(actionType, { ...payload, mediaKey: `${mediaType}:${tmdbId}` }, true);
      setToast({ tone: 'ok', text: res.message || '操作成功' });
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '操作失败' });
    } finally {
      setBusy(false);
    }
  };

  const confirmPending = async () => {
    if (!pendingAction) return;
    setBusy(true);
    try {
      const res = await api.executeAction(pendingAction.actionType, pendingAction.payload, true);
      setToast({ tone: 'ok', text: res.message || '操作已执行' });
      setPendingAction(null);
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '操作失败' });
    } finally {
      setBusy(false);
    }
  };

  if (loading && !data) {
    return <div className="card muted">正在加载处理进度…</div>;
  }

  if (error || !data) {
    return (
      <div className="card stack">
        <div className="banner error">{error || '无法加载作品状态'}</div>
        <button className="btn" type="button" onClick={onBack}><ArrowLeft size={14} /> 返回</button>
      </div>
    );
  }

  const subStep = data.steps.find((s) => s.step === 'subscription');
  const qbStep = data.steps.find((s) => s.step === 'qbittorrent');
  const c115Step = data.steps.find((s) => s.step === 'cloud115');
  const saStep = data.steps.find((s) => s.step === 'symedia');
  const embyStep = data.steps.find((s) => s.step === 'emby');

  return (
    <div className="stack">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <button className="btn btn-sm" type="button" onClick={onBack}>
          <ArrowLeft size={14} /> 返回
        </button>
        <button className="btn btn-sm" type="button" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} /> 刷新进度
        </button>
      </div>

      {toast && <div className={`banner ${toast.tone}`}>{toast.text}</div>}

      {/* Hero */}
      <section className="card" style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {data.media.posterUrl && <img className="poster-lg" src={data.media.posterUrl} alt={data.media.title} />}
        <div style={{ flex: 1, minWidth: 220 }} className="stack">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <h1 style={{ margin: 0, fontSize: 20 }}>{data.media.title}</h1>
              <span className="pill idle">{data.media.mediaType === 'tv' ? '剧集' : '电影'}</span>
              {data.media.year && <span className="muted">{data.media.year}</span>}
              <span className="muted">TMDB #{data.media.tmdbId}</span>
            </div>
            {data.media.overview && <p className="muted" style={{ margin: '6px 0 0' }}>{data.media.overview}</p>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="muted">个人意图：</span>
            <button
              className={`btn btn-sm ${data.media.intent === 'follow' ? 'btn-primary' : ''}`}
              type="button"
              onClick={() => void setIntent('follow')}
            >
              追更（跟播及时看）
            </button>
            <button
              className={`btn btn-sm ${data.media.intent === 'collect' ? 'btn-primary' : ''}`}
              type="button"
              onClick={() => void setIntent('collect')}
            >
              收藏（定版收齐）
            </button>
          </div>
        </div>
      </section>

      {/* 5-Step Pipeline Summary Bar */}
      <section className="pipeline-steps">
        {data.steps.map((step, idx) => (
          <div key={step.step} className={`step-card status-${step.status}`}>
            <div className="muted">{idx + 1}. {step.label}</div>
            <div><StatusPill status={step.status} label={step.statusLabel} /></div>
            <div style={{ fontSize: 12.5 }}>{step.summary}</div>
            {step.webUrl && (
              <a className="muted" href={step.webUrl} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 'auto' }}>
                打开原软件 <ExternalLink size={12} />
              </a>
            )}
          </div>
        ))}
      </section>

      {/* Detailed Step Cards */}
      <div className="grid-2">
        {/* Step 1: Subscription & Selection */}
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">1. 订阅与选种依据（MP Neo / Torra）</h2>
          </div>
          {!subStep?.sources?.length ? (
            <div className="muted">{subStep?.summary}</div>
          ) : (
            <div className="list">
              {subStep.sources.map((src, i) => (
                <div key={`${src.source}-${src.subscriptionId}-${i}`} className="list-row">
                  <div className="row-main">
                    <div className="row-title">
                      <span className="pill active">{src.sourceLabel}</span>
                      {src.seasonNumber && <span className="pill idle">第 {src.seasonNumber} 季</span>}
                    </div>
                    <div style={{ fontSize: 13, marginTop: 4 }}>规则：{src.ruleSummary}</div>
                    <div className="muted">来源：{src.originReason}</div>
                  </div>
                  <div className="row-actions">
                    {src.source === 'moviepilot' && src.subscriptionId && (
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={busy}
                        onClick={() => void triggerAction('mp_search', { subscriptionId: src.subscriptionId, title: data.media.title })}
                      >
                        <Search size={12} /> 触发此条搜索
                      </button>
                    )}
                    {src.webUrl && (
                      <a className="btn btn-sm" href={src.webUrl} target="_blank" rel="noreferrer">
                        打开 <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {!!subStep?.recentSelections?.length && (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ marginBottom: 6 }}>最近选种记录</div>
              <div className="list">
                {subStep.recentSelections.map((sel, idx) => (
                  <div key={idx} className="list-row">
                    <div className="row-main">
                      <div style={{ fontSize: 13, wordBreak: 'break-all' }}>{sel.torrentName}</div>
                      <div className="muted">站点：{sel.torrentSite || '未记录'} · {sel.seasons}{sel.episodes} · {sel.date}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!!subStep?.decisionLogs?.length && (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ marginBottom: 6 }}>Fluxa 决策说明</div>
              <div className="list">
                {subStep.decisionLogs.map((log) => (
                  <div key={log.id} className="list-row">
                    <div className="row-main">
                      <div className="row-title">
                        <StatusPill status={log.outcome === 'blocked' ? 'warn' : 'ok'} label={log.ruleName} />
                        <span className="muted">{log.sourceLabel}</span>
                      </div>
                      <div style={{ fontSize: 13, marginTop: 4 }}>{log.reasonText}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Step 2: qBittorrent */}
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">2. qBittorrent 下载与保种</h2>
            {qbStep?.webUrl && (
              <a className="btn btn-sm" href={qbStep.webUrl} target="_blank" rel="noreferrer">
                打开 qB <ExternalLink size={12} />
              </a>
            )}
          </div>
          {!qbStep?.tasks?.length ? (
            <div className="muted">{qbStep?.summary}</div>
          ) : (
            <div className="list">
              {qbStep.tasks.map((t) => (
                <div key={t.hash} className="list-row">
                  <div className="row-main">
                    <div className="row-title">
                      <StatusPill
                        status={t.status === 'missing_files' || t.status === 'error' ? 'error' : t.status === 'stalled' ? 'warn' : t.status === 'seeding' ? 'ok' : 'active'}
                        label={t.statusLabel}
                      />
                      <span style={{ wordBreak: 'break-all' }}>{t.name}</span>
                    </div>
                    <div className="muted" style={{ marginTop: 4 }}>
                      进度 {t.progressPercent}% · 分类：{t.category || '无'} · 路径：{t.savePath || '未返回'}
                    </div>
                    <div className="progress-track">
                      <div className={`progress-fill ${t.status === 'seeding' ? 'ok' : t.status === 'stalled' ? 'warn' : ''}`} style={{ width: `${t.progressPercent}%` }} />
                    </div>
                  </div>
                  <div className="row-actions">
                    {t.status === 'paused' || t.status === 'completed_paused' ? (
                      <button className="btn btn-sm" type="button" disabled={busy} onClick={() => void triggerAction('qb_resume', { hash: t.hash, name: t.name })}>
                        <Play size={12} /> 恢复
                      </button>
                    ) : (
                      <button className="btn btn-sm" type="button" disabled={busy} onClick={() => void triggerAction('qb_pause', { hash: t.hash, name: t.name })}>
                        <Pause size={12} /> {t.isSeeding ? '停止做种' : '暂停'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Step 3: 115 Upload (Torra) */}
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">3. 115 上传 / 秒传（Torra）</h2>
            {c115Step?.webUrl && (
              <a className="btn btn-sm" href={c115Step.webUrl} target="_blank" rel="noreferrer">
                打开 Torra <ExternalLink size={12} />
              </a>
            )}
          </div>
          <div className="muted" style={{ marginBottom: 8 }}>{c115Step?.summary}</div>
          {!!c115Step?.failedFiles?.length && (
            <div className="list">
              {c115Step.failedFiles.map((f, i) => (
                <div key={i} className="list-row list-row--error">
                  <div className="row-main">
                    <div style={{ fontWeight: 600, wordBreak: 'break-all' }}>{f.fileName}</div>
                    <div style={{ fontSize: 13, color: 'var(--err)' }}>{f.error}</div>
                  </div>
                  {f.taskKey && (
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={busy}
                      onClick={() => void triggerAction('torra_retry_upload', { taskKey: f.taskKey, targetItemId: f.targetItemId || '', fileName: f.fileName })}
                    >
                      重试此上传
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          {!!c115Step?.successFiles?.length && (
            <div className="list" style={{ marginTop: 8 }}>
              {c115Step.successFiles.map((f, i) => (
                <div key={i} className="list-row">
                  <div className="row-main">
                    <div style={{ wordBreak: 'break-all' }}>{f.fileName}</div>
                    {f.destPath && <div className="muted">目标：{f.destPath}</div>}
                  </div>
                  <StatusPill status="ok" label="已上传 115" />
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Step 4 & 5: Symedia (SA) & Emby */}
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">4. SA 整理归档 & 5. Emby 条目</h2>
            <div style={{ display: 'flex', gap: 6 }}>
              {saStep?.webUrl && (
                <a className="btn btn-sm" href={saStep.webUrl} target="_blank" rel="noreferrer">
                  打开 SA <ExternalLink size={12} />
                </a>
              )}
              {embyStep?.webUrl && (
                <a className="btn btn-sm" href={embyStep.webUrl} target="_blank" rel="noreferrer">
                  打开 Emby <ExternalLink size={12} />
                </a>
              )}
            </div>
          </div>
          <div className="list">
            <div className="list-row">
              <div className="row-main">
                <div className="row-title">
                  <span>Emby 媒体库核对</span>
                  <StatusPill status={embyStep?.status || 'unconfirmed'} label={embyStep?.statusLabel || '未确认'} />
                </div>
                <div className="muted" style={{ marginTop: 4 }}>{embyStep?.summary}</div>
              </div>
            </div>
            {!saStep?.records?.length ? (
              <div className="muted">SA 近期转移记录中暂无匹配条目（状态：{saStep?.statusLabel}）。</div>
            ) : (
              saStep.records.map((r, i) => (
                <div key={r.id || i} className={`list-row ${r.outcome === 'failed' || r.outcome === 'identify_failed' ? 'list-row--error' : ''}`}>
                  <div className="row-main">
                    <div className="row-title">
                      <StatusPill
                        status={r.outcome === 'archived' || r.outcome === 'replaced' ? 'ok' : r.outcome === 'rule_protected' ? 'protected' : r.outcome === 'unconfirmed' ? 'unconfirmed' : 'error'}
                        label={r.outcomeLabel}
                      />
                      <span>{r.seasons}{r.episodes}</span>
                      <span className="muted">{r.date}</span>
                    </div>
                    <div style={{ fontSize: 13, marginTop: 4 }}>{r.reasonText}</div>
                    {r.fileName && <div className="muted" style={{ wordBreak: 'break-all' }}>{r.fileName}</div>}
                  </div>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      {/* TV Episode Breakdown */}
      {!!data.episodes.length && (
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">分集状态核对</h2>
            <span className="muted">仅以集级证据判定，不用季级记录脑补单集完成</span>
          </div>
          <div className="list">
            {data.episodes.map((ep) => (
              <div key={ep.episodeLabel} className="list-row">
                <div className="row-main">
                  <div className="row-title">
                    <strong>{ep.episodeLabel}</strong>
                    <span>{ep.name}</span>
                    <span className="muted">{ep.airDate || '播出日期待定'}</span>
                  </div>
                </div>
                <StatusPill status={ep.state} label={ep.stateLabel} />
              </div>
            ))}
          </div>
        </section>
      )}

      {pendingAction && (
        <ConfirmImpactModal
          preview={pendingAction.preview}
          busy={busy}
          onCancel={() => setPendingAction(null)}
          onConfirm={() => void confirmPending()}
        />
      )}
    </div>
  );
}
