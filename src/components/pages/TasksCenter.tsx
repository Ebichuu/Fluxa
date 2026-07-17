import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, Download, ExternalLink, HardDrive, Pause, Play, RefreshCcw, Rss, Server } from 'lucide-react';
import { getActivityLogs, getTaskChain, runQbittorrentAction } from '../../services/api';
import type { QbittorrentAction } from '../../types/qbittorrent';
import type { TaskChainItem, TaskChainResponse, TaskChainState, TaskChainStep } from '../../types/taskChain';
import type { ActivityLogItem } from '../../types/operations';
import { formatSpeed, formatTimeAgo } from '../../utils/formatters';

type FilterName = '全部' | '进行中' | '等待中' | '卡住' | '已入库' | '尚未接到链路';

const filters: FilterName[] = ['全部', '进行中', '等待中', '卡住', '已入库', '尚未接到链路'];

const activityFilters = [
  { key: '', label: '全部' },
  { key: 'subscription', label: '订阅' },
  { key: 'push', label: 'Torra 推送' },
  { key: 'qbittorrent', label: 'qB' },
  { key: 'system', label: '系统' }
] as const;

const stateLabel: Record<TaskChainState, string> = {
  active: '进行中',
  blocked: '卡住',
  completed: '已入库',
  waiting: '等待中'
};

const stateClass: Record<TaskChainState, string> = {
  active: 'ops-task-state ops-task-state--active',
  blocked: 'ops-task-state ops-task-state--warn',
  completed: 'ops-task-state ops-task-state--done',
  waiting: 'ops-task-state'
};

function stepClass(step: TaskChainStep) {
  if (step.status === 'done') return 'ops-task-chain__step is-done';
  if (step.status === 'active') return 'ops-task-chain__step is-now';
  if (step.status === 'blocked') return 'ops-task-chain__step is-stuck';
  return 'ops-task-chain__step is-unknown';
}

function evidenceLabel(step: TaskChainStep) {
  if (step.evidence === 'verified') return step.source || '已验证';
  if (step.evidence === 'inferred') return '推断';
  return '证据不足';
}

function stepDisplayLabel(step: TaskChainStep) {
  if (step.key === 'download') return '正在下载';
  if (step.key === 'library') return '整理与入库';
  return step.label;
}

function currentDetail(item: TaskChainItem) {
  return item.steps.find((step) => step.key === item.currentStep)?.detail || '等待下一步证据';
}

function matchesFilter(item: TaskChainItem, filter: FilterName) {
  if (filter === '全部') return true;
  if (filter === '进行中') return item.state === 'active';
  if (filter === '等待中') return item.state === 'waiting';
  if (filter === '卡住') return item.state === 'blocked';
  if (filter === '已入库') return item.state === 'completed';
  return item.confidence === 'unlinked';
}

export function TasksCenter() {
  const [filter, setFilter] = useState<FilterName>('进行中');
  const [chain, setChain] = useState<TaskChainResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [visibleLimit, setVisibleLimit] = useState(12);
  const [pendingAction, setPendingAction] = useState<{ item: TaskChainItem; action: QbittorrentAction } | null>(null);
  const [actionBusy, setActionBusy] = useState('');
  const [actionFeedback, setActionFeedback] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [activityCategory, setActivityCategory] = useState('');
  const [activities, setActivities] = useState<ActivityLogItem[]>([]);
  const [activityError, setActivityError] = useState('');

  const loadChain = () => {
    setLoading(true);
    setError('');
    getTaskChain()
      .then(setChain)
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : '任务链读取失败');
        setChain(null);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadChain();
    const timer = window.setInterval(loadChain, 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadActivities = () => {
      getActivityLogs(activityCategory)
        .then((payload) => {
          if (!cancelled) {
            setActivities(payload.logs);
            setActivityError('');
          }
        })
        .catch(() => {
          if (!cancelled) {
            setActivities([]);
            setActivityError('活动日志暂不可用');
          }
        });
    };
    loadActivities();
    const timer = window.setInterval(loadActivities, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activityCategory]);

  useEffect(() => {
    setVisibleLimit(12);
  }, [filter]);

  const items = chain?.items ?? [];
  const filtered = useMemo(() => items.filter((item) => matchesFilter(item, filter)), [filter, items]);
  const visible = filtered.slice(0, visibleLimit);
  const counts = useMemo<Record<FilterName, number>>(() => ({
    全部: items.length,
    进行中: items.filter((item) => matchesFilter(item, '进行中')).length,
    等待中: items.filter((item) => item.state === 'waiting').length,
    卡住: items.filter((item) => item.state === 'blocked').length,
    已入库: items.filter((item) => item.state === 'completed').length,
    尚未接到链路: items.filter((item) => item.confidence === 'unlinked').length
  }), [items]);

  const completed115 = items.filter((item) => item.steps.find((step) => step.key === 'cloud115')?.status === 'done').length;
  const openTool = (url: string) => {
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  };

  const confirmQbAction = async () => {
    if (!pendingAction) return;
    const { item, action } = pendingAction;
    setActionBusy(item.id);
    setActionFeedback(null);
    try {
      const result = await runQbittorrentAction({
        action,
        hashes: item.sourceIds.qbHashes,
        taskId: item.id,
        title: item.title
      });
      setActionFeedback({
        tone: result.confirmed ? 'success' : 'error',
        message: result.confirmed
          ? `${action === 'pause' ? '已暂停' : '已恢复'} ${result.succeeded} 个下载${result.skipped ? `，跳过 ${result.skipped} 个` : ''}`
          : '动作已提交，但最新状态尚未确认，请查看刷新后的任务链'
      });
      setPendingAction(null);
      loadChain();
    } catch (reason) {
      setActionFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'qBittorrent 操作失败' });
      setPendingAction(null);
      loadChain();
    } finally {
      setActionBusy('');
    }
  };

  return (
    <main className="work-page ops-page ops-page--tasks">
      <section className="ops-hero ops-hero--tasks">
        <div>
          <p className="ops-eyebrow">任务中心 · 处理进度</p>
          <h1>媒体任务，现在进行到哪一步。</h1>
          <p className="ops-deck">订阅、下载、进入 115 和整理入库集中显示；匹配依据和原工具入口放在任务详情中。</p>
        </div>
        <div className="ops-task-hero-status">
          <span>{chain?.services.qb.connected ? 'PT 主链在线' : '部分服务未连接'}</span>
          <strong>{chain?.services.qb.connected ? formatSpeed(chain.services.qb.downloadSpeed) : '待连接'}</strong>
          <small>{chain ? `${chain.counts.active} 进行中 · ${chain.counts.blocked} 卡住` : '正在汇总任务证据'}</small>
        </div>
      </section>

      <section className="ops-task-summary" aria-label="任务状态摘要">
        <div><Rss size={16} /><span>已保存订阅</span><strong>{items.filter((item) => item.origin === 'subscription').length} 条主干</strong></div>
        <div><Download size={16} /><span>正在下载</span><strong>{chain ? `${chain.services.torra.total} 个订阅 · ${chain.services.qb.active} 个活跃下载` : '读取中'}</strong></div>
        <div><HardDrive size={16} /><span>已进入 115</span><strong>{completed115} 条有秒传或接管记录</strong></div>
        <div><Server size={16} /><span>整理与入库</span><strong>{chain ? `${chain.counts.completed} 个已完成` : '读取中'}</strong></div>
      </section>

      <section className="ops-panel ops-task-workbench">
        <header className="ops-task-toolbar">
          <div className="ops-task-tabs" role="tablist" aria-label="任务筛选">
            {filters.map((name) => (
              <button
                aria-selected={filter === name}
                className={filter === name ? 'ops-task-tab ops-task-tab--active' : 'ops-task-tab'}
                key={name}
                role="tab"
                type="button"
                onClick={() => setFilter(name)}
              >
                {name}<span className={name === '卡住' && counts[name] > 0 ? 'is-alert' : undefined}>{counts[name]}</span>
              </button>
            ))}
          </div>
          <div className="ops-task-toolbar__actions">
            <span>{chain ? `${filtered.length} / ${items.length} 条 · ${formatTimeAgo(chain.generatedAt)}` : '正在读取统一任务链'}</span>
            <button className="ops-icon-button" aria-label="刷新任务链" type="button" onClick={loadChain}><RefreshCcw size={16} /></button>
          </div>
        </header>

        {loading && !chain && <div className="ops-empty ops-task-empty">正在汇总下载、整理和入库状态…</div>}
        {!loading && error && <div className="ops-empty ops-task-empty">{error}</div>}
        {!loading && chain && visible.length === 0 && <div className="ops-empty ops-task-empty">这个筛选下暂时没有任务。</div>}
        {actionFeedback && (
          <div className={`ops-task-action-feedback ops-task-action-feedback--${actionFeedback.tone}`} role="status">
            {actionFeedback.message}
          </div>
        )}

        <div className="ops-task-list">
          {visible.map((item) => (
            <article className={item.state === 'blocked' ? 'ops-task-card ops-task-card--stuck' : 'ops-task-card'} key={item.id}>
              <div className="ops-task-card__head">
                <span className={stateClass[item.state]}>{stateLabel[item.state]}</span>
                <div>
                  <h2>{item.title}</h2>
                  <p>
                    PT · {item.mediaType === 'movie' ? '电影' : item.mediaType === 'tv' ? `剧集${item.seasonNumber ? ` S${String(item.seasonNumber).padStart(2, '0')}` : ''}` : '未识别媒体'}
                    {' · '}{item.confidence === 'strong' ? '已精确匹配' : item.confidence === 'fallback' ? '按标题推测' : '尚未接到链路'}
                  </p>
                </div>
                <strong>{item.progress}%</strong>
              </div>

              {item.state === 'blocked' && <div className="ops-task-alert"><AlertTriangle size={15} />{currentDetail(item)}</div>}

              <div className="ops-task-chain" aria-label="任务证据链">
                {item.steps.map((step, index) => (
                  <div className={stepClass(step)} key={step.key}>
                    <span>0{index + 1} · {evidenceLabel(step)}</span>
                    <strong>{stepDisplayLabel(step)}</strong>
                    <small>{step.detail}</small>
                  </div>
                ))}
              </div>

              <div className="ops-task-progress" aria-label={`链路进度 ${item.progress}%`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.progress}>
                <span style={{ '--progress': `${item.progress}%` } as React.CSSProperties} />
              </div>

              {(item.suggestion || item.embyIndexed || item.qbControl.total > 0) && (
                <footer className="ops-task-card__foot">
                  <span>{item.embyIndexed ? 'Emby 已索引' : currentDetail(item)}</span>
                  <div className="ops-task-card__actions">
                    {item.qbControl.total > 0 && (
                      <button
                        className="ops-action-button ops-action-button--primary"
                        disabled={Boolean(actionBusy)}
                        type="button"
                        onClick={() => setPendingAction({ item, action: item.qbControl.canPause ? 'pause' : 'resume' })}
                      >
                        {item.qbControl.canPause ? <Pause size={14} /> : <Play size={14} />}
                        {actionBusy === item.id ? '正在执行' : item.qbControl.canPause ? '暂停下载' : '恢复下载'}
                      </button>
                    )}
                    {item.suggestion && (
                      <button className="ops-action-button" disabled={!item.suggestion.url || Boolean(actionBusy)} type="button" onClick={() => openTool(item.suggestion!.url)}>
                        <ExternalLink size={14} />{item.suggestion.label}
                      </button>
                    )}
                  </div>
                </footer>
              )}
            </article>
          ))}
        </div>
        {filtered.length > visible.length && (
          <div className="ops-task-more">
            <span>已显示 {visible.length} / {filtered.length} 条</span>
            <button className="ops-action-button" type="button" onClick={() => setVisibleLimit((value) => value + 12)}>显示更多</button>
          </div>
        )}
      </section>

      <section className="ops-panel ops-activity-log">
        <header className="ops-task-toolbar">
          <div><small>操作记录</small><h2>最近活动</h2></div>
          <span>只读 · 最近 100 条</span>
        </header>
        <div className="ops-activity-filters" role="tablist" aria-label="活动类型">
          {activityFilters.map((item) => (
            <button
              aria-selected={activityCategory === item.key}
              className={activityCategory === item.key ? 'ops-task-tab ops-task-tab--active' : 'ops-task-tab'}
              key={item.key || 'all'}
              role="tab"
              type="button"
              onClick={() => setActivityCategory(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {activityError && <div className="ops-empty">{activityError}</div>}
        {!activityError && activities.length === 0 && <div className="ops-empty">当前分类还没有活动记录。</div>}
        <div className="ops-activity-list">
          {activities.map((item, index) => (
            <article className={`ops-activity-item is-${item.status}`} key={`${item.ts}-${item.action}-${index}`}>
              <span><Activity size={13} /></span>
              <div><strong>{item.message || item.action}</strong><small>{item.category} · {item.action}</small></div>
              <time>{item.time}</time>
            </article>
          ))}
        </div>
      </section>

      {pendingAction && (
        <div className="ops-confirm-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !actionBusy) setPendingAction(null);
        }}>
          <section aria-labelledby="qb-action-title" aria-modal="true" className="ops-confirm-dialog" role="dialog">
            <span className="ops-confirm-dialog__signal">下载任务 · {pendingAction.action === 'pause' ? '暂停' : '恢复'}</span>
            <h2 id="qb-action-title">
              {pendingAction.action === 'pause' ? '暂停' : '恢复'} {pendingAction.item.qbControl.total} 个关联下载？
            </h2>
            <p>
              这会{pendingAction.action === 'pause' ? '暂停' : '恢复'}《{pendingAction.item.title}》关联的全部 qBittorrent 下载。
              操作完成后会重新读取真实状态并写入活动日志。
            </p>
            <div className="ops-confirm-dialog__meta">
              <span>媒体任务</span><strong>{pendingAction.item.title}</strong>
              <span>关联下载</span><strong>{pendingAction.item.qbControl.total} 个</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" disabled={Boolean(actionBusy)} type="button" onClick={() => setPendingAction(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" disabled={Boolean(actionBusy)} type="button" onClick={confirmQbAction} autoFocus>
                {pendingAction.action === 'pause' ? <Pause size={14} /> : <Play size={14} />}
                {actionBusy ? '正在提交' : `确认${pendingAction.action === 'pause' ? '暂停' : '恢复'}`}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
