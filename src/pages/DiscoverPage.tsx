import { useCallback, useEffect, useState } from 'react';
import { Compass, Plus } from 'lucide-react';
import { api } from '../services/api';
import type { DiscoverItem, IntentMode, MediaType } from '../types/api';
import { StatusPill } from '../components/StatusPill';

interface DiscoverPageProps {
  onOpenMedia: (type: MediaType, tmdbId: string) => void;
  onGoSettings: () => void;
}

const CATEGORIES = [
  { id: 'trending_tv', label: '本周热门剧集' },
  { id: 'on_the_air', label: '正在播出剧集' },
  { id: 'trending_movie', label: '本周热门电影' },
  { id: 'now_playing', label: '正在上映电影' },
] as const;

export function DiscoverPage({ onOpenMedia, onGoSettings }: DiscoverPageProps) {
  const [category, setCategory] = useState<string>('trending_tv');
  const [items, setItems] = useState<DiscoverItem[]>([]);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ tone: 'ok' | 'error' | 'warn'; text: string } | null>(null);

  // Subscribe dialog
  const [selected, setSelected] = useState<DiscoverItem | null>(null);
  const [target, setTarget] = useState<'moviepilot' | 'torra'>('moviepilot');
  const [intent, setIntent] = useState<IntentMode>('follow');
  const [season, setSeason] = useState<string>('1');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async (cat: string) => {
    setLoading(true);
    try {
      const res = await api.discover(cat);
      setConfigured(res.configured);
      setItems(res.items);
    } catch (err) {
      setToast({ tone: 'error', text: err instanceof Error ? err.message : '发现榜单加载失败' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(category);
  }, [category, load]);

  const openSubscribeModal = (item: DiscoverItem) => {
    setSelected(item);
    setIntent(item.guardrail?.recommendedIntent || 'follow');
    setSeason(String(item.guardrail?.recommendedSeason || 1));
  };

  const submitSubscribe = async (overrideGuardrail = false) => {
    if (!selected) return;
    setSubmitting(true);
    setToast(null);
    try {
      const res = await api.subscribe({
        mediaType: selected.mediaType,
        tmdbId: selected.tmdbId,
        title: selected.title,
        year: selected.year,
        seasonNumber: selected.mediaType === 'tv' ? Number(season) || 1 : null,
        intent,
        target,
        sourceLabel: `发现榜单 (${CATEGORIES.find((c) => c.id === category)?.label || category})`,
        fromChart: true,
        overrideGuardrail,
      });
      setToast({ tone: 'ok', text: res.message || '已提交订阅' });
      setSelected(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '提交订阅失败';
      setToast({ tone: 'warn', text: msg });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="stack">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div className="tabs" style={{ marginBottom: 0 }}>
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              type="button"
              className={`tab-btn ${category === cat.id ? 'active' : ''}`}
              onClick={() => setCategory(cat.id)}
            >
              {cat.label}
            </button>
          ))}
        </div>
        <span className="muted">自动识别「新剧 / 老剧新一季 / 老片拦截」，推送到 MP Neo 或 Torra</span>
      </div>

      {toast && <div className={`banner ${toast.tone}`}>{toast.text}</div>}

      {!configured ? (
        <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <strong>需要配置 TMDB 才能浏览发现榜单</strong>
            <div className="muted">在设置中填写 TMDB API Key 或 Bearer Token 即可使用。</div>
          </div>
          <button className="btn btn-primary" type="button" onClick={onGoSettings}>前往设置</button>
        </div>
      ) : loading ? (
        <div className="card muted">正在加载榜单并评估防误订护栏…</div>
      ) : (
        <div className="media-grid">
          {items.map((item) => {
            const g = item.guardrail;
            return (
              <div key={item.mediaKey} className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', gap: 12 }}>
                  {item.posterUrl ? (
                    <img className="poster-thumb" src={item.posterUrl} alt={item.title} />
                  ) : (
                    <div className="poster-thumb" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <Compass size={20} className="muted" />
                    </div>
                  )}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontWeight: 650, cursor: 'pointer' }} onClick={() => onOpenMedia(item.mediaType, item.tmdbId)}>
                      {item.title}
                    </div>
                    <div className="muted">{item.year || '年份未知'} · 评分 {item.rating || '-'}</div>
                    {g && (
                      <div style={{ marginTop: 6 }}>
                        <StatusPill
                          status={!g.allowed ? 'warn' : g.category === 'returning_new_season' ? 'protected' : 'ok'}
                          label={g.categoryLabel}
                        />
                      </div>
                    )}
                  </div>
                </div>
                {g && <div className="muted" style={{ fontSize: 12.5 }}>{g.reasonText}</div>}
                <div style={{ display: 'flex', gap: 8, marginTop: 'auto' }}>
                  <button className="btn btn-sm" type="button" onClick={() => onOpenMedia(item.mediaType, item.tmdbId)}>
                    查链路
                  </button>
                  <button
                    className={`btn btn-sm ${g?.allowed === false ? '' : 'btn-primary'}`}
                    type="button"
                    onClick={() => openSubscribeModal(item)}
                  >
                    <Plus size={13} /> {g?.allowed === false ? '手动按收藏添加' : '一键订阅'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selected && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3 style={{ margin: 0 }}>订阅《{selected.title}》</h3>
            {selected.guardrail && (
              <div className={`banner ${selected.guardrail.allowed ? 'ok' : 'warn'}`}>
                <strong>{selected.guardrail.ruleName}：</strong>
                {selected.guardrail.reasonText}
              </div>
            )}
            <div className="field">
              <label>推送目标（沿用目标软件现有规则）</label>
              <select value={target} onChange={(e) => setTarget(e.target.value as 'moviepilot' | 'torra')}>
                <option value="moviepilot">MoviePilot Neo</option>
                <option value="torra">Torra</option>
              </select>
            </div>
            <div className="field">
              <label>订阅意图</label>
              <select value={intent} onChange={(e) => setIntent(e.target.value as IntentMode)}>
                <option value="follow">追更（跟播及时可看）</option>
                <option value="collect">收藏（按规则定版收齐）</option>
              </select>
            </div>
            {selected.mediaType === 'tv' && (
              <div className="field">
                <label>订阅季号（老剧新一季已自动锁定最新季，避免倒灌旧季）</label>
                <input type="number" min={1} max={99} value={season} onChange={(e) => setSeason(e.target.value)} />
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn" type="button" onClick={() => setSelected(null)} disabled={submitting}>取消</button>
              <button
                className="btn btn-primary"
                type="button"
                disabled={submitting}
                onClick={() => void submitSubscribe(selected.guardrail?.allowed === false)}
              >
                {submitting ? '提交中…' : selected.guardrail?.allowed === false ? '确认按收藏放行并订阅' : '确认提交订阅'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
