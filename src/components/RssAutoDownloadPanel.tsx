import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Download, Play, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../services/api';
import type { RssAutoOverviewResponse, RssAutoRule } from '../types/api';
import { StatusPill } from './StatusPill';

const ITEM_STATUS_MAP: Record<string, { tone: string; label: string }> = {
  confirmed_in_qb: { tone: 'ok', label: '已确认入 qB' },
  submitted_unconfirmed: { tone: 'warn', label: '已提交待确认' },
  quality_rejected: { tone: 'warn', label: '质量不达标跳过' },
  insufficient_evidence: { tone: 'warn', label: '依据不足跳过' },
  quality_outperformed: { tone: 'idle', label: '同集择优落选' },
  quality_existing_sufficient: { tone: 'protected', label: '已有同档/更优版本' },
  duplicate_in_qb: { tone: 'idle', label: 'qB 已有（Hash 去重）' },
  duplicate_seen: { tone: 'idle', label: '已处理过（Hash 去重）' },
  baseline_skipped: { tone: 'idle', label: '首启基线跳过' },
  failed: { tone: 'error', label: '添加失败' },
};

export function RssAutoDownloadPanel() {
  const [data, setData] = useState<RssAutoOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState('');
  const [toast, setToast] = useState<{ tone: 'ok' | 'warn' | 'error'; text: string } | null>(null);

  // Add form
  const [siteId, setSiteId] = useState('audiences');
  const [officialGroup, setOfficialGroup] = useState('ADWeb');
  const [qualityPolicyId, setQualityPolicyId] = useState('anime_web');
  const [url, setUrl] = useState('');
  const [category, setCategory] = useState('');
  const [savePath, setSavePath] = useState('');
  const [intervalMin, setIntervalMin] = useState('5');
  const [enabled, setEnabled] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getRssAutoOverview();
      setData(res);
      if (res.sites.length && !res.sites.some((s) => s.siteId === siteId)) {
        const first = res.sites[0];
        setSiteId(first.siteId);
        setOfficialGroup(first.officialGroups[0] || '');
      }
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '加载 RSS 自动下载配置失败' });
    } finally {
      setLoading(false);
    }
  }, [siteId]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedSite = data?.sites.find((s) => s.siteId === siteId);

  const handleSiteChange = (nextSiteId: string) => {
    setSiteId(nextSiteId);
    const found = data?.sites.find((s) => s.siteId === nextSiteId);
    setOfficialGroup(found?.officialGroups[0] || '');
  };

  const handleCategoryPick = (catName: string) => {
    setCategory(catName);
    const found = data?.qbCategories.find((c) => c.name === catName);
    if (found?.savePath && !savePath) {
      setSavePath(found.savePath);
    }
  };

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setBusyId('create');
    setToast(null);
    try {
      const res = await api.saveRssAutoRule({
        siteId,
        siteName: selectedSite?.siteName || siteId,
        officialGroup,
        qualityPolicyId,
        url,
        category,
        savePath,
        enabled,
        pollIntervalMinutes: Number(intervalMin) || 5,
      });
      setUrl('');
      setToast({ tone: 'ok', text: res.message || '已保存并建立初始基线' });
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '保存失败' });
    } finally {
      setBusyId('');
    }
  };

  const runRuleNow = async (rule: RssAutoRule) => {
    setBusyId(rule.id);
    setToast(null);
    try {
      const res = await api.runRssAutoRuleNow(rule.id);
      setToast({ tone: res.ok ? 'ok' : 'error', text: res.message });
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '执行检查失败' });
    } finally {
      setBusyId('');
    }
  };

  const toggleRule = async (rule: RssAutoRule) => {
    setBusyId(rule.id);
    try {
      await api.toggleRssAutoRule(rule.id, !rule.enabled);
      await load();
    } finally {
      setBusyId('');
    }
  };

  const deleteRule = async (rule: RssAutoRule) => {
    setBusyId(rule.id);
    try {
      await api.deleteRssAutoRule(rule.id);
      setToast({ tone: 'ok', text: `已删除「${rule.siteName} · ${rule.officialGroup}」配置（未触碰 qB 已有任务）` });
      await load();
    } finally {
      setBusyId('');
    }
  };

  const changeRulePolicy = async (rule: RssAutoRule, nextPolicyId: string) => {
    setBusyId(rule.id);
    try {
      await api.toggleRssAutoRule(rule.id, rule.enabled, nextPolicyId);
      setToast({ tone: 'ok', text: `已更新「${rule.siteName} · ${rule.officialGroup}」的质量策略` });
      await load();
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '切换质量策略失败' });
    } finally {
      setBusyId('');
    }
  };

  const ruleMap = new Map((data?.rules || []).map((r) => [r.id, r]));
  const selectedPolicy = data?.qualityPolicies?.find((p) => p.id === qualityPolicyId);

  return (
    <div className="stack">
      {toast && <div className={`banner ${toast.tone}`}>{toast.text}</div>}
      {data && !data.qbConfigured && (
        <div className="banner warn">qBittorrent 尚未配置：可先保存官组 RSS 规则，配置好 qB 连接后自动开始推送新条目。</div>
      )}

      {/* Add Site + Official-Group RSS Rule */}
      <section className="card stack">
        <div className="card-header" style={{ marginBottom: 0 }}>
          <h2 className="card-title"><Download size={17} color="var(--accent)" /> 新增「站点 + 本站官组」RSS 自动下载</h2>
          <span className="muted">指定官组 RSS → 质量策略过滤 &amp; 同集择优 → Hash 去重 → 推给 qB 并读回核验</span>
        </div>

        <form className="stack" style={{ gap: 12 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="field">
              <label>站点</label>
              <select value={siteId} onChange={(e) => handleSiteChange(e.target.value)}>
                {(data?.sites || []).map((s) => (
                  <option key={s.siteId} value={s.siteId}>
                    #{s.priority} {s.siteName}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>本站官组（限定该站对应官组）</label>
              <input
                type="text"
                list="site-official-groups-list"
                placeholder="如 ADWeb / MTeam / FRDS"
                value={officialGroup}
                onChange={(e) => setOfficialGroup(e.target.value)}
                required
              />
              <datalist id="site-official-groups-list">
                {(selectedSite?.officialGroups || []).map((g) => (
                  <option key={g} value={g} />
                ))}
              </datalist>
            </div>
          </div>

          <div className="field">
            <label>绑定质量策略（不合格或依据不足的条目自动跳过，不因官组发布就全量下载）</label>
            <select value={qualityPolicyId} onChange={(e) => setQualityPolicyId(e.target.value)}>
              {(data?.qualityPolicies || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {selectedPolicy && (
              <div className="muted" style={{ marginTop: 4 }}>{selectedPolicy.summary}</div>
            )}
          </div>

          <div className="field">
            <label>该官组专属 RSS 地址（Passkey 仅存于后端，前端脱敏显示）</label>
            <input
              type="text"
              placeholder="https://example.org/torrentrss.php?..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
          </div>

          <div className="form-grid">
            <div className="field">
              <label>qB 分类（沿用现有 qB 分类，可留空）</label>
              <input
                type="text"
                list="qb-categories-list"
                placeholder="例如 10-电影 / 02-日漫"
                value={category}
                onChange={(e) => handleCategoryPick(e.target.value)}
              />
              <datalist id="qb-categories-list">
                {(data?.qbCategories || []).map((c) => (
                  <option key={c.name} value={c.name}>{c.savePath ? `${c.name} (${c.savePath})` : c.name}</option>
                ))}
              </datalist>
            </div>
            <div className="field">
              <label>qB 保存路径（可选，留空沿用 qB 分类默认路径）</label>
              <input
                type="text"
                placeholder="例如 /downloads/anime"
                value={savePath}
                onChange={(e) => setSavePath(e.target.value)}
              />
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                检查间隔（分钟）
                <input
                  type="number"
                  min={1}
                  max={1440}
                  value={intervalMin}
                  onChange={(e) => setIntervalMin(e.target.value)}
                  style={{ width: 68, padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-2)' }}
                />
              </label>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                立即启用（首次仅建基线，不补下历史）
              </label>
            </div>
            <button className="btn btn-primary" type="submit" disabled={busyId === 'create'}>
              <Plus size={14} /> {busyId === 'create' ? '保存并建基线中…' : '保存官组 RSS 规则'}
            </button>
          </div>
        </form>
      </section>

      {/* Configured Rules List */}
      <section className="card">
        <div className="card-header">
          <h2 className="card-title">已配置的官组 RSS 规则（{data?.rules.length || 0}）</h2>
          <button className="btn btn-sm" type="button" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={13} /> 刷新
          </button>
        </div>
        {!data?.rules.length ? (
          <div className="muted">尚未配置官组 RSS 规则。</div>
        ) : (
          <div className="list">
            {data.rules.map((rule) => (
              <div key={rule.id} className={`list-row ${rule.lastStatus === 'error' ? 'list-row--error' : ''}`}>
                <div className="row-main">
                  <div className="row-title">
                    <StatusPill status={rule.enabled ? 'ok' : 'idle'} label={rule.enabled ? '已启用' : '已停用'} />
                    <strong>{rule.siteName}</strong>
                    <span className="pill active">{rule.officialGroup}</span>
                    <StatusPill
                      status={rule.qualityPolicyId && rule.qualityPolicyId !== 'none' ? 'protected' : 'idle'}
                      label={`策略：${rule.qualityPolicyName || rule.qualityPolicyId || '仅哈希去重'}`}
                    />
                    {rule.category && <span className="pill idle">分类：{rule.category}</span>}
                    <span className="pill idle">每 {rule.pollIntervalMinutes} 分钟</span>
                    <span className="pill ok">已入 qB {rule.addedCount} 条</span>
                  </div>
                  <div className="muted" style={{ marginTop: 4 }}>
                    RSS：{rule.urlMasked}{rule.savePath ? ` · 保存路径：${rule.savePath}` : ''}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 13 }}>
                    最近检查：{rule.lastCheckedAt || '尚未检查'} · {rule.lastMessage || (rule.baselineInitialized ? '已建基线，等待新条目' : '等待首次建立基线')}
                  </div>
                </div>
                <div className="row-actions">
                  <select
                    aria-label="切换质量策略"
                    value={rule.qualityPolicyId || 'none'}
                    disabled={busyId === rule.id}
                    onChange={(e) => void changeRulePolicy(rule, e.target.value)}
                    style={{
                      padding: '4px 8px',
                      borderRadius: 8,
                      border: '1px solid var(--border)',
                      background: 'var(--surface-2)',
                      color: 'var(--text)',
                      fontSize: 12.5,
                    }}
                  >
                    {(data?.qualityPolicies || []).map((p) => (
                      <option key={p.id} value={p.id}>{p.shortName}</option>
                    ))}
                  </select>
                  <button
                    className="btn btn-sm btn-primary"
                    type="button"
                    disabled={busyId === rule.id}
                    onClick={() => void runRuleNow(rule)}
                  >
                    <Play size={12} /> {busyId === rule.id ? '检查中…' : '立即检查'}
                  </button>
                  <button className="btn btn-sm" type="button" disabled={busyId === rule.id} onClick={() => void toggleRule(rule)}>
                    {rule.enabled ? '停用' : '启用'}
                  </button>
                  <button className="btn btn-sm" type="button" disabled={busyId === rule.id} onClick={() => void deleteRule(rule)}>
                    <Trash2 size={12} /> 删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Recent Processing Results */}
      <section className="card">
        <div className="card-header">
          <h2 className="card-title">最近添加与去重记录</h2>
          <span className="muted">不自动删除旧任务、不停止做种、不修改 SA 规则</span>
        </div>
        {!data?.items.length ? (
          <div className="muted">暂无处理记录。</div>
        ) : (
          <div className="list">
            {data.items.slice(0, 30).map((item) => {
              const badge = ITEM_STATUS_MAP[item.status] || { tone: 'idle', label: item.status };
              const parentRule = ruleMap.get(item.ruleId);
              return (
                <div key={item.id} className={`list-row ${item.status === 'failed' ? 'list-row--error' : ''}`}>
                  <div className="row-main">
                    <div className="row-title">
                      <StatusPill status={badge.tone} label={badge.label} />
                      {parentRule && <span className="pill idle">{parentRule.siteName} · {parentRule.officialGroup}</span>}
                      <span style={{ wordBreak: 'break-all' }}>{item.title}</span>
                    </div>
                    <div className="muted" style={{ marginTop: 4 }}>
                      {item.createdAt}
                      {item.qualityMeta?.specs?.scopeLabel ? ` · 范围 ${item.qualityMeta.specs.scopeLabel}` : ''}
                      {item.qualityMeta?.specs?.specSummary ? ` · ${item.qualityMeta.specs.specSummary}` : ''}
                      {item.torrentHash ? ` · Hash ${item.torrentHash.slice(0, 12)}…` : ''}
                      {item.qbCategory ? ` · 分类 ${item.qbCategory}` : ''}
                    </div>
                    {item.errorText && <div style={{ marginTop: 3, fontSize: 12.5 }}>{item.errorText}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
