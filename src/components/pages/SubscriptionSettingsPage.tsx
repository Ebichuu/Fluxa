import { useState } from 'react';
import { ArrowLeft, Database, ShieldCheck } from 'lucide-react';
import type { PageId } from '../layout/AppTopNav';
import { SubscriptionHubSettings } from './SettingsPage';

interface SubscriptionSettingsPageProps {
  onNavigate: (page: PageId) => void;
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
          <p className="ops-eyebrow">订阅设置 · 来源与时间</p>
          <h1>设置系统自动发现哪些内容。</h1>
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
      </section>
    </main>
  );
}
