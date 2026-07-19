import { useEffect, useState } from 'react';
import { ArrowLeft, Database, Save, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { getSubscriptionAutomationSettings, updateSubscriptionAutomationSettings } from '../../services/api';
import type { SubscriptionAutomationSettings } from '../../types/subscriptions';
import type { PageId } from '../layout/AppTopNav';
import { SubscriptionHubSettings } from './SettingsPage';

interface SubscriptionSettingsPageProps {
  onNavigate: (page: PageId) => void;
}

function QualityWatchSettings() {
  const [settings, setSettings] = useState<SubscriptionAutomationSettings | null>(null);
  const [scheduleText, setScheduleText] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getSubscriptionAutomationSettings({ signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setSettings(payload);
        setScheduleText(payload.scheduleMinutes.join(', '));
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setMessage(reason instanceof Error ? reason.message : '质量观察设置加载失败');
      });
    return () => controller.abort();
  }, []);

  if (!settings) return <div className="ops-settings-card ops-settings-card--wide ops-empty">{message || '质量观察设置加载中…'}</div>;

  const save = () => {
    const scheduleMinutes = scheduleText.split(/[\s,，]+/).filter(Boolean).map(Number);
    if (scheduleMinutes.some((value) => !Number.isInteger(value))) {
      setMessage('检查时间点必须是整数分钟');
      return;
    }
    setSaving(true);
    setMessage('');
    updateSubscriptionAutomationSettings({
      enabled: settings.enabled,
      defaultWindowHours: settings.defaultWindowHours,
      scheduleMinutes,
      minIntervalMinutes: settings.minIntervalMinutes,
      hourlyLimit: settings.hourlyLimit,
      dailyLimit: settings.dailyLimit,
      batchSize: settings.batchSize
    })
      .then((payload) => {
        setSettings(payload);
        setScheduleText(payload.scheduleMinutes.join(', '));
        setMessage('质量观察设置已保存');
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '质量观察设置保存失败'))
      .finally(() => setSaving(false));
  };

  return (
    <section className="ops-settings-card ops-settings-card--wide sub-config quality-settings">
      <header className="ops-settings-card__head">
        <div><span><SlidersHorizontal size={16} /></span><div><small>阶段 6 · 质量观察</small><h2>追更洗版策略</h2></div></div>
        <strong>{settings.environmentEnabled ? '服务端闸门已开启' : '服务端闸门未开启'}</strong>
      </header>
      <div className="sub-config__toggles">
        <label><input checked={settings.enabled} type="checkbox" onChange={(event) => setSettings({ ...settings, enabled: event.target.checked })} />启用质量观察</label>
        <span className="quality-settings__readonly">下载闸门：{settings.downloadEnvironmentEnabled ? '已开启' : '未开启'}</span>
      </div>
      <div className="sub-config__row sub-config__row--pair">
        <label>默认观察窗口<select value={settings.defaultWindowHours} onChange={(event) => {
          const defaultWindowHours = Number(event.target.value) as 24 | 48;
          setSettings({ ...settings, defaultWindowHours });
          setScheduleText(defaultWindowHours === 24 ? '720, 1440' : '720, 1440, 2880');
        }}><option value={24}>24 小时</option><option value={48}>48 小时</option></select></label>
        <label>检查时间点（分钟）<input value={scheduleText} onChange={(event) => setScheduleText(event.target.value)} placeholder="720, 1440, 2880" /></label>
      </div>
      <div className="sub-config__row sub-config__row--pair">
        <label>最小间隔（分钟）<input min={60} max={1440} type="number" value={settings.minIntervalMinutes} onChange={(event) => setSettings({ ...settings, minIntervalMinutes: Number(event.target.value) })} /></label>
        <label>每小时限额<input min={1} max={1000} type="number" value={settings.hourlyLimit} onChange={(event) => setSettings({ ...settings, hourlyLimit: Number(event.target.value) })} /></label>
      </div>
      <div className="sub-config__row sub-config__row--pair">
        <label>每日限额<input min={1} max={1000} type="number" value={settings.dailyLimit} onChange={(event) => setSettings({ ...settings, dailyLimit: Number(event.target.value) })} /></label>
        <label>每轮批量<input min={2} max={3} type="number" value={settings.batchSize} onChange={(event) => setSettings({ ...settings, batchSize: Number(event.target.value) })} /></label>
      </div>
      <div className="sub-config__foot">
        <small>真实分析和下载仍由服务端闸门、Torra/qB 状态及幂等策略共同决定。</small>
        <button className="tool-link" disabled={saving} type="button" onClick={save}><Save size={14} />{saving ? '保存中…' : '保存质量观察设置'}</button>
        {message && <small role="status">{message}</small>}
      </div>
    </section>
  );
}

export function SubscriptionSettingsPage({ onNavigate }: SubscriptionSettingsPageProps) {
  const [modeLabel, setModeLabel] = useState('读取中');

  return (
    <main className="work-page ops-page ops-page--subscription-settings">
      <section className="ops-hero ops-hero--subscription-settings">
        <div>
          <button className="ops-back-link" type="button" onClick={() => onNavigate('subscriptions')}>
            <ArrowLeft aria-hidden="true" size={14} />
            返回我的订阅
          </button>
          <p className="ops-eyebrow">来源与时间</p>
          <h1>订阅设置</h1>
          <p className="ops-page-subtitle">设置系统自动发现哪些内容。</p>
          <p className="ops-deck">选择内容来源、执行时间和订阅规则；保存后由 PT 主线统一获取。</p>
        </div>
        <div className="ops-subscription-settings-guard">
          <span><Database size={15} />当前 PT 通道</span>
          <strong>{modeLabel}</strong>
          <small><ShieldCheck size={13} />真实外部写入仍受安全开关控制</small>
        </div>
      </section>

      <section className="ops-settings-grid ops-settings-grid--subscription">
        <SubscriptionHubSettings onModeChange={setModeLabel} />
        <QualityWatchSettings />
      </section>
    </main>
  );
}
