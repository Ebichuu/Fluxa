import { useEffect, useState } from 'react';
import { CheckCircle2, Plus, Save, Trash2 } from 'lucide-react';
import { api } from '../services/api';
import type { AppSettings, AuditRecord, RssSource, SiteOfficialGroup } from '../types/api';

export function SettingsPage() {
  const [tab, setTab] = useState<'services' | 'guardrails' | 'safety'>('services');
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [rssSources, setRssSources] = useState<RssSource[]>([]);
  const [audit, setAudit] = useState<AuditRecord[]>([]);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message: string }>>({});

  // New RSS source form
  const [rssName, setRssName] = useState('');
  const [rssSiteId, setRssSiteId] = useState('mteam');
  const [rssUrl, setRssUrl] = useState('');

  // New site-official-group form
  const [newSiteName, setNewSiteName] = useState('');
  const [newSiteDomain, setNewSiteDomain] = useState('');
  const [newSiteGroups, setNewSiteGroups] = useState('');

  const load = async () => {
    try {
      const [s, r, a] = await Promise.all([api.getSettings(), api.getRssSources(), api.getAudit()]);
      setSettings(s);
      setRssSources(r.items);
      setAudit(a.items);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '设置加载失败' });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setToast(null);
    try {
      const res = await api.saveSettings(settings);
      setSettings(res.settings);
      setToast({ tone: 'ok', text: '设置已保存并立即生效（无需重启容器）' });
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '保存失败' });
    } finally {
      setSaving(false);
    }
  };

  const testService = async (service: string) => {
    setTestResults((cur) => ({ ...cur, [service]: { ok: true, message: '测试中…' } }));
    try {
      const res = await api.testService(service);
      setTestResults((cur) => ({ ...cur, [service]: { ok: res.ok, message: res.message } }));
    } catch (err) {
      setTestResults((cur) => ({
        ...cur,
        [service]: { ok: false, message: err instanceof Error ? err.message : '连接失败' },
      }));
    }
  };

  const addRss = async () => {
    if (!rssName.trim() || !rssUrl.trim()) return;
    try {
      await api.saveRssSource({ name: rssName, siteId: rssSiteId, url: rssUrl, enabled: true });
      setRssName('');
      setRssUrl('');
      const r = await api.getRssSources();
      setRssSources(r.items);
      setToast({ tone: 'ok', text: '已保存私人 RSS 来源' });
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '保存 RSS 失败' });
    }
  };

  const removeRss = async (id: string) => {
    await api.deleteRssSource(id);
    const r = await api.getRssSources();
    setRssSources(r.items);
  };

  const addSiteGroup = () => {
    if (!settings || !newSiteName.trim()) return;
    const nextRow: SiteOfficialGroup = {
      siteId: newSiteName.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_') || `site_${Date.now()}`,
      siteName: newSiteName.trim(),
      domains: newSiteDomain.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean),
      officialGroups: newSiteGroups.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean),
    };
    setSettings({
      ...settings,
      guardrails: {
        ...settings.guardrails,
        siteOfficialGroups: [...settings.guardrails.siteOfficialGroups, nextRow],
      },
    });
    setNewSiteName('');
    setNewSiteDomain('');
    setNewSiteGroups('');
  };

  const updateSiteGroups = (index: number, groupsText: string) => {
    if (!settings) return;
    const copy = [...settings.guardrails.siteOfficialGroups];
    copy[index] = {
      ...copy[index],
      officialGroups: groupsText.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean),
    };
    setSettings({ ...settings, guardrails: { ...settings.guardrails, siteOfficialGroups: copy } });
  };

  const removeSiteGroup = (index: number) => {
    if (!settings) return;
    const copy = settings.guardrails.siteOfficialGroups.filter((_, i) => i !== index);
    setSettings({ ...settings, guardrails: { ...settings.guardrails, siteOfficialGroups: copy } });
  };

  if (!settings) return <div className="card muted">加载设置中…</div>;

  const setSvc = <K extends keyof AppSettings['services']>(
    svc: K,
    patch: Partial<AppSettings['services'][K]>
  ) => {
    setSettings({
      ...settings,
      services: {
        ...settings.services,
        [svc]: { ...settings.services[svc], ...patch },
      },
    });
  };

  const secretPlaceholder = (svc: keyof AppSettings['services'], key: string) =>
    settings.services[svc]._hasSecrets?.[key] ? '已保存（留空保持不变）' : '请输入';

  return (
    <div className="stack">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div className="tabs" style={{ marginBottom: 0 }}>
          <button className={`tab-btn ${tab === 'services' ? 'active' : ''}`} type="button" onClick={() => setTab('services')}>
            服务连接
          </button>
          <button className={`tab-btn ${tab === 'guardrails' ? 'active' : ''}`} type="button" onClick={() => setTab('guardrails')}>
            选种护栏与官组映射
          </button>
          <button className={`tab-btn ${tab === 'safety' ? 'active' : ''}`} type="button" onClick={() => setTab('safety')}>
            操作边界与留痕
          </button>
        </div>
        <button className="btn btn-primary" type="button" onClick={() => void save()} disabled={saving}>
          <Save size={15} /> {saving ? '保存中…' : '保存全部设置'}
        </button>
      </div>

      {toast && <div className={`banner ${toast.tone}`}>{toast.text}</div>}

      {tab === 'services' && (
        <div className="stack">
          {/* MoviePilot Neo */}
          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title">MoviePilot Neo</h2>
              <button className="btn btn-sm" type="button" onClick={() => void testService('moviepilot')}>测试连接</button>
            </div>
            {testResults.moviepilot && <div className={`banner ${testResults.moviepilot.ok ? 'ok' : 'error'}`}>{testResults.moviepilot.message}</div>}
            <div className="form-grid">
              <div className="field">
                <label>内网 API 地址（如 http://192.168.1.10:3000）</label>
                <input type="text" value={settings.services.moviepilot.baseUrl} onChange={(e) => setSvc('moviepilot', { baseUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>外网/快捷跳转地址（可选，留空同上）</label>
                <input type="text" value={settings.services.moviepilot.webUrl} onChange={(e) => setSvc('moviepilot', { webUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>API Token（优先使用）</label>
                <input type="password" placeholder={secretPlaceholder('moviepilot', 'apiToken')} value={settings.services.moviepilot.apiToken} onChange={(e) => setSvc('moviepilot', { apiToken: e.target.value })} />
              </div>
              <div className="field">
                <label>或 账号 / 密码（留空则仅用 Token）</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input type="text" placeholder="用户名" value={settings.services.moviepilot.username} onChange={(e) => setSvc('moviepilot', { username: e.target.value })} />
                  <input type="password" placeholder={secretPlaceholder('moviepilot', 'password')} value={settings.services.moviepilot.password} onChange={(e) => setSvc('moviepilot', { password: e.target.value })} />
                </div>
              </div>
            </div>
          </section>

          {/* Torra */}
          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title">Torra（订阅与 115 上传）</h2>
              <button className="btn btn-sm" type="button" onClick={() => void testService('torra')}>测试连接</button>
            </div>
            {testResults.torra && <div className={`banner ${testResults.torra.ok ? 'ok' : 'error'}`}>{testResults.torra.message}</div>}
            <div className="form-grid">
              <div className="field">
                <label>Torra 地址</label>
                <input type="text" value={settings.services.torra.baseUrl} onChange={(e) => setSvc('torra', { baseUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>快捷跳转地址（可选）</label>
                <input type="text" value={settings.services.torra.webUrl} onChange={(e) => setSvc('torra', { webUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>Torra Token</label>
                <input type="password" placeholder={secretPlaceholder('torra', 'token')} value={settings.services.torra.token} onChange={(e) => setSvc('torra', { token: e.target.value })} />
              </div>
              <div className="field">
                <label>或 账号 / 密码</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input type="text" placeholder="用户名" value={settings.services.torra.username} onChange={(e) => setSvc('torra', { username: e.target.value })} />
                  <input type="password" placeholder={secretPlaceholder('torra', 'password')} value={settings.services.torra.password} onChange={(e) => setSvc('torra', { password: e.target.value })} />
                </div>
              </div>
            </div>
          </section>

          {/* qBittorrent */}
          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title">qBittorrent（下载与保种）</h2>
              <button className="btn btn-sm" type="button" onClick={() => void testService('qbittorrent')}>测试连接</button>
            </div>
            {testResults.qbittorrent && <div className={`banner ${testResults.qbittorrent.ok ? 'ok' : 'error'}`}>{testResults.qbittorrent.message}</div>}
            <div className="form-grid">
              <div className="field">
                <label>qBittorrent WebUI 地址</label>
                <input type="text" value={settings.services.qbittorrent.baseUrl} onChange={(e) => setSvc('qbittorrent', { baseUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>快捷跳转地址（可选）</label>
                <input type="text" value={settings.services.qbittorrent.webUrl} onChange={(e) => setSvc('qbittorrent', { webUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>用户名</label>
                <input type="text" value={settings.services.qbittorrent.username} onChange={(e) => setSvc('qbittorrent', { username: e.target.value })} />
              </div>
              <div className="field">
                <label>密码</label>
                <input type="password" placeholder={secretPlaceholder('qbittorrent', 'password')} value={settings.services.qbittorrent.password} onChange={(e) => setSvc('qbittorrent', { password: e.target.value })} />
              </div>
            </div>
          </section>

          {/* Symedia (SA) */}
          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title">Symedia / SA（只读查看整理与规则拒绝，不改 SA 规则）</h2>
              <button className="btn btn-sm" type="button" onClick={() => void testService('symedia')}>测试连接</button>
            </div>
            {testResults.symedia && <div className={`banner ${testResults.symedia.ok ? 'ok' : 'error'}`}>{testResults.symedia.message}</div>}
            <div className="form-grid">
              <div className="field">
                <label>Symedia 地址</label>
                <input type="text" value={settings.services.symedia.baseUrl} onChange={(e) => setSvc('symedia', { baseUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>Token 或 账号密码</label>
                <input type="password" placeholder={secretPlaceholder('symedia', 'token')} value={settings.services.symedia.token} onChange={(e) => setSvc('symedia', { token: e.target.value })} />
              </div>
              <div className="field">
                <label>用户名（无 Token 时使用）</label>
                <input type="text" value={settings.services.symedia.username} onChange={(e) => setSvc('symedia', { username: e.target.value })} />
              </div>
              <div className="field">
                <label>密码</label>
                <input type="password" placeholder={secretPlaceholder('symedia', 'password')} value={settings.services.symedia.password} onChange={(e) => setSvc('symedia', { password: e.target.value })} />
              </div>
            </div>
          </section>

          {/* Emby & TMDB & Telegram */}
          <section className="card stack">
            <div className="card-header" style={{ marginBottom: 0 }}>
              <h2 className="card-title">Emby · TMDB · Telegram</h2>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-sm" type="button" onClick={() => void testService('emby')}>测 Emby</button>
                <button className="btn btn-sm" type="button" onClick={() => void testService('tmdb')}>测 TMDB</button>
                <button className="btn btn-sm" type="button" onClick={() => void testService('telegram')}>发 TG 测试</button>
              </div>
            </div>
            {(['emby', 'tmdb', 'telegram'] as const).map((k) =>
              testResults[k] ? <div key={k} className={`banner ${testResults[k].ok ? 'ok' : 'error'}`}>{k.toUpperCase()}：{testResults[k].message}</div> : null
            )}
            <div className="form-grid">
              <div className="field">
                <label>Emby 地址</label>
                <input type="text" value={settings.services.emby.baseUrl} onChange={(e) => setSvc('emby', { baseUrl: e.target.value })} />
              </div>
              <div className="field">
                <label>Emby API Key</label>
                <input type="password" placeholder={secretPlaceholder('emby', 'apiKey')} value={settings.services.emby.apiKey} onChange={(e) => setSvc('emby', { apiKey: e.target.value })} />
              </div>
              <div className="field">
                <label>TMDB API Key（v3）</label>
                <input type="password" placeholder={secretPlaceholder('tmdb', 'apiKey')} value={settings.services.tmdb.apiKey} onChange={(e) => setSvc('tmdb', { apiKey: e.target.value })} />
              </div>
              <div className="field">
                <label>或 TMDB Bearer Token（v4）</label>
                <input type="password" placeholder={secretPlaceholder('tmdb', 'bearerToken')} value={settings.services.tmdb.bearerToken} onChange={(e) => setSvc('tmdb', { bearerToken: e.target.value })} />
              </div>
              <div className="field">
                <label>Telegram Bot Token（可选）</label>
                <input type="password" placeholder={secretPlaceholder('telegram', 'botToken')} value={settings.services.telegram.botToken} onChange={(e) => setSvc('telegram', { botToken: e.target.value })} />
              </div>
              <div className="field">
                <label>Telegram Chat ID</label>
                <input type="text" value={settings.services.telegram.chatId} onChange={(e) => setSvc('telegram', { chatId: e.target.value })} />
              </div>
            </div>
          </section>
        </div>
      )}

      {tab === 'guardrails' && (
        <div className="stack">
          <section className="card stack">
            <h2 className="card-title">榜单防误订与官组护栏</h2>
            <div className="form-grid">
              <div className="field">
                <label>新片 / 新剧时间窗口（天）</label>
                <input
                  type="number"
                  value={settings.guardrails.newReleaseMaxAgeDays}
                  onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, newReleaseMaxAgeDays: Number(e.target.value) || 365 } })}
                />
              </div>
              <div className="field">
                <label>老剧新一季判定窗口（天）</label>
                <input
                  type="number"
                  value={settings.guardrails.newSeasonMaxAgeDays}
                  onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, newSeasonMaxAgeDays: Number(e.target.value) || 180 } })}
                />
              </div>
            </div>
            <label className="toggle-row">
              <span><strong>拦截榜单中的老电影</strong><div className="muted">超过时间窗口的老片不自动进入追更，需手动按「收藏」添加</div></span>
              <input
                type="checkbox"
                checked={settings.guardrails.blockOldMoviesFromCharts}
                onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, blockOldMoviesFromCharts: e.target.checked } })}
              />
            </label>
            <label className="toggle-row">
              <span><strong>拦截已完结老剧</strong><div className="muted">区分新剧与老剧新一季，防止榜单把旧剧整套订回来</div></span>
              <input
                type="checkbox"
                checked={settings.guardrails.blockFinishedOldSeriesFromCharts}
                onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, blockFinishedOldSeriesFromCharts: e.target.checked } })}
              />
            </label>
            <label className="toggle-row">
              <span><strong>老剧新一季仅订阅最新季</strong><div className="muted">不倒灌历史旧季，保持追更干净</div></span>
              <input
                type="checkbox"
                checked={settings.guardrails.onlySubscribeLatestSeasonForReturning}
                onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, onlySubscribeLatestSeasonForReturning: e.target.checked } })}
              />
            </label>
            <label className="toggle-row">
              <span><strong>拦截跨站转载组</strong><div className="muted">当来源站为 A、发布组属于 B 站官组时明确标红拦截</div></span>
              <input
                type="checkbox"
                checked={settings.guardrails.blockCrossSiteRepost}
                onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, blockCrossSiteRepost: e.target.checked } })}
              />
            </label>
            <label className="toggle-row">
              <span><strong>严格要求本站官组</strong><div className="muted">仅放行属于当前来源站官组列表的种子</div></span>
              <input
                type="checkbox"
                checked={settings.guardrails.requireSiteOfficialGroup}
                onChange={(e) => setSettings({ ...settings, guardrails: { ...settings.guardrails, requireSiteOfficialGroup: e.target.checked } })}
              />
            </label>
          </section>

          <section className="card stack">
            <h2 className="card-title">站点 → 本站官组映射表</h2>
            <div className="muted">按来源站分别维护本站官组，绝不使用跨站通用的发布组白名单。</div>
            <div className="list">
              {settings.guardrails.siteOfficialGroups.map((site, idx) => (
                <div key={site.siteId} className="list-row">
                  <div className="row-main">
                    <div className="row-title">
                      <strong>{site.siteName}</strong>
                      <span className="muted">{site.domains.join(', ')}</span>
                    </div>
                    <input
                      type="text"
                      style={{ marginTop: 6, width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}
                      value={site.officialGroups.join(', ')}
                      onChange={(e) => updateSiteGroups(idx, e.target.value)}
                    />
                  </div>
                  <button className="btn btn-sm" type="button" onClick={() => removeSiteGroup(idx)}>
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
            <div className="form-grid">
              <input type="text" placeholder="新站点名（如 彩虹岛）" value={newSiteName} onChange={(e) => setNewSiteName(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }} />
              <input type="text" placeholder="域名（可选，如 chdbits.co）" value={newSiteDomain} onChange={(e) => setNewSiteDomain(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }} />
              <input type="text" placeholder="本站官组（逗号分隔）" value={newSiteGroups} onChange={(e) => setNewSiteGroups(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }} />
              <button className="btn" type="button" onClick={addSiteGroup}><Plus size={14} /> 添加站点官组</button>
            </div>
          </section>

          <section className="card stack">
            <h2 className="card-title">私人 PT RSS 源（按需拉取校验官组）</h2>
            <div className="form-grid">
              <input type="text" placeholder="来源名称（如 M-Team 剧集）" value={rssName} onChange={(e) => setRssName(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }} />
              <select value={rssSiteId} onChange={(e) => setRssSiteId(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }}>
                {settings.guardrails.siteOfficialGroups.map((s) => (
                  <option key={s.siteId} value={s.siteId}>绑定站点：{s.siteName}</option>
                ))}
              </select>
              <input type="text" placeholder="RSS 订阅地址 (https://...)" value={rssUrl} onChange={(e) => setRssUrl(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)' }} />
              <button className="btn btn-primary" type="button" onClick={() => void addRss()}><Plus size={14} /> 添加 RSS 源</button>
            </div>
            {rssSources.length > 0 && (
              <div className="list">
                {rssSources.map((s) => (
                  <div key={s.id} className="list-row">
                    <div className="row-main">
                      <strong>{s.name}</strong> <span className="pill idle">{s.siteId}</span>
                      <div className="muted" style={{ wordBreak: 'break-all' }}>{s.url.slice(0, 60)}…</div>
                    </div>
                    <button className="btn btn-sm" type="button" onClick={() => void removeRss(s.id)}><Trash2 size={13} /></button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {tab === 'safety' && (
        <div className="stack">
          <section className="card stack">
            <h2 className="card-title"><CheckCircle2 size={17} /> 操作边界保护</h2>
            <label className="toggle-row">
              <span><strong>停止做种 / 暂停保种任务前二次确认</strong><div className="muted">明确展示种子名、保种状态和保存路径，防止误停保种</div></span>
              <input
                type="checkbox"
                checked={settings.safety.requireConfirmForSeedingActions}
                onChange={(e) => setSettings({ ...settings, safety: { ...settings.safety, requireConfirmForSeedingActions: e.target.checked } })}
              />
            </label>
            <label className="toggle-row">
              <span><strong>换源 / 洗版前二次确认</strong><div className="muted">始终不提供全库重扫、重启所有服务或删掉重下等黑盒按钮</div></span>
              <input
                type="checkbox"
                checked={settings.safety.requireConfirmForRewashOrSwitch}
                onChange={(e) => setSettings({ ...settings, safety: { ...settings.safety, requireConfirmForRewashOrSwitch: e.target.checked } })}
              />
            </label>
          </section>

          <section className="card stack">
            <h2 className="card-title">最近操作留痕</h2>
            {!audit.length ? (
              <div className="muted">暂无操作记录。</div>
            ) : (
              <div className="list">
                {audit.map((row) => (
                  <div key={row.id} className="list-row">
                    <div className="row-main">
                      <div className="row-title">
                        <span className={`pill ${row.status === 'success' ? 'ok' : 'error'}`}>{row.targetService}</span>
                        <span>{row.summary}</span>
                      </div>
                      {row.errorText && <div style={{ fontSize: 12.5, color: 'var(--err)' }}>{row.errorText}</div>}
                      <div className="muted">{row.createdAt}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
