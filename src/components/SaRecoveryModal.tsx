import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, Search, ShieldCheck, Wrench, X } from 'lucide-react';
import { api } from '../services/api';
import type { SaDiagnoseResponse, SaRecordRef, SaRecoveryJob, SaRecoveryProposal } from '../types/api';
import { StatusPill } from './StatusPill';

interface SaRecoveryModalProps {
  record: SaRecordRef;
  onClose: () => void;
  onUpdated: () => void;
}

const JOB_STATUS_LABELS: Record<string, { tone: string; label: string }> = {
  draft: { tone: 'idle', label: '方案待确认' },
  file_prepared: { tone: 'warn', label: '文件准备完成（待提交）' },
  submitted: { tone: 'active', label: '已提交（待处理）' },
  processing: { tone: 'active', label: '处理中（待核验）' },
  not_processed: { tone: 'warn', label: '超时尚未处理' },
  skipped_existing: { tone: 'protected', label: '已有版本，无需补库' },
  resolved: { tone: 'ok', label: '验证成功' },
  failed: { tone: 'error', label: '再次失败（已停止）' },
};

export function SaRecoveryModal({ record, onClose, onUpdated }: SaRecoveryModalProps) {
  const [diag, setDiag] = useState<SaDiagnoseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [manualTmdbId, setManualTmdbId] = useState('');
  const [manualType, setManualType] = useState<'tv' | 'movie'>(record.mediaType || 'tv');
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [transferTaskId, setTransferTaskId] = useState('');
  const [strategy, setStrategy] = useState<'sa_manual' | 'rename_and_manual'>('sa_manual');
  const [saveMapping, setSaveMapping] = useState(true);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<SaRecoveryJob | null>(null);
  const [feedback, setFeedback] = useState<{ tone: 'ok' | 'warn' | 'error'; text: string } | null>(null);

  const runDiagnose = useCallback(async (overrideTmdb = '', overrideType = '') => {
    setLoading(true);
    setFeedback(null);
    try {
      const res = await api.diagnoseSaFailure(record, overrideTmdb, overrideType);
      setDiag(res);
      setSelectedIdx(0);
      setJob(res.latestJob);
      if (res.savedMapping?.transferTaskId) {
        setTransferTaskId(res.savedMapping.transferTaskId);
      } else if (res.saTransferTasks.length > 0) {
        setTransferTaskId((cur) => cur || res.saTransferTasks[0].id);
      }
      if (res.saCapabilities?.openapiChecked && !res.saCapabilities?.supportsExplicitIdentify && res.fileCheck?.exists) {
        setStrategy('rename_and_manual');
      }
      if (res.noRecoveryNeeded) {
        onUpdated();
      }
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof Error ? err.message : '诊断失败' });
    } finally {
      setLoading(false);
    }
  }, [record]);

  useEffect(() => {
    void runDiagnose();
  }, [runDiagnose]);

  const activeProposal: SaRecoveryProposal | null = diag?.proposals[selectedIdx] || null;
  const activeExisting = activeProposal?.existingVersion?.exists ? activeProposal.existingVersion : null;
  const saMissingExplicitParams = Boolean(
    diag?.saCapabilities?.openapiChecked && !diag.saCapabilities.supportsExplicitIdentify
  );

  const submitRecovery = async () => {
    if (!diag || !activeProposal) return;
    setBusy(true);
    setFeedback(null);
    try {
      const res = await api.executeSaRecovery({
        saRecordId: diag.record.id,
        fileName: diag.record.fileName,
        srcPath: diag.record.src,
        failedDate: diag.record.date,
        failureReason: diag.record.reasonText,
        mediaType: activeProposal.mediaType,
        tmdbId: activeProposal.tmdbId,
        canonicalTitle: activeProposal.canonicalTitle,
        originalTitle: activeProposal.originalTitle,
        targetYear: activeProposal.year,
        sourceSeason: activeProposal.sourceSeason,
        sourceEpisode: activeProposal.sourceEpisode,
        targetSeason: activeProposal.targetSeason,
        targetEpisode: activeProposal.targetEpisode,
        seasonName: activeProposal.seasonName,
        transferTaskId,
        strategy,
        saveMapping,
        confirmed: true,
      });
      setJob(res.job);
      setFeedback({ tone: res.ok ? 'ok' : 'error', text: res.message });
      onUpdated();
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof Error ? err.message : '执行恢复失败' });
    } finally {
      setBusy(false);
    }
  };

  const runVerify = async () => {
    if (!job) return;
    setBusy(true);
    setFeedback(null);
    try {
      const res = await api.verifySaRecovery(job.id);
      setJob(res.job);
      setFeedback({ tone: res.resolved ? 'ok' : res.status === 'failed' ? 'error' : 'warn', text: res.message });
      if (res.resolved) onUpdated();
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof Error ? err.message : '核验失败' });
    } finally {
      setBusy(false);
    }
  };

  const jobBadge = job ? JOB_STATUS_LABELS[job.status] || { tone: 'idle', label: job.status } : null;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div
        className="modal"
        style={{ maxWidth: 680, maxHeight: '88vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Wrench size={18} color="var(--accent)" />
            <h3 style={{ margin: 0, fontSize: 16 }}>SA 识别失败诊断与安全恢复</h3>
          </div>
          <button className="icon-btn" type="button" onClick={onClose}><X size={15} /></button>
        </div>

        {feedback && <div className={`banner ${feedback.tone}`}>{feedback.text}</div>}

        {loading && !diag ? (
          <div className="muted">正在分析文件名、排查历史映射并检索 TMDB 季集依据…</div>
        ) : diag ? (
          <div className="stack" style={{ gap: 12 }}>
            {/* 1. Original SA failure facts */}
            <div className="impact-list">
              <div className="impact-row">
                <span className="muted">SA 记录 ID / 时间</span>
                <strong>#{diag.record.id || '-'} · {diag.record.date || '未知'}</strong>
              </div>
              <div className="impact-row">
                <span className="muted">源文件</span>
                <strong style={{ wordBreak: 'break-all', textAlign: 'right' }}>{diag.record.fileName}</strong>
              </div>
              <div className="impact-row">
                <span className="muted">失败原因</span>
                <strong style={{ color: 'var(--err)' }}>{diag.record.reasonText}</strong>
              </div>
              <div className="impact-row">
                <span className="muted">文件检查</span>
                <span style={{ textAlign: 'right' }}>{diag.fileCheck.detail}</span>
              </div>
              {diag.parsed.hasSpecialMarkers && (
                <div className="banner warn" style={{ marginTop: 4 }}>
                  <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                  检测到特殊区分标记（{diag.parsed.specialMarkers.join(' / ')}）：不会自动清理这些标记，也不会统一当单部电影处理。
                </div>
              )}
              {diag.savedMapping && (
                <div className="banner ok" style={{ marginTop: 4 }}>
                  <ShieldCheck size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                  已命中篇章限定映射：「{diag.savedMapping.arcTitle}」S{String(diag.savedMapping.sourceSeason).padStart(2, '0')} → TMDB #{diag.savedMapping.tmdbId}《{diag.savedMapping.canonicalTitle}》S{String(diag.savedMapping.targetSeason).padStart(2, '0')}
                </div>
              )}
            </div>

            {/* 2. Manual TMDB lookup override */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <select
                value={manualType}
                onChange={(e) => setManualType(e.target.value as 'tv' | 'movie')}
                style={{ padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }}
              >
                <option value="tv">剧集 / 动漫</option>
                <option value="movie">电影</option>
              </select>
              <input
                type="text"
                placeholder="手动指定 TMDB ID 核验季集（如 205493）"
                value={manualTmdbId}
                onChange={(e) => setManualTmdbId(e.target.value)}
                style={{ flex: 1, minWidth: 180, padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }}
              />
              <button
                className="btn btn-sm"
                type="button"
                disabled={loading || !manualTmdbId.trim()}
                onClick={() => void runDiagnose(manualTmdbId.trim(), manualType)}
              >
                <Search size={13} /> 按 TMDB ID 核对依据
              </button>
            </div>

            {/* 3. Candidate proposals with transparent evidence */}
            <div>
              <div className="muted" style={{ marginBottom: 6 }}>
                候选恢复方案（首次季号转换或多候选需你核对依据后确认一次）：
              </div>
              {!diag.proposals.length ? (
                <div className="banner warn">
                  <strong>无法可靠识别：</strong>
                  {diag.statusMessage || '未找到与源文件作品身份可靠匹配的 TMDB 候选。'}
                  {diag.filteredOutCount ? `（已过滤 ${diag.filteredOutCount} 个无关候选）` : ''}
                  <div className="muted" style={{ marginTop: 4 }}>
                    已尝试标题查询：{diag.searchedQueries.map((q) => q.query).slice(0, 4).join(' / ') || '无'}。请在上方手动输入 TMDB ID 核对季集，不会凑合生成无关方案。
                  </div>
                </div>
              ) : (
                <div className="list">
                  {diag.proposals.slice(0, 4).map((p, idx) => (
                    <div
                      key={`${p.tmdbId}-${p.targetSeason}-${idx}`}
                      className={`list-row list-row--clickable ${idx === selectedIdx ? 'list-row--warn' : ''}`}
                      style={idx === selectedIdx ? { borderColor: 'var(--accent)', background: 'var(--accent-soft)' } : undefined}
                      onClick={() => setSelectedIdx(idx)}
                    >
                      <div className="row-main">
                        <div className="row-title">
                          <input type="radio" checked={idx === selectedIdx} onChange={() => setSelectedIdx(idx)} />
                          <strong>{p.canonicalTitle}</strong>
                          <span className="pill idle">TMDB #{p.tmdbId}</span>
                          {p.mediaType === 'tv' && p.targetSeason && p.targetEpisode && (
                            <StatusPill
                              status={p.seasonChanged ? 'protected' : 'ok'}
                              label={
                                p.seasonChanged
                                  ? `S${String(p.sourceSeason).padStart(2, '0')}E${String(p.sourceEpisode).padStart(2, '0')} → S${String(p.targetSeason).padStart(2, '0')}E${String(p.targetEpisode).padStart(2, '0')}`
                                  : `S${String(p.targetSeason).padStart(2, '0')}E${String(p.targetEpisode).padStart(2, '0')}`
                              }
                            />
                          )}
                          {p.seasonName && <span className="muted">「{p.seasonName}」</span>}
                          {p.existingVersion?.exists ? (
                            <StatusPill
                              status={p.existingVersion.noRecoveryNeeded ? 'protected' : 'error'}
                              label={p.existingVersion.badgeLabel || '已有版本，无需补库'}
                            />
                          ) : p.alreadyInEmby ? (
                            <StatusPill status="error" label="Emby 已有（禁覆盖）" />
                          ) : null}
                          {p.sequelConflict?.hasConflict && <StatusPill status="error" label="疑似续集串片冲突" />}
                        </div>
                        <div style={{ marginTop: 4, fontSize: 12.5 }}>
                          {p.reasons.map((r, ri) => <div key={ri}>• {r}</div>)}
                        </div>
                        <div className="muted" style={{ marginTop: 4, wordBreak: 'break-all' }}>
                          规范名预览（保留全部规格）：{p.suggestedFileName}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 3.5 Existing preferred version callout (e.g. CR already exists vs failed LINETV) */}
            {activeExisting && (
              <div className="banner ok" style={{ borderColor: 'var(--accent)' }}>
                <ShieldCheck size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                {activeExisting.summary}
                {activeExisting.noRecoveryNeeded ? '（已自动从首页异常待办默认忽略）' : ''}
              </div>
            )}

            {/* 3.6 Real SA OpenAPI parameter check warning */}
            {saMissingExplicitParams && activeProposal && !activeExisting && (
              <div className="banner warn">
                <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                <strong>SA 真实接口参数核对提示：</strong>
                SA 的 <code>{diag.saCapabilities.manualEndpointPath || '/api/v1/transfer/manual'}</code> 仅声明参数 [
                {(diag.saCapabilities.manualBodyFields || ['items', 'transfer_task_id']).join(', ')}
                ]，不支持直接传 TMDB ID / 季集覆盖。原样提交不会让指定映射生效，请使用「保留规格改名（含 {'{tmdbid=...}'} 与目标季集）+ 单文件提交」或在 SA 网页端手动指定识别。
              </div>
            )}

            {/* 4. Execution options & boundaries */}
            {activeProposal && (
              <div className="card" style={{ padding: 12 }} >
                <div className="form-grid">
                  <div className="field">
                    <label>目标 SA 归档任务（仅处理此单文件）</label>
                    {diag.saTransferTasks.length > 0 ? (
                      <select value={transferTaskId} onChange={(e) => setTransferTaskId(e.target.value)}>
                        <option value="">请选择 SA 归档任务</option>
                        {diag.saTransferTasks.map((t) => (
                          <option key={t.id} value={t.id}>{t.name} (#{t.id})</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        placeholder="填写 SA transfer_task_id"
                        value={transferTaskId}
                        onChange={(e) => setTransferTaskId(e.target.value)}
                      />
                    )}
                  </div>
                  <div className="field">
                    <label>执行策略（优先 SA 原生接口，不改全局权重）</label>
                    <select value={strategy} onChange={(e) => setStrategy(e.target.value as 'sa_manual' | 'rename_and_manual')}>
                      <option value="sa_manual">优先：SA 单文件指定识别归档</option>
                      <option value="rename_and_manual">回退：保留规格改名 + 单文件提交（需本地挂载）</option>
                    </select>
                  </div>
                </div>
                {activeProposal.mediaType === 'tv' && (
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontSize: 13 }}>
                    <input type="checkbox" checked={saveMapping} onChange={(e) => setSaveMapping(e.target.checked)} />
                    <span>
                      保存限定到此篇章「{diag.parsed.arcTitle}」的映射，供后续同类集数复用（不影响其他篇章的 S01）
                    </span>
                  </label>
                )}
              </div>
            )}

            {/* 5. Recovery Job Lifecycle & 4-Point Strict Verification */}
            {job && (
              <div className="card" style={{ padding: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <strong>恢复任务 #{job.id}</strong>
                    {jobBadge && <StatusPill status={jobBadge.tone} label={jobBadge.label} />}
                    <span className="muted">尝试 {job.attemptCount}/{job.maxAttempts} 次</span>
                  </div>
                  <button className="btn btn-sm btn-primary" type="button" disabled={busy} onClick={() => void runVerify()}>
                    <RefreshCw size={13} /> 立即核验结果
                  </button>
                </div>
                <div style={{ marginTop: 8, fontSize: 13 }}>{job.verification.detail || job.lastError}</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                  <StatusPill status={job.verification.saSuccessConfirmed ? 'ok' : 'unconfirmed'} label="1. SA 新成功记录" />
                  <StatusPill status={job.verification.tmdbAndEpisodeMatched ? 'ok' : 'unconfirmed'} label="2. TMDB 与季集一致" />
                  <StatusPill status={job.verification.destVideoConfirmed ? 'ok' : 'unconfirmed'} label="3. 目标视频路径" />
                  <StatusPill status={job.verification.strmOrEmbyConfirmed ? 'ok' : 'unconfirmed'} label="4. STRM / Emby 核验" />
                </div>
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn" type="button" onClick={onClose}>关闭</button>
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy || !activeProposal || Boolean(activeProposal.alreadyInEmby) || Boolean(activeExisting?.exists) || job?.status === 'resolved'}
                onClick={() => void submitRecovery()}
              >
                <CheckCircle2 size={14} />{' '}
                {activeExisting?.noRecoveryNeeded
                  ? '已有版本，无需补库'
                  : busy
                    ? '提交中…'
                    : '确认方案并提交单文件恢复'}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
