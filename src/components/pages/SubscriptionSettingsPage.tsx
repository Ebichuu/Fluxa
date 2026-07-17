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
          <p className="ops-eyebrow">SUBSCRIPTION / POLICY</p>
          <h1>决定订阅如何运行，不混入系统连接与凭据设置。</h1>
          <p className="ops-deck">来源负责发现内容；订阅统一交给 Torra，下载、秒传和入库继续走同一条 PT 主线。</p>
        </div>
        <div className="ops-subscription-settings-guard">
          <span><Database size={15} />当前 PT 通道</span>
          <strong>{modeLabel}</strong>
          <small><ShieldCheck size={13} />真实外部写入仍受服务端闸门控制</small>
        </div>
      </section>

      <section className="ops-settings-grid ops-settings-grid--subscription">
        <SubscriptionHubSettings onModeChange={setModeLabel} />
      </section>
    </main>
  );
}
