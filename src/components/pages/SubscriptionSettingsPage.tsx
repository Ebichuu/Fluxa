import { useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import type { PageId } from '../layout/AppTopNav';
import { PageStatusHeader } from '../layout/PageStatusHeader';
import { SubscriptionHubSettings } from './SettingsPage';

interface SubscriptionSettingsPageProps {
  onNavigate: (page: PageId) => void;
}

export function SubscriptionSettingsPage({ onNavigate }: SubscriptionSettingsPageProps) {
  const [modeLabel, setModeLabel] = useState('读取中');

  return (
    <main className="work-page ops-page ops-page--subscription-settings">
      <PageStatusHeader
        actions={(
          <button className="ops-action-button" type="button" onClick={() => onNavigate('subscriptions')}>
            <ArrowLeft aria-hidden="true" size={14} />返回我的订阅
          </button>
        )}
        context="来源与时间"
        detail="真实外部写入受安全开关控制"
        status={modeLabel}
        title="订阅设置"
      />

      <section className="ops-settings-grid ops-settings-grid--subscription">
        <SubscriptionHubSettings onModeChange={setModeLabel} />
      </section>
    </main>
  );
}
