import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Download,
  Edit3,
  Plus,
  PanelRightOpen,
  RefreshCcw,
  Search,
  Send,
  ServerCog,
  ShieldCheck,
  Trash2,
  X
} from 'lucide-react';
import {
  applyRssMatchCleanup,
  backfillRssIdentities,
  deleteRssSource,
  getAutomationAction,
  getRssArtifactGroups,
  getRssMatchGroups,
  getRssSeedItem,
  getRssSeedItems,
  getRssSources,
  previewRssArtifactExactDownload,
  previewRssMatchCleanup,
  previewRssResourceDownload,
  runRssMatcher,
  saveRssSource,
  startRssArtifactExactDownload,
  startRssMatchAnalysis,
  startRssMatchDownload,
  startRssResourceDownload,
  testRssSource
} from '../../services/api';
import { writeUrlQuery, type UrlHistoryMode } from '../../app/urlState';
import type { AutomationAction, RssExactDownloadPreview, RssIdentityStatus, RssLibrarySummary, RssMatch, RssMatchCleanupPreview, RssMatchGroup, RssMatchGroupListResponse, RssResourceDownloadPreview, RssSeedItem, RssSource, RssSourceInput } from '../../types/rssSeedLibrary';
import {
  classifyRssResourceScope,
  countRssResourceScopes,
  rssMatchMethodLabel,
  rssResourceScopeLabel,
  rssResourceScopeSummaryText
} from '../../types/rssSeedLibrary';
import { formatTimeAgo } from '../../utils/formatters';
import { createIdempotencyKey } from '../../utils/idempotency';
import { rssSeedFollowStateLabel } from '../../utils/rssProcessingState';
import { ConfirmDialog } from '../layout/ConfirmDialog';
import { PosterImage } from '../layout/PosterImage';
import { RelativeTime } from '../status/RelativeTime';
import type { AppNavigate } from '../layout/AppTopNav';

type WindowFilter = '' | '1h' | '24h' | '7d';
type ResourceView = 'new' | 'identify' | 'scoring' | 'upgrades' | 'cleanup';
type FollowStateFilter = '' | 'linked' | 'unlinked';
type MediaTypeFilter = '' | 'movie' | 'tv' | 'unknown';
const RSS_INTERVAL_PRESETS = [1, 3, 5] as const;
const rssPageSize = 50;
const matchActionPollIntervalMs = 1500;
const matchActionPollAttempts = 70;

interface RssLibraryUrlState {
  view: ResourceView;
  query: string;
  sourceId: string;
  identityStatus: RssIdentityStatus;
  followState: FollowStateFilter;
  windowFilter: WindowFilter;
  offset: number;
  publishedDate: string;
  subscriptionId: string;
  tmdbId: string;
  mediaType: '' | 'movie' | 'tv';
  resourceType: MediaTypeFilter;
  contextTitle: string;
  seasonNumber: number | null;
  episodeNumber: number | null;
  matchId: string;
}

type RssResourceContext = Pick<RssLibraryUrlState,
  'publishedDate' | 'subscriptionId' | 'tmdbId' | 'mediaType' | 'contextTitle' | 'seasonNumber' | 'episodeNumber' | 'matchId'
>;

const emptyResourceContext: RssResourceContext = {
  publishedDate: '',
  subscriptionId: '',
  tmdbId: '',
  mediaType: '',
  contextTitle: '',
  seasonNumber: null,
  episodeNumber: null,
  matchId: ''
};

function readRssLibraryUrlState(location: Location = window.location): RssLibraryUrlState {
  const params = new URLSearchParams(location.search);
  const windowValue = params.get('window');
  const identityValue = params.get('identityStatus');
  const followStateValue = params.get('followState');
  const viewValue = params.get('view');
  const mediaTypeValue = params.get('mediaType');
  const resourceTypeValue = params.get('resourceType');
  const publishedDateValue = params.get('publishedDate') ?? '';
  const parsedOffset = Number(params.get('offset'));
  const parsedSeason = Number(params.get('seasonNumber'));
  const parsedEpisode = Number(params.get('episodeNumber'));
  return {
    view: ['identify', 'scoring', 'upgrades', 'cleanup'].includes(viewValue ?? '') ? viewValue as ResourceView : 'new',
    query: params.get('q') ?? '',
    sourceId: params.get('sourceId') ?? '',
    identityStatus: ['identified', 'conflict', 'unidentified'].includes(identityValue ?? '')
      ? identityValue as RssIdentityStatus
      : '',
    followState: followStateValue === 'linked' || followStateValue === 'unlinked' ? followStateValue : '',
    windowFilter: windowValue === null
      ? publishedDateValue ? '' : '24h'
      : windowValue === 'all'
        ? ''
        : ['1h', '24h', '7d'].includes(windowValue)
          ? windowValue as WindowFilter
          : '24h',
    offset: Number.isInteger(parsedOffset) && parsedOffset >= 0
      ? Math.floor(parsedOffset / rssPageSize) * rssPageSize
      : 0,
    publishedDate: /^\d{4}-\d{2}-\d{2}$/.test(publishedDateValue) ? publishedDateValue : '',
    subscriptionId: params.get('subscriptionId')?.trim() || '',
    tmdbId: /^\d{1,24}$/.test(params.get('tmdbId') ?? '') ? params.get('tmdbId') ?? '' : '',
    mediaType: mediaTypeValue === 'movie' || mediaTypeValue === 'tv' ? mediaTypeValue : '',
    resourceType: ['movie', 'tv', 'unknown'].includes(resourceTypeValue ?? '')
      ? resourceTypeValue as MediaTypeFilter
      : '',
    contextTitle: params.get('title')?.trim().slice(0, 240) || '',
    seasonNumber: Number.isInteger(parsedSeason) && parsedSeason > 0 ? parsedSeason : null,
    episodeNumber: Number.isInteger(parsedEpisode) && parsedEpisode > 0 ? parsedEpisode : null,
    matchId: params.get('matchId')?.trim().slice(0, 80) || ''
  };
}

function writeRssLibraryUrlState(state: RssLibraryUrlState, mode: UrlHistoryMode = 'replace') {
  writeUrlQuery({
    view: state.view === 'new' ? null : state.view,
    q: state.query || null,
    sourceId: state.sourceId || null,
    identityStatus: state.identityStatus || null,
    followState: state.followState || null,
    window: state.windowFilter === '24h' ? null : state.windowFilter || 'all',
    offset: state.offset > 0 ? state.offset : null,
    publishedDate: state.publishedDate || null,
    subscriptionId: state.subscriptionId || null,
    tmdbId: state.tmdbId || null,
    mediaType: state.mediaType || null,
    resourceType: state.resourceType || null,
    title: state.contextTitle || null,
    seasonNumber: state.seasonNumber,
    episodeNumber: state.episodeNumber,
    matchId: state.matchId || null
  }, mode);
}

function isPresetInterval(value: number) {
  return RSS_INTERVAL_PRESETS.some((preset) => preset === value);
}

function isValidInterval(value: number) {
  return Number.isInteger(value) && value >= 1 && value <= 1440;
}

const emptySummary: RssLibrarySummary = {
  enabled: false,
  sources: 0,
  activeSources: 0,
  errorSources: 0,
  items: 0,
  lastSuccessAt: ''
};

const defaultForm: RssSourceInput = {
  name: '',
  feedUrl: '',
  enabled: true,
  intervalMinutes: 5,
  retentionDays: 7,
  allowHttp: false
};

function sizeLabel(bytes: number) {
  if (!bytes) return '大小未知';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function episodeLabel(item: RssSeedItem) {
  if (item.mediaType === 'movie') return '电影';
  if (item.mediaType !== 'tv') return '类型待确认';
  const season = item.seasonNumber == null ? '' : `S${String(item.seasonNumber).padStart(2, '0')}`;
  const episodes = item.episodeStart == null
    ? ''
    : item.episodeEnd && item.episodeEnd !== item.episodeStart
      ? `E${String(item.episodeStart).padStart(2, '0')}-${String(item.episodeEnd).padStart(2, '0')}`
      : `E${String(item.episodeStart).padStart(2, '0')}`;
  return `${season}${episodes}` || '剧集';
}

function identityLabel(status: RssSeedItem['identityStatus']) {
  if (status === 'identified') return '已识别';
  if (status === 'conflict') return '候选冲突';
  return '未识别';
}

function identitySourceLabel(value: string) {
  const labels: Record<string, string> = {
    rss_field: 'RSS 结构化字段',
    rss_description: 'RSS 简介',
    rss_link: 'RSS 公开链接',
    subscription_match: 'Fluxa 追更唯一匹配',
    torra_subscription_match: 'Torra 订阅唯一匹配'
  };
  return value.split(',').filter(Boolean).map((source) => labels[source] || source).join('、') || '暂无可靠来源';
}

function identityConfidenceLabel(value: string) {
  const labels: Record<string, string> = {
    strong: '高（结构化证据）',
    explicit: '高（公开明确证据）',
    fallback: '保守匹配',
    conflict: '存在冲突'
  };
  return labels[value] || value || '暂无';
}

function exactTimeLabel(value: string) {
  if (!value) return '未知';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `北京时间 ${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(parsed)}`;
}

function identityBackfillLabel(summary: RssLibrarySummary) {
  if (!summary.identityBackfillRan || summary.identityBackfillStatus === 'not_run') {
    return `身份回填尚未运行；当前 ${summary.items} 条种子尚不能证明识别链路已处理。`;
  }
  if (summary.identityBackfillStatus === 'failed') {
    return `身份回填最近运行失败；已扫描 ${summary.lastIdentityBackfillScanned ?? 0} 条，剩余 ${summary.lastIdentityBackfillRemaining ?? summary.items} 条。`;
  }
  return [
    `最近回填 ${summary.lastIdentityBackfillAt ? formatTimeAgo(summary.lastIdentityBackfillAt) : '时间未知'}`,
    `扫描 ${summary.lastIdentityBackfillScanned ?? 0} 条`,
    `识别 ${summary.lastIdentityBackfillIdentified ?? 0} 条`,
    `冲突 ${summary.lastIdentityBackfillConflicts ?? 0} 条`,
    `未变化 ${summary.lastIdentityBackfillUnchanged ?? 0} 条`,
    `剩余 ${summary.lastIdentityBackfillRemaining ?? summary.items} 条`
  ].join(' · ');
}

function matcherLabel(summary: RssLibrarySummary) {
  if (!summary.matcherRan || summary.matcherStatus === 'not_run') return `匹配器尚未运行；当前 ${summary.items} 条种子还没有匹配结果。`;
  if (summary.matcherStatus === 'failed') return `匹配器最近运行失败；上次扫描 ${summary.lastMatchScanned ?? 0} 条，未将结果视为成功。`;
  return `最近匹配 ${summary.lastMatchAt ? formatTimeAgo(summary.lastMatchAt) : '时间未知'} · 扫描 ${summary.lastMatchScanned ?? 0} 条 · 命中 ${summary.lastMatchCreated ?? 0} 条`;
}

function matchActionLabel(action: AutomationAction | undefined, matchStatus: RssMatch['status']) {
  if (!action) {
    if (matchStatus === 'expired') return '匹配记录已过期，无需处理';
    if (matchStatus === 'ignored') return '已检查，没有更合适的版本';
    if (matchStatus === 'confirmed') return '已处理完成';
    if (matchStatus === 'triggered') return '正在恢复上次检查结果';
    return '等待检查';
  }
  if (action.status === 'failed') return action.error?.message || '操作失败，请稍后重试';
  if (action.status === 'cancelled') return '操作已取消';
  if (action.type === 'rss-exact-download' || action.type === 'rss-resource-download') {
    if (action.status !== 'succeeded') return 'qB 正在确认下载任务';
    return action.result?.alreadyPresent === true ? 'qB 已存在相同资源' : 'qB 已确认下载任务';
  }
  if (action.status !== 'succeeded') {
    return action.type === 'rewash-download' ? 'Torra 正在接收下载任务' : 'Torra 正在检查可用版本';
  }
  if (action.type === 'rewash-download') return 'Torra 已接收下载任务，后续进度可在任务中心查看';
  const selectedCount = action.result?.selectedCount ?? 0;
  return selectedCount > 0 ? `检查完成，发现 ${selectedCount} 个更合适的版本` : '检查完成，当前没有更合适的版本';
}

const shadowReasonLabels: Record<string, string> = {
  torra_unavailable: 'Torra 当前不可读',
  torra_rule_read_failed: 'Torra 规则当前不可读',
  match_context_missing: '候选上下文不完整',
  subscription_missing: '关联订阅不存在',
  watch_unit_missing: '季集观察目标不存在',
  torra_subscription_missing: 'Torra 订阅绑定暂未确认',
  identity_unconfirmed: '作品或季集身份暂未确认',
  torra_subscription_owner_mismatch: '订阅所有权不一致',
  candidate_scope_mismatch: '候选季集范围不兼容',
  artifact_scope_unconfirmed: '候选范围暂未确认',
  artifact_owner_conflict: '同一候选存在所有权冲突',
  subscription_media_type_unconfirmed: '订阅媒体类型暂未确认',
  subscription_category_unconfirmed: '订阅分类暂未确认',
  rule_not_found: '没有找到唯一适用规则',
  rule_ambiguous: '适用规则不唯一',
  candidate_title_missing: '候选标题不完整',
  candidate_size_invalid: '候选大小无效',
  candidate_size_unconfirmed: '候选大小暂未确认',
  baseline_identity_unconfirmed: '当前版本身份暂未确认',
  baseline_version_unconfirmed: '当前版本文件暂未确认',
  baseline_artifact_conflict: '当前版本存在多个冲突文件',
  baseline_size_conflict: '当前版本文件大小存在冲突',
  higher_scored_candidate: '已被同集更高分候选取代',
  rule_pattern_missing: '规则字段不完整',
  rule_pattern_invalid: '规则表达式无法安全解析',
  rule_group_invalid: '规则分组无法安全解析',
  rule_filter_invalid: '规则筛选条件无法安全解析',
  rule_weight_invalid: '规则权重无法安全解析',
  rule_score_invalid: '规则分值无法安全解析',
  version_entries_missing: '版本控制条件不完整',
  version_entry_unsupported: '版本控制条件暂不支持 Torra 规则评分',
  version_entry_invalid: '版本控制条件无效',
  version_attribute_unsupported: '版本字段暂不支持 Torra 规则评分',
  version_match_mode_unsupported: '版本匹配方式暂不支持 Torra 规则评分',
  version_condition_values_missing: '版本条件值不完整',
  version_condition_values_invalid: '版本条件值无效',
  version_fields_unconfirmed: '候选版本字段暂未确认',
  always_override_unsupported: '强制覆盖规则不参与 Torra 规则评分'
};

function scoreLabel(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '暂未确认';
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

function shadowEvaluationLabel(match: RssMatch) {
  if (match.evaluationStatus === 'scored' && typeof match.candidateScore === 'number') {
    const candidate = `候选 ${scoreLabel(match.candidateScore)} 分`;
    const versionNote = match.candidateSummary?.versionState === 'unconfirmed'
      ? ' · 版本条件待下载后确认'
      : '';
    if (['initial_candidate', 'best_available'].includes(match.decision || '')) return `${candidate} · 当前首轮最高分${versionNote}`;
    if (['waiting_baseline', 'best_waiting_baseline'].includes(match.decision || '')) return `${candidate} · 等待当前版本基线${versionNote}`;
    if (match.decision === 'rule_rejected') return `${candidate} · Torra 规则未接受${versionNote}`;
    if (match.decision === 'superseded') return `${candidate} · 已被更高分候选取代${versionNote}`;
    if (typeof match.baselineScore !== 'number') return `${candidate}${versionNote}`;
    const baseline = `当前版本 ${scoreLabel(match.baselineScore)} 分`;
    if (['upgrade_available', 'current_best'].includes(match.decision || '')) {
      return `${candidate} · ${baseline} · 提升 ${scoreLabel(match.candidateScore - match.baselineScore)} 分${versionNote}`;
    }
    if (match.decision === 'same_score') return `${candidate} · 与当前版本同分${versionNote}`;
    if (match.decision === 'lower_score') return `${candidate} · 低于${baseline}${versionNote}`;
    return `${candidate} · ${baseline}${versionNote}`;
  }
  if (match.evaluationStatus === 'blocked') {
    const reason = shadowReasonLabels[match.evaluationReason || ''] || '评分条件暂未确认';
    return `评分暂未确认 · ${reason}`;
  }
  return '等待 Torra 规则评分';
}

function candidateGroupScoreLabel(group: RssMatchGroup) {
  if (group.coveredUnits?.length) {
    const score = typeof group.bestCandidateScore === 'number'
      ? `${scoreLabel(group.bestCandidateScore)} 分`
      : '评分暂未确认';
    const conclusion = group.winsAllCoveredUnits
      ? '当前唯一冠军'
      : group.state === 'partially_best'
        ? '仅部分集胜出，禁止提交'
        : group.state === 'protected'
          ? '当前版本已保护'
          : '继续观察';
    return `覆盖 ${group.coveredUnits.length} 集 · ${score} · ${conclusion}`;
  }
  if (group.state === 'initial_best') {
    const candidate = typeof group.bestCandidateScore === 'number'
      ? `首轮最高 ${scoreLabel(group.bestCandidateScore)} 分`
      : '首轮最高分暂未确认';
    return `${candidate} · ${group.candidateCount} 个版本`;
  }
  const baseline = typeof group.baselineScore === 'number'
    ? `当前版本 ${scoreLabel(group.baselineScore)} 分`
    : '当前版本暂未确认';
  const candidate = typeof group.bestCandidateScore === 'number'
    ? `最佳候选 ${scoreLabel(group.bestCandidateScore)} 分`
    : '最佳候选暂未确认';
  return `${baseline} · ${candidate} · ${group.candidateCount} 个版本`;
}

function seedProcessingStateLabel(
  followState: RssSeedItem['followState'] | undefined,
  match: RssMatch | undefined,
  action: AutomationAction | undefined,
  resourceDownloadStatus = ''
) {
  if (action?.type === 'rss-resource-download') {
    if (action.status === 'failed') return 'qB 提交失败';
    if (action.status === 'cancelled') return '下载已取消';
    if (action.status !== 'succeeded') return '正在提交 qB';
    return action.result?.alreadyPresent === true ? 'qB 已存在相同资源' : 'qB 已接收';
  }
  if (resourceDownloadStatus === 'succeeded') return 'qB 已接收';
  if (['claimed', 'submitted', 'polling'].includes(resourceDownloadStatus)) return '正在提交 qB';
  const resourceState = rssSeedFollowStateLabel(followState, Boolean(match));
  if (resourceState) return resourceState;
  if (!match) return '未关联';
  if (action?.type === 'rewash-download' || match.status === 'confirmed') return 'Torra 已接收';
  if (action && !['succeeded', 'failed', 'cancelled'].includes(action.status)) return '分析中';
  if (match.evaluationStatus === 'scored' && typeof match.candidateScore === 'number') {
    return `Torra 规则评分 ${scoreLabel(match.candidateScore)} 分`;
  }
  if (match.evaluationStatus === 'blocked') return '评分暂未确认';
  return '等待评分';
}

function seedPriorityReason(item: RssSeedItem, scope: ReturnType<typeof classifyRssResourceScope>) {
  const identityKnown = item.identityStatus === 'identified';
  const rangeKnown = scope === 'explicit_episode' || scope === 'explicit_multi_episode';
  if (identityKnown && rangeKnown) return '精确身份优先 · 明确季集优先';
  if (identityKnown) return '精确身份优先';
  if (rangeKnown) return '明确季集优先';
  return '暂无优先证据；最终下载推荐只来自 Torra 分析评分';
}

export function RssSeedLibraryPage({ onNavigate }: { onNavigate: AppNavigate }) {
  const [initialUrlState] = useState(readRssLibraryUrlState);
  const [resourceView, setResourceView] = useState<ResourceView>(initialUrlState.view);
  const [resourceContext, setResourceContext] = useState<RssResourceContext>({
    publishedDate: initialUrlState.publishedDate,
    subscriptionId: initialUrlState.subscriptionId,
    tmdbId: initialUrlState.tmdbId,
    mediaType: initialUrlState.mediaType,
    contextTitle: initialUrlState.contextTitle,
    seasonNumber: initialUrlState.seasonNumber,
    episodeNumber: initialUrlState.episodeNumber,
    matchId: initialUrlState.matchId
  });
  const [sources, setSources] = useState<RssSource[]>([]);
  const [summary, setSummary] = useState<RssLibrarySummary>(emptySummary);
  const [items, setItems] = useState<RssSeedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [matchGroups, setMatchGroups] = useState<RssMatchGroup[]>([]);
  const [matchesTotal, setMatchesTotal] = useState(0);
  const [decisionTotal, setDecisionTotal] = useState(0);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [matchGroupCounts, setMatchGroupCounts] = useState<NonNullable<RssMatchGroupListResponse['counts']>>({
    total: 0,
    initialBest: 0,
    waitingBaseline: 0,
    monitoringRss: 0,
    upgradeAvailable: 0,
    protected: 0,
    needsCleanup: 0,
    blocked: 0
  });
  const [matchesOffset, setMatchesOffset] = useState(0);
  const [matchesLoading, setMatchesLoading] = useState(false);
  const [matchActions, setMatchActions] = useState<Record<string, AutomationAction>>({});
  const [resourceDownloadActions, setResourceDownloadActions] = useState<Record<string, AutomationAction>>({});
  const [exactPreviews, setExactPreviews] = useState<Record<string, RssExactDownloadPreview>>({});
  const [matchPollTimedOut, setMatchPollTimedOut] = useState<Record<string, boolean>>({});
  const [matchBusy, setMatchBusy] = useState('');
  const [downloadTarget, setDownloadTarget] = useState<{ match: RssMatch; analysis: AutomationAction } | null>(null);
  const [exactDownloadTarget, setExactDownloadTarget] = useState<{ group: RssMatchGroup; preview: RssExactDownloadPreview } | null>(null);
  const [resourceDownloadTarget, setResourceDownloadTarget] = useState<{ item: RssSeedItem; preview: RssResourceDownloadPreview } | null>(null);
  const [cleanupPreview, setCleanupPreview] = useState<RssMatchCleanupPreview | null>(null);
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [query, setQuery] = useState(initialUrlState.query);
  const [sourceId, setSourceId] = useState(initialUrlState.sourceId);
  const [identityStatus, setIdentityStatus] = useState<RssIdentityStatus>(initialUrlState.identityStatus);
  const [followState, setFollowState] = useState<FollowStateFilter>(initialUrlState.followState);
  const [mediaTypeFilter, setMediaTypeFilter] = useState<MediaTypeFilter>(initialUrlState.resourceType);
  const [windowFilter, setWindowFilter] = useState<WindowFilter>(initialUrlState.windowFilter);
  const [offset, setOffset] = useState(initialUrlState.offset);
  const [urlRevision, setUrlRevision] = useState(0);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState<{ tone: 'ok' | 'error'; message: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<RssSource | null>(null);
  const [form, setForm] = useState<RssSourceInput>(defaultForm);
  const [saving, setSaving] = useState(false);
  const matches = useMemo(
    () => matchGroups.flatMap((group) => group.candidates),
    [matchGroups]
  );
  const [testingSourceId, setTestingSourceId] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<RssSource | null>(null);
  const [detailItem, setDetailItem] = useState<RssSeedItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [identityBackfillBusy, setIdentityBackfillBusy] = useState(false);
  const [matcherRunBusy, setMatcherRunBusy] = useState(false);
  const itemsRequestRef = useRef<AbortController | null>(null);
  const matchesRequestRef = useRef<AbortController | null>(null);
  const matchPollRefs = useRef(new Map<string, AbortController>());
  const resourcePollRefs = useRef(new Map<string, AbortController>());
  const detailRequestRef = useRef<AbortController | null>(null);
  const pageSize = rssPageSize;

  const syncUrlState = (patch: Partial<RssLibraryUrlState>) => {
    writeRssLibraryUrlState({
      view: resourceView,
      ...resourceContext,
      query,
      sourceId,
      identityStatus,
      followState,
      resourceType: mediaTypeFilter,
      windowFilter,
      offset,
      ...patch
    });
  };

  const loadSources = () => getRssSources().then((payload) => {
    setSources(payload.items);
    setSummary(payload.summary);
  });

  const loadItems = async (input: Partial<RssLibraryUrlState> = {}) => {
    itemsRequestRef.current?.abort();
    const controller = new AbortController();
    itemsRequestRef.current = controller;
    setItemsLoading(true);
    const requestedState: RssLibraryUrlState = {
      view: input.view ?? resourceView,
      publishedDate: input.publishedDate ?? resourceContext.publishedDate,
      subscriptionId: input.subscriptionId ?? resourceContext.subscriptionId,
      tmdbId: input.tmdbId ?? resourceContext.tmdbId,
      mediaType: input.mediaType ?? resourceContext.mediaType,
      resourceType: input.resourceType ?? mediaTypeFilter,
      contextTitle: input.contextTitle ?? resourceContext.contextTitle,
      seasonNumber: input.seasonNumber ?? resourceContext.seasonNumber,
      episodeNumber: input.episodeNumber ?? resourceContext.episodeNumber,
      matchId: input.matchId ?? resourceContext.matchId,
      query: input.query ?? query,
      sourceId: input.sourceId ?? sourceId,
      identityStatus: input.identityStatus ?? identityStatus,
      followState: input.followState ?? followState,
      windowFilter: input.windowFilter ?? windowFilter,
      offset: input.offset ?? offset
    };
    try {
      const payload = await getRssSeedItems(
        {
          query: requestedState.query || (
            requestedState.contextTitle && (requestedState.subscriptionId || requestedState.tmdbId)
              ? requestedState.contextTitle
              : ''
          ),
          sourceId: requestedState.sourceId,
          window: requestedState.windowFilter,
          identityStatus: requestedState.identityStatus,
          followState: requestedState.followState,
          reviewState: requestedState.view === 'identify' ? 'follow_needs_review' : '',
          publishedDate: requestedState.publishedDate,
          subscriptionId: requestedState.subscriptionId,
          tmdbId: requestedState.tmdbId,
          mediaType: requestedState.mediaType || undefined,
          seasonNumber: requestedState.seasonNumber ?? undefined,
          episodeNumber: requestedState.episodeNumber ?? undefined,
          resourceType: requestedState.resourceType || undefined,
          limit: pageSize,
          offset: requestedState.offset
        },
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      setItems(payload.items);
      setTotal(payload.total);
      setOffset(payload.offset);
      writeRssLibraryUrlState({ ...requestedState, offset: payload.offset });
    } catch (reason) {
      if (!controller.signal.aborted) {
        setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '资源中心读取失败' });
      }
    } finally {
      if (!controller.signal.aborted) setItemsLoading(false);
    }
  };

  const loadMatches = async (nextOffset = matchesOffset, view = resourceView): Promise<Record<string, AutomationAction>> => {
    matchesRequestRef.current?.abort();
    const controller = new AbortController();
    matchesRequestRef.current = controller;
    setMatchesLoading(true);
    try {
      const hasScopedMatchContext = Boolean(resourceContext.subscriptionId || resourceContext.matchId);
      const scopedMediaType = hasScopedMatchContext ? resourceContext.mediaType || undefined : undefined;
      const scopedSeasonNumber = hasScopedMatchContext ? resourceContext.seasonNumber ?? undefined : undefined;
      const scopedEpisodeNumber = hasScopedMatchContext ? resourceContext.episodeNumber ?? undefined : undefined;
      const groupLoader = view === 'cleanup' ? getRssMatchGroups : getRssArtifactGroups;
      const [payload, globalPayload, decisionPayload] = await Promise.all([
        groupLoader({
          groupState: view === 'upgrades' ? 'upgrade_available' : undefined,
          groupScope: view === 'cleanup' ? 'decision' : 'scoreable',
          subscriptionId: resourceContext.subscriptionId || undefined,
          mediaType: scopedMediaType,
          seasonNumber: scopedSeasonNumber,
          episodeNumber: scopedEpisodeNumber,
          matchId: resourceContext.matchId || undefined,
          limit: 10,
          offset: nextOffset
        }, { signal: controller.signal }),
        hasScopedMatchContext || view === 'cleanup'
          ? getRssArtifactGroups({ limit: 1, offset: 0 }, { signal: controller.signal })
          : Promise.resolve(null),
        view === 'cleanup'
          ? Promise.resolve(null)
          : getRssMatchGroups({ groupScope: 'decision', limit: 1, offset: 0 }, { signal: controller.signal })
      ]);
      if (controller.signal.aborted) return {};
      if (!Array.isArray(payload.groups)) {
        throw new Error('候选组接口暂未可用，请确认前后端版本一致');
      }
      setMatchGroups(payload.groups);
      setExactPreviews({});
      setMatchesTotal(payload.total);
      setDecisionTotal(view === 'cleanup' ? payload.total : decisionPayload?.total ?? 0);
      const counts = globalPayload?.counts ?? payload.counts;
      if (counts) setMatchGroupCounts(counts);
      setMatchesOffset(payload.offset);
      const groupedMatches = view === 'cleanup' ? [] : Array.from(new Map(
        payload.groups.flatMap((group) => group.candidates).map((match) => [match.id, match])
      ).values());
      const linkedActions = await Promise.all(groupedMatches.map(async (match) => {
        if (!match.triggerActionId) return null;
        try {
          return { matchId: match.id, action: await getAutomationAction(match.triggerActionId, { signal: controller.signal }) };
        } catch {
          return null;
        }
      }));
      const refreshedActions = linkedActions.reduce<Record<string, AutomationAction>>((next, entry) => {
        if (entry) next[entry.matchId] = entry.action;
        return next;
      }, {});
      if (!controller.signal.aborted) {
        setMatchActions((current) => ({ ...current, ...refreshedActions }));
        setMatchPollTimedOut((current) => linkedActions.reduce((next, entry) => {
          if (entry && ['succeeded', 'failed', 'cancelled'].includes(entry.action.status)) delete next[entry.matchId];
          return next;
        }, { ...current }));
      }
      return refreshedActions;
    } catch (reason) {
      if (!controller.signal.aborted) setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配读取失败' });
      return {};
    } finally {
      if (!controller.signal.aborted) setMatchesLoading(false);
    }
  };

  const refresh = async () => {
    setLoading(true);
    setFeedback(null);
    try {
        await Promise.all([
          loadSources(),
          loadItems(),
          loadMatches(0),
          getRssSeedItems({ reviewState: 'follow_needs_review', limit: 1, offset: 0 }).then((payload) => setReviewTotal(payload.total))
        ]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '资源中心读取失败' });
    } finally {
      setLoading(false);
    }
  };

  const runIdentityBackfill = async () => {
    setIdentityBackfillBusy(true);
    try {
      const result = await backfillRssIdentities(50);
      setFeedback({
        tone: 'ok',
        message: `身份回填完成：本次扫描 ${result.scanned} 条，识别 ${result.identified} 条，冲突 ${result.conflicts} 条，未变化 ${result.unchanged} 条，剩余 ${result.remaining} 条`
      });
      await Promise.all([loadSources(), loadItems({ offset: 0 })]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 身份回填失败' });
    } finally {
      setIdentityBackfillBusy(false);
    }
  };

  const runMatcher = async () => {
    setMatcherRunBusy(true);
    try {
      const result = await runRssMatcher(200);
      setFeedback({
        tone: 'ok',
        message: `匹配器完成：扫描 ${result.scanned} 条，新增 ${result.created} 条候选，尚有 ${result.uncheckedRemaining ?? result.remaining} 条从未检查 · ${result.remaining} 条未建立候选`
      });
      await Promise.all([loadSources(), loadItems({ offset: 0 }), loadMatches(0)]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 历史匹配失败' });
    } finally {
      setMatcherRunBusy(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, windowFilter, identityStatus, followState, mediaTypeFilter, resourceView, resourceContext, urlRevision]);

  useEffect(() => {
    const restoreUrlState = () => {
      const next = readRssLibraryUrlState();
      setResourceView(next.view);
      setResourceContext({
        publishedDate: next.publishedDate,
        subscriptionId: next.subscriptionId,
        tmdbId: next.tmdbId,
        mediaType: next.mediaType,
        contextTitle: next.contextTitle,
        seasonNumber: next.seasonNumber,
        episodeNumber: next.episodeNumber,
        matchId: next.matchId
      });
      setQuery(next.query);
      setSourceId(next.sourceId);
      setIdentityStatus(next.identityStatus);
      setFollowState(next.followState);
      setMediaTypeFilter(next.resourceType);
      setWindowFilter(next.windowFilter);
      setOffset(next.offset);
      setUrlRevision((current) => current + 1);
    };
    window.addEventListener('popstate', restoreUrlState);
    return () => window.removeEventListener('popstate', restoreUrlState);
  }, []);

  useEffect(() => () => {
    itemsRequestRef.current?.abort();
    matchesRequestRef.current?.abort();
    matchPollRefs.current.forEach((controller) => controller.abort());
    matchPollRefs.current.clear();
    resourcePollRefs.current.forEach((controller) => controller.abort());
    resourcePollRefs.current.clear();
    detailRequestRef.current?.abort();
  }, []);

  const openItemDetail = async (item: RssSeedItem) => {
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setDetailItem(item);
    setDetailError('');
    setDetailLoading(true);
    try {
      const detail = await getRssSeedItem(item.id, { signal: controller.signal });
      if (!controller.signal.aborted) setDetailItem(detail);
    } catch (reason) {
      if (!controller.signal.aborted) setDetailError(reason instanceof Error ? reason.message : '种子详情读取失败');
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  };

  const closeItemDetail = () => {
    detailRequestRef.current?.abort();
    setDetailItem(null);
    setDetailError('');
    setDetailLoading(false);
  };

  const pollMatchAction = async (matchId: string, actionId: string) => {
    matchPollRefs.current.get(matchId)?.abort();
    const controller = new AbortController();
    matchPollRefs.current.set(matchId, controller);
    setMatchPollTimedOut((current) => {
      const next = { ...current };
      delete next[matchId];
      return next;
    });
    try {
      for (let attempt = 0; attempt < matchActionPollAttempts; attempt += 1) {
        const action = await getAutomationAction(actionId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setMatchActions((current) => ({ ...current, [matchId]: action }));
        if (['succeeded', 'failed', 'cancelled'].includes(action.status)) {
          setMatchPollTimedOut((current) => {
            const next = { ...current };
            delete next[matchId];
            return next;
          });
          void loadMatches(matchesOffset);
          return;
        }
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, matchActionPollIntervalMs);
          controller.signal.addEventListener('abort', () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
      }
      if (!controller.signal.aborted) {
        const refreshed = await loadMatches(matchesOffset);
        if (refreshed[matchId]?.id === actionId && ['succeeded', 'failed', 'cancelled'].includes(refreshed[matchId].status)) return;
        setMatchPollTimedOut((current) => ({ ...current, [matchId]: true }));
        if (!controller.signal.aborted) setFeedback({ tone: 'error', message: '状态确认已超时，匹配记录已刷新；任务可能仍在后台执行，可稍后再次确认。' });
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        const refreshed = await loadMatches(matchesOffset);
        if (refreshed[matchId]?.id === actionId && ['succeeded', 'failed', 'cancelled'].includes(refreshed[matchId].status)) return;
        setMatchPollTimedOut((current) => ({ ...current, [matchId]: true }));
        if (!controller.signal.aborted) setFeedback({ tone: 'error', message: `${reason instanceof Error ? reason.message : 'RSS 匹配动作读取失败'}；匹配记录已刷新，可稍后再次确认。` });
      }
    } finally {
      if (matchPollRefs.current.get(matchId) === controller) matchPollRefs.current.delete(matchId);
    }
  };

  const pollResourceDownloadAction = async (itemId: string, actionId: string) => {
    resourcePollRefs.current.get(itemId)?.abort();
    const controller = new AbortController();
    resourcePollRefs.current.set(itemId, controller);
    try {
      for (let attempt = 0; attempt < matchActionPollAttempts; attempt += 1) {
        const action = await getAutomationAction(actionId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setResourceDownloadActions((current) => ({ ...current, [itemId]: action }));
        if (['succeeded', 'failed', 'cancelled'].includes(action.status)) {
          await loadItems({ offset });
          if (action.status === 'failed') {
            setFeedback({ tone: 'error', message: action.error?.message || 'qB 资源提交失败' });
          }
          return;
        }
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, matchActionPollIntervalMs);
          controller.signal.addEventListener('abort', () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
      }
      if (!controller.signal.aborted) {
        await loadItems({ offset });
        setFeedback({ tone: 'error', message: 'qB 状态确认已超时，资源列表已刷新；可在任务中心继续确认。' });
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        await loadItems({ offset });
        setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'qB 资源提交状态读取失败' });
      }
    } finally {
      if (resourcePollRefs.current.get(itemId) === controller) resourcePollRefs.current.delete(itemId);
    }
  };

  const analyzeMatch = (match: RssMatch) => {
    setMatchBusy(`analysis:${match.id}`);
    startRssMatchAnalysis(match.id, createIdempotencyKey())
      .then((action) => {
        setMatchActions((current) => ({ ...current, [match.id]: action }));
        void pollMatchAction(match.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 匹配分析提交失败' }))
      .finally(() => setMatchBusy(''));
  };

  const previewExactDownload = (group: RssMatchGroup, busyKey = `exact-preview:${group.id}`) => {
    setMatchBusy(busyKey);
    previewRssArtifactExactDownload(group.id)
      .then((preview) => {
        setExactPreviews((current) => ({ ...current, [group.id]: preview }));
        const primary = preview.blockers[0];
        if (preview.ready && preview.previewToken) {
          setExactDownloadTarget({ group, preview });
        }
        setFeedback({
          tone: preview.ready ? 'ok' : 'error',
          message: primary?.message || (preview.ready
            ? preview.downloadCategoryConfigured === false
              ? '精准下载预检通过 · 未设置 qB 分类，将按订阅目录提交'
              : '精准下载预检通过'
            : '精准下载预检未通过')
        });
      })
      .catch((reason: unknown) => setFeedback({
        tone: 'error',
        message: reason instanceof Error ? reason.message : '精准下载预检失败'
      }))
      .finally(() => setMatchBusy(''));
  };

  const previewItemExactDownload = async (item: RssSeedItem) => {
    if (item.followState !== 'linked') {
      const busyKey = `resource-preview:${item.id}`;
      setMatchBusy(busyKey);
      setFeedback(null);
      try {
        const preview = await previewRssResourceDownload(item.id);
        const primary = preview.blockers[0];
        if (preview.ready && preview.previewToken) {
          setResourceDownloadTarget({ item, preview });
        }
        setFeedback({
          tone: preview.ready ? 'ok' : 'error',
          message: primary?.message || (preview.ready
            ? `已自动归入 ${preview.categoryDirectory || preview.categoryLabel}`
            : '资源下载预检未通过')
        });
      } catch (reason) {
        setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '资源下载预检失败' });
      } finally {
        setMatchBusy('');
      }
      return;
    }
    const busyKey = `exact-resolve:${item.id}`;
    setMatchBusy(busyKey);
    setFeedback(null);
    try {
      const payload = await getRssArtifactGroups({ itemId: item.id, limit: 2, offset: 0 });
      if (payload.total === 0) {
        throw new Error('该资源的追更关联已变化，请刷新后重试');
      }
      if (payload.total !== 1 || payload.groups.length !== 1) {
        throw new Error('该资源存在多个候选归属，不能自动选择下载目标');
      }
      previewExactDownload(payload.groups[0], busyKey);
    } catch (reason) {
      setMatchBusy('');
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '下载目标读取失败' });
    }
  };

  const confirmExactDownload = () => {
    const previewToken = exactDownloadTarget?.preview.previewToken;
    if (!exactDownloadTarget || !previewToken) return;
    const { group, preview } = exactDownloadTarget;
    const match = group.representativeMatch
      || group.candidates.find((candidate) => candidate.bestCandidate)
      || group.candidates[0];
    if (!match) return;
    setExactDownloadTarget(null);
    setMatchBusy(`exact-download:${group.id}`);
    startRssArtifactExactDownload(group.id, {
      confirm: true,
      previewToken,
      idempotencyKey: createIdempotencyKey()
    })
      .then((action) => {
        setMatchActions((current) => ({ ...current, [match.id]: action }));
        setFeedback({ tone: 'ok', message: '已提交 qB，正在确认下载任务。' });
        void pollMatchAction(match.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({
        tone: 'error',
        message: reason instanceof Error ? reason.message : '精准下载提交失败'
      }))
      .finally(() => setMatchBusy(''));
  };

  const confirmResourceDownload = () => {
    const previewToken = resourceDownloadTarget?.preview.previewToken;
    if (!resourceDownloadTarget || !previewToken) return;
    const { item } = resourceDownloadTarget;
    setResourceDownloadTarget(null);
    setMatchBusy(`resource-download:${item.id}`);
    startRssResourceDownload(item.id, {
      confirm: true,
      previewToken,
      idempotencyKey: createIdempotencyKey()
    })
      .then((action) => {
        setResourceDownloadActions((current) => ({ ...current, [item.id]: action }));
        setFeedback({ tone: 'ok', message: '已按自动分类提交 qB，正在确认下载任务。' });
        void pollResourceDownloadAction(item.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({
        tone: 'error',
        message: reason instanceof Error ? reason.message : 'qB 资源提交失败'
      }))
      .finally(() => setMatchBusy(''));
  };

  const confirmMatchDownload = () => {
    if (!downloadTarget) return;
    const { match, analysis } = downloadTarget;
    setDownloadTarget(null);
    setMatchBusy(`download:${match.id}`);
    startRssMatchDownload(match.id, {
      confirm: true,
      idempotencyKey: createIdempotencyKey(),
      analysisActionId: analysis.id
    })
      .then((action) => {
        setMatchActions((current) => ({ ...current, [match.id]: action }));
        setFeedback({ tone: 'ok', message: '已交给 Torra 处理，Torra 正在接收任务。' });
        void pollMatchAction(match.id, action.id);
      })
      .catch((reason: unknown) => setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 候选下载提交失败' }))
      .finally(() => setMatchBusy(''));
  };

  const timeline = useMemo(() => {
    if (resourceContext.episodeNumber == null) return items;
    const scopePriority = {
      explicit_episode: 0,
      explicit_multi_episode: 1,
      season_pack: 2,
      scope_pending: 3
    } as const;
    return items
      .map((item, index) => ({ item, index, scope: classifyRssResourceScope(item) }))
      .sort((left, right) => scopePriority[left.scope] - scopePriority[right.scope] || left.index - right.index)
      .map(({ item }) => item);
  }, [items, resourceContext.episodeNumber]);
  const itemScopeCounts = useMemo(
    () => countRssResourceScopes(items.map((item) => classifyRssResourceScope(item))),
    [items]
  );
  const matchByItemId = useMemo(() => {
    const index = new Map<string, RssMatch>();
    matches.forEach((match) => {
      if (match.itemId && !index.has(match.itemId)) index.set(match.itemId, match);
    });
    return index;
  }, [matches]);
  const searchedSources = useMemo(() => {
    const scoped = sourceId ? sources.filter((source) => source.id === sourceId) : sources;
    return {
      searched: scoped.filter((source) => source.enabled && source.feedConfigured && !source.lastError),
      unavailable: scoped.filter((source) => source.enabled && Boolean(source.lastError)),
      inactive: scoped.filter((source) => (!source.enabled || !source.feedConfigured) && !source.lastError)
    };
  }, [sourceId, sources]);

  const sourceSearchSummary = useMemo(() => {
    const parts: string[] = [];
    if (searchedSources.searched.length) parts.push(`已搜索：${searchedSources.searched.map((source) => source.name).join('、')}`);
    if (searchedSources.unavailable.length) parts.push(`暂时不可用：${searchedSources.unavailable.map((source) => source.name).join('、')}`);
    if (searchedSources.inactive.length) parts.push(`未启用或未配置：${searchedSources.inactive.map((source) => source.name).join('、')}`);
    return parts.join('；');
  }, [searchedSources]);

  const openCreate = () => {
    setEditing(null);
    setForm(defaultForm);
    setFormOpen(true);
    setFeedback(null);
  };

  const openEdit = (source: RssSource) => {
    setEditing(source);
    setForm({
      name: source.name,
      feedUrl: '',
      enabled: source.enabled,
      intervalMinutes: source.intervalMinutes,
      retentionDays: source.retentionDays as 3 | 7 | 14,
      allowHttp: source.allowHttp
    });
    setFormOpen(true);
    setFeedback(null);
  };

  const submitSource = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const payload = { ...form };
      if (editing && !payload.feedUrl) delete payload.feedUrl;
      await saveRssSource(payload, editing?.id);
      setFormOpen(false);
      setEditing(null);
      setForm(defaultForm);
      setFeedback({ tone: 'ok', message: editing ? '来源设置已保存' : 'RSS 来源已加入资源中心' });
      await loadSources();
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 来源保存失败' });
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (source: RssSource) => {
    if (testingSourceId) return;
    setTestingSourceId(source.id);
    setFeedback(null);
    try {
      const action = await testRssSource(source.id);
      setFeedback({
        tone: action.status === 'succeeded' ? 'ok' : 'error',
        message: action.status === 'succeeded'
          ? `RSS 可读取，识别到 ${action.result?.items ?? 0} 条内容`
          : action.result?.message || 'RSS 测试失败'
      });
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : 'RSS 测试失败' });
    } finally {
      setTestingSourceId('');
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      await deleteRssSource(deleteTarget.id);
      const nextSourceId = sourceId === deleteTarget.id ? '' : sourceId;
      if (nextSourceId !== sourceId) setSourceId(nextSourceId);
      setOffset(0);
      syncUrlState({ sourceId: nextSourceId, offset: 0 });
      setDeleteTarget(null);
      setFeedback({ tone: 'ok', message: '来源和对应本地索引已删除' });
      await Promise.all([loadSources(), loadItems({ sourceId: nextSourceId, offset: 0 })]);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '来源删除失败' });
    } finally {
      setSaving(false);
    }
  };

  const changeResourceView = (nextView: ResourceView) => {
    if (nextView === resourceView) return;
    setResourceView(nextView);
    setOffset(0);
    setMatchesOffset(0);
    setFeedback(null);
    writeRssLibraryUrlState({
      view: nextView,
      ...resourceContext,
      query,
      sourceId,
      identityStatus,
      followState,
      resourceType: mediaTypeFilter,
      windowFilter,
      offset: 0
    }, 'push');
  };

  const previewCleanupGroup = async (group: RssMatchGroup) => {
    const matchIds = group.candidates.map((candidate) => candidate.id);
    setCleanupBusy(true);
    setFeedback(null);
    try {
      const preview = await previewRssMatchCleanup(matchIds);
      if (preview.itemCount < 1) {
        setFeedback({ tone: 'error', message: '当前归属已变化，没有可安全归档的失效匹配' });
        await loadMatches(matchesOffset, 'cleanup');
        return;
      }
      setCleanupPreview(preview);
    } catch (reason) {
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '待整理预览生成失败' });
    } finally {
      setCleanupBusy(false);
    }
  };

  const confirmCleanup = async () => {
    if (!cleanupPreview) return;
    setCleanupBusy(true);
    try {
      const result = await applyRssMatchCleanup({
        previewId: cleanupPreview.id,
        fingerprint: cleanupPreview.fingerprint,
        matchIds: cleanupPreview.items.map((item) => item.matchId),
        idempotencyKey: createIdempotencyKey()
      });
      setCleanupPreview(null);
      setFeedback({ tone: 'ok', message: `已归档 ${result.archivedCount} 条失效匹配；RSS 原始资源仍保留` });
      await loadMatches(0, 'cleanup');
    } catch (reason) {
      setCleanupPreview(null);
      setFeedback({ tone: 'error', message: reason instanceof Error ? reason.message : '失效匹配归档失败' });
      await loadMatches(0, 'cleanup');
    } finally {
      setCleanupBusy(false);
    }
  };

  const resourceViews: Array<{ id: ResourceView; label: string; count: number }> = [
    { id: 'new', label: '新资源', count: summary.items },
    { id: 'identify', label: '追更待识别', count: reviewTotal },
    { id: 'scoring', label: '候选评分', count: matchGroupCounts.scoreableTotal ?? matchGroupCounts.total },
    { id: 'upgrades', label: '追更洗版', count: matchGroupCounts.upgradeAvailable },
    { id: 'cleanup', label: '需要决定', count: decisionTotal }
  ];
  const currentRangeText = windowFilter
    ? `当前范围 ${total} 条 · 最近 ${windowFilter === '1h' ? '1 小时' : windowFilter === '24h' ? '24 小时' : '7 天'}`
    : `当前范围 ${total} 条`;
  const matchPanelTitle = resourceView === 'upgrades'
    ? '追更洗版候选'
    : resourceView === 'cleanup'
      ? '需要人工决定的候选'
      : 'Torra 规则候选决策';
  const matchPanelSummary = resourceView === 'upgrades'
    ? matchesTotal ? `${matchesTotal} 个覆盖范围全部胜出的唯一产物` : '当前没有可洗版的更高分候选'
    : resourceView === 'cleanup'
      ? matchesTotal ? `${matchesTotal} 个候选组需要确认身份、规则或订阅归属` : '当前没有需要人工决定的候选'
      : matchesTotal ? `最近 ${matchesTotal} 个唯一产物 · 只读评估，不会自动下载` : '新资源会自动匹配并评分，不会自动下载';
  const upgradeEmpty = (matchGroupCounts.waitingBaseline ?? 0) > 0
    ? {
        title: `${matchGroupCounts.waitingBaseline} 个候选组正在等待当前版本基线`,
        detail: '候选已经到达，但尚不能证明它严格优于当前入库版本。',
        action: 'baseline' as const
      }
    : ((matchGroupCounts.blocked ?? 0) + (matchGroupCounts.needsCleanup ?? 0)) > 0
      ? {
          title: '部分候选的身份、规则或订阅归属尚未确认',
          detail: '这些候选不会进入下载，需先在候选评分或待整理视图解决阻断。',
          action: 'blocked' as const
        }
      : (matchGroupCounts.monitoringRss ?? 0) > 0
        ? {
            title: '候选仍在评分或持续观察',
            detail: 'Torra 规则评分完成后，只会把严格高于当前版本的候选列在这里。',
            action: 'scoring' as const
          }
        : {
            title: '当前没有可洗版的更高分候选',
            detail: '已评分候选均未严格高于当前入库版本。',
            action: 'none' as const
          };
  const hasResourceContext = Boolean(
    resourceContext.publishedDate || resourceContext.subscriptionId || resourceContext.tmdbId || resourceContext.matchId
  );
  const resourceContextTitle = resourceContext.matchId
    ? '任务来源候选'
    : resourceContext.publishedDate
      ? `${resourceContext.publishedDate}${followState === 'linked' ? ' 追更命中新资源' : ' 新资源'}`
      : resourceContext.contextTitle || '当前追更资源';
  const resourceContextScope = [
    resourceContext.mediaType === 'movie' ? '电影' : resourceContext.mediaType === 'tv' ? '剧集' : '',
    resourceContext.seasonNumber != null ? `S${String(resourceContext.seasonNumber).padStart(2, '0')}` : '',
    resourceContext.episodeNumber != null ? `E${String(resourceContext.episodeNumber).padStart(2, '0')}` : '',
    resourceContext.tmdbId ? `TMDB ${resourceContext.tmdbId}` : ''
  ].filter(Boolean).join(' · ');

  const clearResourceContext = () => {
    const nextWindow = resourceContext.publishedDate && windowFilter === '' ? '24h' : windowFilter;
    setResourceContext(emptyResourceContext);
    setFollowState('');
    setWindowFilter(nextWindow);
    setOffset(0);
    setMatchesOffset(0);
    writeRssLibraryUrlState({
      view: resourceView,
      ...emptyResourceContext,
      query,
      sourceId,
      identityStatus,
      followState: '',
      resourceType: mediaTypeFilter,
      windowFilter: nextWindow,
      offset: 0
    });
  };

  return (
    <main className="work-page ops-page rss-library-page">
      <section className="ops-hero ops-hero--compact rss-library-hero">
        <div>
          <p className="ops-eyebrow">PT RSS · 候选决策</p>
          <h1>资源中心</h1>
          <p className="ops-page-subtitle">查看站点新资源、身份关联与候选评分。</p>
          <p className="ops-deck">PT RSS 内容在本地搜索和评分；只有通过订阅身份与安全预检的候选才能进入后续流程。</p>
        </div>
        <button aria-busy={loading} className="ops-action-button" disabled={loading} type="button" onClick={refresh}>
          <RefreshCcw aria-hidden="true" className={loading ? 'rss-spin' : ''} size={15} />
          {loading ? '正在刷新' : `刷新 · RSS ${summary.enabled ? '已开启' : '已关闭'}`}
        </button>
      </section>

      <section className="rss-ledger-strip" aria-label="资源中心状态">
        <div><span>本地种子</span><strong>{summary.items}</strong></div>
        <div><span>RSS 来源</span><strong className="rss-source-count">已启用 {summary.activeSources}/{summary.sources} · <em className={summary.errorSources ? 'rss-value--warn' : ''}>健康 {Math.max(0, summary.sources - summary.errorSources)}/{summary.sources}</em></strong></div>
        <div><span>异常来源</span><strong className={summary.errorSources ? 'rss-value--warn' : ''}>{summary.errorSources}</strong></div>
        <div><span>最近收集</span><strong>{summary.lastSuccessAt ? <RelativeTime value={summary.lastSuccessAt} /> : '尚未收集'}</strong></div>
      </section>

      {feedback && (
        <div className={feedback.tone === 'error' ? 'rss-feedback rss-feedback--error' : 'rss-feedback'} role="status">
          {feedback.tone === 'error' ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
          {feedback.message}
        </div>
      )}

      <nav aria-label="资源中心视图" className="rss-resource-views" role="tablist">
        {resourceViews.map((view) => (
          <button
            aria-selected={resourceView === view.id}
            className={resourceView === view.id ? 'rss-resource-view rss-resource-view--active' : 'rss-resource-view'}
            key={view.id}
            role="tab"
            type="button"
            onClick={() => changeResourceView(view.id)}
          >
            <span>{view.label}</span>
            <strong>全库 {view.count}</strong>
          </button>
        ))}
      </nav>

      {hasResourceContext && (
        <section aria-label="当前资源范围" className="rss-context-scope">
          <div>
            <span>当前范围</span>
            <strong>{resourceContextTitle}</strong>
            <small>{resourceContextScope || '按可靠来源证据定位'}</small>
          </div>
          <button aria-label="清除当前资源范围" className="ops-icon-button" title="清除当前资源范围" type="button" onClick={clearResourceContext}>
            <X aria-hidden="true" size={15} />
          </button>
        </section>
      )}

      <section className="rss-library-layout">
        <div className="rss-index-panel">
          <div className="rss-items-view" hidden={resourceView === 'scoring' || resourceView === 'upgrades' || resourceView === 'cleanup'}>
          <div className="rss-toolbar">
            <form
              className="rss-search"
              onSubmit={(event) => {
                event.preventDefault();
                const nextQuery = query.trim();
                setQuery(nextQuery);
                setOffset(0);
                syncUrlState({ query: nextQuery, offset: 0 });
                void loadItems({ query: nextQuery, offset: 0 });
              }}
            >
              <input aria-label="搜索本地资源" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="中文名、发布名、制作组、HDR…" />
              {query && <button aria-label="清空搜索" className="rss-search-clear" title="清空搜索" type="button" onClick={() => { setQuery(''); setOffset(0); syncUrlState({ query: '', offset: 0 }); void loadItems({ query: '', offset: 0 }); }}><X aria-hidden="true" size={14} /></button>}
              <button aria-label="提交资源搜索" className="rss-search-submit" disabled={itemsLoading} title="搜索" type="submit"><Search aria-hidden="true" size={15} /></button>
            </form>
            <div className="rss-window-tabs" aria-label="更新时间范围">
              {([
                ['', '全部'], ['1h', '1 小时'], ['24h', '24 小时'], ['7d', '7 天']
              ] as Array<[WindowFilter, string]>).map(([value, label]) => (
                <button className={windowFilter === value ? 'rss-window-tab rss-window-tab--active' : 'rss-window-tab'} key={label} type="button" onClick={() => { setWindowFilter(value); setOffset(0); syncUrlState({ windowFilter: value, offset: 0 }); }}>{label}</button>
              ))}
            </div>
          </div>

          <div className="rss-index-head">
            <span>{loading || itemsLoading ? '正在读取本地索引' : currentRangeText}</span>
            <div className="rss-index-filters">
              <select aria-label="按媒体类型筛选" value={resourceContext.mediaType || mediaTypeFilter} disabled={Boolean(resourceContext.mediaType)} onChange={(event) => { const next = event.target.value as MediaTypeFilter; setMediaTypeFilter(next); setOffset(0); syncUrlState({ resourceType: next, offset: 0 }); }}>
                <option value="">全部类型</option>
                <option value="tv">电视剧</option>
                <option value="movie">电影</option>
                <option value="unknown">其他 / 待确认</option>
              </select>
              <select aria-label="按 RSS 来源筛选" value={sourceId} onChange={(event) => { const next = event.target.value; setSourceId(next); setOffset(0); syncUrlState({ sourceId: next, offset: 0 }); }}>
                <option value="">全部来源</option>
                {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
              </select>
            </div>
          </div>

          {!loading && !itemsLoading && itemScopeCounts.total > 0 && (
            <p className="rss-scope-summary" role="status">
              本页 {rssResourceScopeSummaryText(itemScopeCounts)}
            </p>
          )}

          <div className="rss-collection-summary" role="status">
            <span className={summary.enabled && summary.errorSources === 0 ? 'rss-source-light' : summary.errorSources ? 'rss-source-light rss-source-light--error' : 'rss-source-light rss-source-light--off'} />
            <div>
              <strong>{!summary.enabled ? 'RSS 收集当前未开启' : summary.errorSources ? '部分 RSS 来源暂时不可用' : 'RSS 正在正常收集'}</strong>
              <span>{!summary.enabled ? '已保存的本地种子仍可搜索，开启后才会继续收集新内容。' : summary.errorSources ? '可用来源仍会继续收集；展开“RSS 来源与设置”可查看异常来源。' : '新种子会自动保存，并继续尝试匹配你的追更作品。'}</span>
            </div>
          </div>

          <div className="rss-timeline">
            {!loading && timeline.length === 0 && (
              <div className="ops-empty rss-empty">
                <Database aria-hidden="true" size={22} />
                <strong>{query.trim() ? `没有找到“${query.trim()}”` : resourceView === 'identify' ? '当前没有与追更相关的待识别资源' : sources.length ? '当前范围内没有资源' : '还没有添加 RSS 来源'}</strong>
                <span>{resourceView === 'identify' ? '未关联追更的 RSS 资源保持中性，不会进入这里。' : sources.length ? (sourceSearchSummary || '当前没有可搜索的 RSS 来源。') : '先添加 RSS 来源；开启收集后，新发布内容会保存在这里。'}</span>
              </div>
            )}
            {timeline.map((item) => {
              const scope = classifyRssResourceScope(item);
              const isSeasonPackForEpisode = resourceContext.episodeNumber != null && scope === 'season_pack';
              const itemMatch = matchByItemId.get(item.id);
              const itemAction = resourceDownloadActions[item.id] || (itemMatch ? matchActions[itemMatch.id] : undefined);
              const downloadBusy = [`exact-resolve:${item.id}`, `resource-preview:${item.id}`, `resource-download:${item.id}`].includes(matchBusy);
              const resourceDownloadActive = ['claimed', 'submitted', 'polling', 'succeeded'].includes(
                itemAction?.type === 'rss-resource-download'
                  ? itemAction.status
                  : item.resourceDownloadStatus || ''
              );
              const downloadDisabled = !item.hasDownload
                || item.identityStatus !== 'identified'
                || !['movie', 'tv'].includes(item.mediaType)
                || resourceDownloadActive
                || Boolean(matchBusy);
              const downloadTitle = !item.hasDownload
                ? 'RSS 没有提供下载附件'
                : item.identityStatus !== 'identified' || !['movie', 'tv'].includes(item.mediaType)
                  ? '媒体身份未确认，暂不能自动分类'
                  : resourceDownloadActive
                    ? '该资源已提交 qB'
                  : downloadBusy
                    ? '正在执行下载预检'
                    : isSeasonPackForEpisode
                      ? '下载整季包（会包含本季其他集）'
                    : item.followState === 'linked'
                      ? '下载前先执行安全预检'
                      : '自动分类并提交 qB';
              const downloadButton = (
                <button
                  aria-label={`${downloadTitle}：${item.mediaTitle || item.sourceTitle || item.title}`}
                  className={isSeasonPackForEpisode ? 'ops-icon-button rss-seed-download rss-seed-download--season-pack' : 'ops-icon-button rss-seed-download'}
                  disabled={downloadDisabled}
                  title={downloadTitle}
                  type="button"
                  onClick={() => void previewItemExactDownload(item)}
                >
                  <Download aria-hidden="true" size={14} />
                </button>
              );
              return (
              <article className="rss-seed-row" key={item.id}>
                <div className="rss-seed-time"><span /> <RelativeTime value={item.publishedAt || item.lastSeenAt} /></div>
                <div className="rss-seed-body rss-seed-body--with-poster">
                  <PosterImage
                    className="rss-seed-poster"
                    fallbackClassName="rss-seed-poster--fallback"
                    src={item.posterUrl}
                    title={item.mediaTitle || item.sourceTitle || item.title}
                  />
                  <div className="rss-seed-content">
                    <div className="rss-seed-card-head">
                      <span>{item.sourceName}</span>
                      <RelativeTime value={item.publishedAt || item.lastSeenAt} />
                      <span className={`rss-identity-chip rss-identity-chip--${item.identityStatus}`}>{identityLabel(item.identityStatus)}</span>
                      <span className="rss-processing-chip">{seedProcessingStateLabel(item.followState, itemMatch, itemAction, item.resourceDownloadStatus)}</span>
                    </div>
                    <h2>{item.title}</h2>
                    {(item.mediaTitle || item.sourceTitle) && (item.mediaTitle || item.sourceTitle || '').trim().toLocaleLowerCase() !== item.title.trim().toLocaleLowerCase() && (
                      <p className="rss-media-title">
                        <strong>{item.mediaTitle || item.sourceTitle}</strong>
                        {item.mediaTitle && item.mediaYear ? <span>{item.mediaYear}</span> : !item.mediaTitle && item.sourceTitle ? <span>RSS</span> : null}
                      </p>
                    )}
                    <div className="rss-seed-desktop-meta">
                      <div className="rss-seed-meta">
                        <span>{episodeLabel(item)}</span>
                        <span>{rssResourceScopeLabel(scope)}</span>
                        {isSeasonPackForEpisode && <span className="rss-scope-warning">整季包，不是单集</span>}
                        <span>{sizeLabel(item.sizeBytes)}</span>
                      </div>
                      <div className="rss-version-line">
                        {item.versionSummary
                          ? item.versionSummary.split(' · ').map((value) => <span key={value}>{value}</span>)
                          : <span className="rss-version-muted">等待版本信息</span>}
                      </div>
                    </div>
                    <details className="rss-seed-technical">
                      <summary>技术信息</summary>
                      <dl>
                        <div><dt>类型与范围</dt><dd>{episodeLabel(item)} · {rssResourceScopeLabel(scope)}</dd></div>
                        <div><dt>大小</dt><dd>{sizeLabel(item.sizeBytes)}</dd></div>
                        <div><dt>版本</dt><dd>{item.versionSummary || '等待版本信息'}</dd></div>
                        <div><dt>来源地址</dt><dd>{item.sourceDomain}</dd></div>
                        <div><dt>匹配原因</dt><dd>{rssMatchMethodLabel(item.matchMethod, item.matchConfidence)}</dd></div>
                        <div><dt>官种</dt><dd>无法判断</dd></div>
                        <div><dt>下载 / 入库 / 重复</dt><dd>尚未确认</dd></div>
                        <div><dt>当前处理状态</dt><dd>{seedProcessingStateLabel(item.followState, itemMatch, itemAction, item.resourceDownloadStatus)}</dd></div>
                        <div><dt>优先检查理由</dt><dd>{seedPriorityReason(item, scope)}</dd></div>
                      </dl>
                      <button className="rss-seed-open" type="button" onClick={() => void openItemDetail(item)}><PanelRightOpen aria-hidden="true" size={13} />查看识别证据</button>
                    </details>
                    <div className="rss-seed-mobile-actions">
                      {downloadButton}
                      <button className="rss-seed-open" type="button" onClick={() => void openItemDetail(item)}><PanelRightOpen aria-hidden="true" size={13} />详情</button>
                    </div>
                  </div>
                </div>
                <div className="rss-seed-state">
                  <span className="state-chip">{seedProcessingStateLabel(item.followState, itemMatch, itemAction, item.resourceDownloadStatus)}</span>
                  <span className={`rss-identity-chip rss-identity-chip--${item.identityStatus}`}>{identityLabel(item.identityStatus)}</span>
                  <small>{item.sourceDomain}</small>
                  <div className="rss-seed-actions">
                    {downloadButton}
                    <button className="rss-seed-open" type="button" onClick={() => void openItemDetail(item)}>
                      <PanelRightOpen aria-hidden="true" size={13} />详情
                    </button>
                  </div>
                </div>
              </article>
              );
            })}
          </div>
          {total > pageSize && (
            <nav className="rss-pagination" aria-label="资源中心分页">
              <button
                aria-label="上一页"
                disabled={itemsLoading || offset <= 0}
                title="上一页"
                type="button"
                onClick={() => void loadItems({ offset: Math.max(0, offset - pageSize) })}
              >
                <ChevronLeft aria-hidden="true" size={15} />
              </button>
              <span>第 {Math.floor(offset / pageSize) + 1} / {Math.ceil(total / pageSize)} 页</span>
              <button
                aria-label="下一页"
                disabled={itemsLoading || offset + pageSize >= total}
                title="下一页"
                type="button"
                onClick={() => void loadItems({ offset: offset + pageSize })}
              >
                <ChevronRight aria-hidden="true" size={15} />
              </button>
            </nav>
          )}
          </div>
          <section className="rss-match-panel" aria-label={matchPanelTitle} hidden={resourceView === 'new' || resourceView === 'identify'}>
            <header className="rss-match-panel__head">
              <div>
                <strong>{matchPanelTitle}</strong>
                <small>{matchPanelSummary}</small>
              </div>
              <button className="ops-link" disabled={matchesLoading} type="button" onClick={() => void loadMatches(matchesOffset)}><RefreshCcw size={13} />刷新</button>
            </header>
            {matchesLoading && <small className="sub-detail__hint">正在查看是否有种子匹配到追更作品…</small>}
            {!matchesLoading && matchGroups.length === 0 && <div className="rss-match-empty"><CheckCircle2 size={15} /><span><strong>{resourceView === 'upgrades' ? upgradeEmpty.title : resourceView === 'cleanup' ? '当前没有需要人工决定的候选' : summary.enabled && summary.errorSources === 0 ? 'RSS 正常收集，但暂未匹配到追更作品' : '暂未匹配到追更作品'}</strong><small>{resourceView === 'upgrades' ? upgradeEmpty.detail : resourceView === 'cleanup' ? '身份、规则或订阅归属存在阻断时，会安全地列在这里。' : summary.enabled ? '可用来源会继续自动检查；现在无需处理。' : '开启收集后，新资源会自动尝试匹配。'}</small>{resourceView === 'upgrades' && upgradeEmpty.action === 'baseline' && <button className="ops-link" type="button" onClick={() => onNavigate('subscription-settings')}><ShieldCheck size={13} />预览历史基线</button>}</span></div>}
            <div className="rss-match-list">
              {matchGroups.map((group) => {
                const match = group.representativeMatch
                  || group.candidates.find((candidate) => candidate.bestCandidate)
                  || group.candidates[0];
                if (!match) return null;
                const seed = items.find((item) => item.id === match.itemId);
                const action = matchActions[match.id];
                const exactPreview = exactPreviews[group.id];
                const pollTimedOut = Boolean(matchPollTimedOut[match.id]);
                const actionRunning = action && !pollTimedOut && !['succeeded', 'failed', 'cancelled'].includes(action.status);
                const selectedCount = action?.type === 'rewash-analysis' && action.status === 'succeeded' ? action.result?.selectedCount ?? 0 : 0;
                const isSubmitting = matchBusy.endsWith(`:${match.id}`) || matchBusy.endsWith(`:${group.id}`);
                const canAnalyze = match.status === 'candidate' || action?.status === 'failed' || action?.status === 'cancelled';
                const isDownloadAction = action?.type === 'rewash-download' || action?.type === 'rss-exact-download';
                const downloadFailed = Boolean(isDownloadAction && ['failed', 'cancelled'].includes(action.status));
                const downloadConfirmed = !downloadFailed && (Boolean(isDownloadAction) || match.status === 'confirmed');
                const sourceFocused = Boolean(
                  resourceContext.matchId && group.candidates.some((candidate) => candidate.id === resourceContext.matchId)
                );
                const canPreviewExact = group.coveredUnits?.length
                  ? group.state === 'upgrade_available' && group.winsAllCoveredUnits === true
                  : ['upgrade_available', 'initial_best'].includes(group.state);
                return (
                  <article className={sourceFocused ? 'rss-match-row rss-match-row--focused' : 'rss-match-row'} key={group.id}>
                    <div className="rss-match-row__content">
                      <strong>{group.title || match.itemTitle || seed?.title || '已匹配到一条追更内容'} · {group.episodeLabel}</strong>
                      <small>{candidateGroupScoreLabel(group)}</small>
                      <span className="rss-match-status">{shadowEvaluationLabel(match)}</span>
                      {resourceView === 'cleanup' && group.state === 'needs_cleanup' && (
                        <div className="rss-ownership-summary" aria-label="候选归属">
                          {(group.ownerships ?? []).map((ownership) => (
                            <span className={`rss-ownership-chip rss-ownership-chip--${ownership.state}`} key={`${ownership.matchId}:${ownership.state}`}>
                              {ownership.state === 'valid'
                                ? '有效归属'
                                : ownership.state === 'archived'
                                  ? '已归档归属'
                                  : ownership.state === 'conflict'
                                    ? '归属冲突'
                                    : '失效归属'}
                              {' · '}{ownership.subscriptionId}
                            </span>
                          ))}
                          {(group.ownerships ?? []).length === 0 && (
                            <span className="rss-ownership-chip rss-ownership-chip--invalid">失效归属 · 订阅不存在</span>
                          )}
                        </div>
                      )}
                      {resourceView === 'cleanup' && group.state === 'blocked' && (
                        <div className="rss-ownership-summary" aria-label="候选阻断原因">
                          <span className="rss-ownership-chip rss-ownership-chip--conflict">
                            暂不可处理 · {shadowEvaluationLabel(match)}
                          </span>
                        </div>
                      )}
                      {resourceView !== 'cleanup' && canPreviewExact && (
                        <button
                          className="ops-link rss-exact-preview"
                          disabled={Boolean(matchBusy)}
                          title="只读复核订阅、规则、版本和下载器状态"
                          type="button"
                          onClick={() => previewExactDownload(group)}
                        >
                          <ShieldCheck size={13} />
                          {matchBusy === `exact-preview:${group.id}` ? '正在预检' : '精准下载预检'}
                        </button>
                      )}
                      {exactPreview && (
                        <small className={exactPreview.ready ? 'rss-match-status' : 'rss-match-status rss-match-status--error'}>
                          {exactPreview.ready
                            ? `精准下载预检通过 · ${exactPreview.coveredUnitCount ?? group.coveredUnits?.length ?? 1} 集`
                            : `精准下发不可用 · ${exactPreview.blockers[0]?.message || '预检未通过'}`}
                          {!exactPreview.ready && exactPreview.blockers.length > 1 ? ` · 另有 ${exactPreview.blockers.length - 1} 项未通过` : ''}
                        </small>
                      )}
                      {(action || pollTimedOut) && <small className={action?.status === 'failed' || pollTimedOut ? 'rss-match-status rss-match-status--error' : 'rss-match-status'}>{pollTimedOut ? '人工 Torra 分析状态确认超时' : `人工 Torra 分析 · ${matchActionLabel(action, match.status)}`}</small>}
                      <details className="rss-candidate-group__details">
                        <summary>{group.unitResults?.length ? `查看 ${group.unitResults.length} 个集级结果` : `查看 ${group.candidateCount} 个候选版本`}</summary>
                        <div className="rss-candidate-group__versions">
                          {group.unitResults?.length ? group.unitResults.map((result) => result.match && (
                            <div className={result.match.id === resourceContext.matchId ? 'rss-candidate-version rss-candidate-version--focused' : 'rss-candidate-version'} key={result.unitId}>
                              <span>
                                <strong>{result.seasonNumber && result.episodeNumber ? `S${String(result.seasonNumber).padStart(2, '0')}E${String(result.episodeNumber).padStart(2, '0')}` : '季集暂未确认'} · {result.match.candidateSummary?.versionSummary || '版本信息暂未确认'}</strong>
                                <small>{result.winsUnit ? '本集冠军' : result.state === 'protected' ? '本集受保护' : '本集未胜出'} · {shadowEvaluationLabel(result.match)}</small>
                              </span>
                            </div>
                          )) : group.candidates.map((candidate) => (
                            <div className={candidate.id === resourceContext.matchId ? 'rss-candidate-version rss-candidate-version--focused' : 'rss-candidate-version'} key={candidate.id}>
                              <span>
                                <strong>{candidate.candidateSummary?.versionSummary || '版本信息暂未确认'}</strong>
                                <small>{shadowEvaluationLabel(candidate)}</small>
                              </span>
                              {Boolean(candidate.candidateSummary?.scoreBreakdown?.length) && (
                                <small>{candidate.candidateSummary?.scoreBreakdown?.map((row) => `${row.label} ${row.score >= 0 ? '+' : ''}${scoreLabel(row.score)}`).join(' · ')}</small>
                              )}
                            </div>
                          ))}
                        </div>
                      </details>
                    </div>
                    {resourceView === 'cleanup' && group.state === 'needs_cleanup' ? (
                      <button
                        className="ops-action-button ops-action-button--primary"
                        disabled={cleanupBusy}
                        type="button"
                        onClick={() => void previewCleanupGroup(group)}
                      >
                        <Trash2 size={13} />{cleanupBusy ? '正在核验' : '预览归档'}
                      </button>
                    ) : resourceView === 'cleanup' ? (
                      <button className="ops-action-button" type="button" onClick={() => changeResourceView('scoring')}>
                        查看评分条件
                      </button>
                    ) : pollTimedOut && action ? (
                      <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => {
                        setFeedback({ tone: 'ok', message: '正在重新确认 Torra 动作状态…' });
                        void pollMatchAction(match.id, action.id);
                      }}>
                        <RefreshCcw size={13} />再次确认
                      </button>
                    ) : action && selectedCount > 0 ? (
                      <button className="ops-action-button ops-action-button--primary" disabled={isSubmitting} type="button" onClick={() => setDownloadTarget({ match, analysis: action })}>
                        <Download size={13} />{isSubmitting ? '正在提交' : '处理 Torra 搜索结果'}
                      </button>
                    ) : downloadFailed ? (
                      <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => onNavigate('tasks', { outcomeState: 'action_required' })}>
                        前往任务中心
                      </button>
                    ) : downloadConfirmed ? (
                      <button className="ops-action-button" disabled type="button">已交给 Torra</button>
                    ) : (
                      <button aria-label="人工触发 Torra 整条订阅分析" title="会触发 Torra 对整条订阅执行搜索分析" className={canAnalyze ? 'ops-action-button ops-action-button--primary' : 'ops-action-button'} disabled={!canAnalyze || Boolean(actionRunning) || isSubmitting || action?.status === 'succeeded'} type="button" onClick={() => analyzeMatch(match)}>
                        <Send size={13} />{isSubmitting ? '正在提交' : actionRunning ? '人工分析中' : canAnalyze ? '人工 Torra 分析' : '无需处理'}
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
            {matchesTotal > 10 && (
              <nav className="rss-pagination rss-pagination--compact" aria-label="RSS 匹配分页">
                <button aria-label="上一页匹配" title="上一页匹配" disabled={matchesLoading || matchesOffset <= 0} type="button" onClick={() => void loadMatches(Math.max(0, matchesOffset - 10))}><ChevronLeft aria-hidden="true" size={14} /></button>
                <span>第 {Math.floor(matchesOffset / 10) + 1} / {Math.ceil(matchesTotal / 10)} 页</span>
                <button aria-label="下一页匹配" title="下一页匹配" disabled={matchesLoading || matchesOffset + 10 >= matchesTotal} type="button" onClick={() => void loadMatches(matchesOffset + 10)}><ChevronRight aria-hidden="true" size={14} /></button>
              </nav>
            )}
          </section>

          <ConfirmDialog
            busy={cleanupBusy}
            labelledBy="rss-match-cleanup-title"
            describedBy="rss-match-cleanup-description"
            open={Boolean(cleanupPreview)}
            onClose={() => !cleanupBusy && setCleanupPreview(null)}
          >
            <span className="ops-confirm-dialog__signal">本地归档</span>
            <h2 id="rss-match-cleanup-title">归档失效匹配？</h2>
            <p id="rss-match-cleanup-description">
              将归档 {cleanupPreview?.itemCount ?? 0} 条已确认失去订阅归属的匹配。RSS 原始资源、评分证据和候选历史不会删除，也不会触发搜索或下载。
            </p>
            <div className="rss-cleanup-preview-list">
              {cleanupPreview?.items.map((item) => (
                <span key={item.matchId}>
                  <strong>{item.title || '标题暂未确认'}</strong>
                  <small>{item.subscriptionId} · 订阅不存在</small>
                </span>
              ))}
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" disabled={cleanupBusy} type="button" onClick={() => setCleanupPreview(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" disabled={cleanupBusy} type="button" onClick={() => void confirmCleanup()}>
                <Trash2 size={13} />{cleanupBusy ? '正在归档' : '确认归档'}
              </button>
            </div>
          </ConfirmDialog>

          <details className="rss-diagnostics">
            <summary><ServerCog size={15} /><span><strong>高级诊断</strong><small>身份识别、历史扫描和匹配器信息</small></span></summary>
            <div className="rss-diagnostics__body">
              <div className="rss-diagnostics__actions">
                <button className="ops-action-button ops-action-button--primary" disabled={identityBackfillBusy || matcherRunBusy} type="button" onClick={() => void runIdentityBackfill()}>
                  <RefreshCcw aria-hidden="true" size={13} />{identityBackfillBusy ? '正在回填' : '补齐身份'}
                </button>
                <button className="ops-link" disabled={identityBackfillBusy || matcherRunBusy} type="button" onClick={() => void runMatcher()}>
                  <Send aria-hidden="true" size={13} />{matcherRunBusy ? '正在匹配' : '运行一批匹配'}
                </button>
                <select aria-label="按身份状态筛选" value={identityStatus} onChange={(event) => { const next = event.target.value as RssIdentityStatus; setIdentityStatus(next); setOffset(0); syncUrlState({ identityStatus: next, offset: 0 }); }}>
                  <option value="">全部身份</option>
                  <option value="identified">已识别</option>
                  <option value="conflict">候选冲突</option>
                  <option value="unidentified">未识别</option>
                </select>
              </div>
              <p className="rss-identity-run" role="status" title={summary.lastIdentityBackfillAt ? exactTimeLabel(summary.lastIdentityBackfillAt) : undefined}>{identityBackfillLabel(summary)}</p>
              <p className="rss-identity-run" role="status" title={summary.lastMatchAt ? exactTimeLabel(summary.lastMatchAt) : undefined}>{matcherLabel(summary)}</p>
            </div>
          </details>
        </div>

        <details className="rss-source-disclosure">
          <summary><ServerCog size={15} /><span><strong>RSS 来源与设置</strong><small>{sources.length} 个来源 · 添加、测试或修改收集地址</small></span></summary>
        <aside className="rss-source-panel">
          <div className="rss-source-head">
            <div><ServerCog size={17} /><span><small>来源管理</small><strong>{sources.length} 个 RSS</strong></span></div>
            <button className="ops-link" disabled={saving || Boolean(testingSourceId)} type="button" onClick={openCreate}><Plus size={14} />添加来源</button>
          </div>

          {formOpen && (
            <div className="rss-source-form">
              <div className="rss-source-form__title"><strong>{editing ? '编辑来源' : '添加 RSS 来源'}</strong><button aria-label="关闭来源表单" disabled={saving} title="关闭来源表单" type="button" onClick={() => setFormOpen(false)}><X aria-hidden="true" size={15} /></button></div>
              <label>来源名称<input disabled={saving} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：主站 RSS" /></label>
              <label>私人 RSS 地址<input autoComplete="off" className="monospace-text" disabled={saving} type="password" value={form.feedUrl || ''} onChange={(event) => setForm({ ...form, feedUrl: event.target.value })} placeholder={editing ? '留空保持原地址' : 'https://…?passkey=…'} /></label>
              <div className="rss-source-form__pair">
                <label>
                  轮询周期
                  <div className={isPresetInterval(form.intervalMinutes) ? 'rss-source-form__interval' : 'rss-source-form__interval is-custom'}>
                    <select
                      aria-label="轮询周期选项"
                      disabled={saving}
                      value={isPresetInterval(form.intervalMinutes) ? String(form.intervalMinutes) : 'custom'}
                      onChange={(event) => {
                        const value = event.target.value;
                        setForm({ ...form, intervalMinutes: value === 'custom' ? (isPresetInterval(form.intervalMinutes) ? 10 : form.intervalMinutes) : Number(value) });
                      }}
                    >
                      <option value={1}>1 分钟</option>
                      <option value={3}>3 分钟</option>
                      <option value={5}>5 分钟</option>
                      <option value="custom">自定义</option>
                    </select>
                  {!isPresetInterval(form.intervalMinutes) && (
                    <input
                      aria-label="自定义轮询分钟数"
                      disabled={saving}
                      max={1440}
                      min={1}
                      placeholder="分钟"
                      step={1}
                      type="number"
                      value={form.intervalMinutes}
                      onChange={(event) => setForm({ ...form, intervalMinutes: event.currentTarget.valueAsNumber || 0 })}
                    />
                  )}
                  </div>
                </label>
                <label>保留时间<select disabled={saving} value={form.retentionDays} onChange={(event) => setForm({ ...form, retentionDays: Number(event.target.value) as 3 | 7 | 14 })}><option value={3}>3 天</option><option value={7}>7 天</option><option value={14}>14 天</option></select></label>
              </div>
              <label className="rss-source-check"><input checked={form.enabled} disabled={saving} type="checkbox" onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用这个来源</label>
              <label className="rss-source-check"><input checked={form.allowHttp} disabled={saving} type="checkbox" onChange={(event) => setForm({ ...form, allowHttp: event.target.checked })} />允许 HTTP 或非标准端口</label>
              <button className="ops-action-button ops-action-button--primary" disabled={saving || !form.name.trim() || !isValidInterval(form.intervalMinutes) || (!editing && !form.feedUrl?.trim())} type="button" onClick={submitSource}>{saving ? '正在保存' : '保存来源'}</button>
              <p>RSS 地址会明文保存在 SQLite，但页面、接口和日志不会回显完整 Passkey。</p>
            </div>
          )}

          <div className="rss-source-list">
            {sources.length === 0 && !formOpen && <div className="ops-empty">还没有 RSS 来源。添加后也不会自动访问，直到收集开关开启。</div>}
            {sources.map((source) => (
              <article className="rss-source-card" key={source.id}>
                <div className="rss-source-card__top">
                  <span className={source.lastError ? 'rss-source-light rss-source-light--error' : source.enabled ? 'rss-source-light' : 'rss-source-light rss-source-light--off'} />
                  <div><strong>{source.name}</strong><small>{source.domain}</small></div>
                  <span>{source.intervalMinutes}m</span>
                </div>
                <div className="rss-source-card__meta">
                  <span><Clock3 size={12} />保留 {source.retentionDays} 天</span>
                  <span>{source.lastSuccessAt ? <RelativeTime value={source.lastSuccessAt} /> : '尚未收集'}</span>
                </div>
                {source.lastError && <p>{source.lastError}</p>}
                <div className="rss-source-card__actions">
                  <button disabled={saving || Boolean(testingSourceId)} type="button" onClick={() => void runTest(source)}>{testingSourceId === source.id ? '测试中…' : '测试'}</button>
                  <button disabled={saving || Boolean(testingSourceId)} type="button" onClick={() => openEdit(source)}><Edit3 size={12} />编辑</button>
                  <button className="rss-source-delete" disabled={saving || Boolean(testingSourceId)} type="button" onClick={() => setDeleteTarget(source)}><Trash2 size={12} />删除</button>
                </div>
              </article>
            ))}
          </div>
        </aside>
        </details>
      </section>

      <ConfirmDialog busy={saving} labelledBy="rss-delete-title" describedBy="rss-delete-description" open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)}>
        {deleteTarget && (
          <>
          <span className="ops-confirm-dialog__signal">删除本地来源</span>
          <h2 id="rss-delete-title">删除“{deleteTarget.name}”？</h2>
          <p id="rss-delete-description">这会删除该来源在 Fluxa 内保存的种子索引，不会修改 PT 站点上的任何数据。</p>
          <div className="ops-confirm-dialog__meta"><span>来源</span><strong>{deleteTarget.domain}</strong><span>影响</span><strong>本地索引</strong></div>
          <div className="ops-confirm-dialog__actions"><button className="ops-action-button" disabled={saving} type="button" onClick={() => setDeleteTarget(null)}>取消</button><button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus disabled={saving} type="button" onClick={confirmDelete}>{saving ? '正在删除' : '确认删除'}</button></div>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        busy={Boolean(matchBusy)}
        labelledBy="rss-resource-download-title"
        describedBy="rss-resource-download-description"
        open={Boolean(resourceDownloadTarget)}
        onClose={() => setResourceDownloadTarget(null)}
      >
        {resourceDownloadTarget && (
          <>
            <span className="ops-confirm-dialog__signal">自动分类下载</span>
            <h2 id="rss-resource-download-title">按分类提交这个 RSS 资源到 qB？</h2>
            <p id="rss-resource-download-description">
              Fluxa 会把资源放入 Torra 的分类下载目录。下载完成后由现有 mover 和秒传插件继续处理，资源原始记录不会被删除。
              {resourceContext.episodeNumber != null && classifyRssResourceScope(resourceDownloadTarget.item) === 'season_pack'
                ? ' 这是整季包，会同时下载本季其他集。'
                : ''}
            </p>
            <div className="ops-confirm-dialog__meta">
              <span>作品</span><strong>{resourceDownloadTarget.item.mediaTitle || resourceDownloadTarget.item.sourceTitle || resourceDownloadTarget.item.title}</strong>
              <span>范围</span><strong>{resourceDownloadTarget.preview.scopeLabel || '已确认资源范围'}</strong>
              {resourceContext.episodeNumber != null && classifyRssResourceScope(resourceDownloadTarget.item) === 'season_pack' && (
                <><span>单集提醒</span><strong className="rss-confirm-warning">整季包，会包含本季其他集</strong></>
              )}
              <span>自动分类</span><strong>{resourceDownloadTarget.preview.categoryDirectory || resourceDownloadTarget.preview.categoryLabel || '待确认'}</strong>
              <span>分类依据</span><strong>{resourceDownloadTarget.preview.classificationReason || '已核验媒体身份'}</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" type="button" onClick={() => setResourceDownloadTarget(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus type="button" onClick={confirmResourceDownload}>
                <Download size={13} />确认提交 qB
              </button>
            </div>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        busy={Boolean(matchBusy)}
        labelledBy="rss-exact-download-title"
        describedBy="rss-exact-download-description"
        open={Boolean(exactDownloadTarget)}
        onClose={() => setExactDownloadTarget(null)}
      >
        {exactDownloadTarget && (
          <>
            <span className="ops-confirm-dialog__signal">人工精准下载</span>
            <h2 id="rss-exact-download-title">提交这个唯一冠军到 qB？</h2>
            <p id="rss-exact-download-description">Fluxa 会使用 Torra 订阅当前配置的保存目录和下载器；只有 Torra 明确提供 qB 分类时才会一并提交。当前媒体文件与 RSS 原始记录不会被删除。</p>
            <div className="ops-confirm-dialog__meta">
              <span>作品</span><strong>{exactDownloadTarget.group.title || '已匹配追更作品'}</strong>
              <span>覆盖范围</span><strong>{exactDownloadTarget.preview.episodeLabel || exactDownloadTarget.group.episodeLabel || `${exactDownloadTarget.preview.coveredUnitCount ?? 1} 集`}</strong>
              <span>分数提升</span><strong>{typeof exactDownloadTarget.preview.scoreGain === 'number' ? scoreLabel(exactDownloadTarget.preview.scoreGain) : '已通过严格高分校验'}</strong>
              <span>qB 分类</span><strong>{exactDownloadTarget.preview.downloadCategory || '未设置 · 按订阅目录提交'}</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" type="button" onClick={() => setExactDownloadTarget(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus type="button" onClick={confirmExactDownload}>
                <Download size={13} />确认提交 qB
              </button>
            </div>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        busy={Boolean(matchBusy)}
        labelledBy="rss-download-title"
        describedBy="rss-download-description"
        open={Boolean(downloadTarget)}
        onClose={() => setDownloadTarget(null)}
      >
        {downloadTarget && (
          <>
            <span className="ops-confirm-dialog__signal">人工确认交给 Torra</span>
            <h2 id="rss-download-title">把检查出的升级版本交给 Torra？</h2>
            <p id="rss-download-description">Torra 会再次核对当前追更、观察单元和分析结果；原有高质量文件不会在这里被删除。</p>
            <div className="ops-confirm-dialog__meta">
              <span>作品</span><strong>{downloadTarget.match.subscriptionTitle || downloadTarget.match.itemTitle || '已匹配追更作品'}</strong>
              <span>候选</span><strong>{downloadTarget.analysis.result?.selectedCount ?? 0} 个升级版本</strong>
            </div>
            <div className="ops-confirm-dialog__actions">
              <button className="ops-action-button" type="button" onClick={() => setDownloadTarget(null)}>取消</button>
              <button className="ops-action-button ops-action-button--primary" data-dialog-initial-focus type="button" onClick={confirmMatchDownload}>确认交给 Torra</button>
            </div>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog className="rss-detail-dialog" labelledBy="rss-detail-title" describedBy="rss-detail-description" open={Boolean(detailItem)} onClose={closeItemDetail}>
        {detailItem && (
          <>
            <header className="rss-detail-head">
              <div>
                <span>本地种子详情</span>
                <h2 id="rss-detail-title">{detailItem.title}</h2>
              </div>
              <button aria-label="关闭种子详情" data-dialog-initial-focus title="关闭" type="button" onClick={closeItemDetail}><X aria-hidden="true" size={18} /></button>
            </header>
            {detailLoading && <div className="rss-detail-notice"><RefreshCcw className="rss-spin" size={14} />正在读取完整信息</div>}
            {detailError && <div className="rss-detail-notice rss-detail-notice--error"><AlertTriangle size={14} />{detailError}</div>}
            <div className="rss-detail-grid">
              <span>来源</span><strong>{detailItem.sourceName}</strong>
              <span>发布时间</span><strong>{exactTimeLabel(detailItem.publishedAt)}</strong>
              <span>类型与范围</span><strong>{episodeLabel(detailItem)} · {rssResourceScopeLabel(classifyRssResourceScope(detailItem))}</strong>
              <span>大小</span><strong>{sizeLabel(detailItem.sizeBytes)}</strong>
              <span>分类</span><strong>{detailItem.category || '未提供'}</strong>
              <span>版本</span><strong>{detailItem.versionSummary || '未提取'}</strong>
              <span>TMDB</span><strong>{detailItem.tmdbId || '未识别'}</strong>
              <span>IMDb</span><strong>{detailItem.imdbId || '未识别'}</strong>
              <span>身份状态</span><strong className={`rss-detail-status rss-detail-status--${detailItem.identityStatus}`}>{identityLabel(detailItem.identityStatus)}</strong>
              <span>身份来源</span><strong>{identitySourceLabel(detailItem.identitySource)}</strong>
              <span>置信度</span><strong>{identityConfidenceLabel(detailItem.identityConfidence)}</strong>
              <span>最近确认</span><strong>{exactTimeLabel(detailItem.identityUpdatedAt)}</strong>
              <span>匹配原因</span><strong>{rssMatchMethodLabel(detailItem.matchMethod, detailItem.matchConfidence)}</strong>
              <span>官种</span><strong>无法判断</strong>
              <span>下载 / 入库 / 重复</span><strong>尚未确认</strong>
              <span>当前处理状态</span><strong>{seedProcessingStateLabel(
                detailItem.followState,
                matchByItemId.get(detailItem.id),
                resourceDownloadActions[detailItem.id] || (matchByItemId.get(detailItem.id) ? matchActions[matchByItemId.get(detailItem.id)!.id] : undefined),
                detailItem.resourceDownloadStatus
              )}</strong>
            </div>
            <section className="rss-detail-description" aria-labelledby="rss-detail-description-title">
              <h3 id="rss-detail-description-title">RSS 简介</h3>
              <p id="rss-detail-description">{detailItem.description || '该 RSS 条目没有提供简介。'}</p>
            </section>
            <p className="rss-detail-security">出于安全考虑，Fluxa 不向浏览器返回下载地址、详情地址或 Passkey。</p>
          </>
        )}
      </ConfirmDialog>
    </main>
  );
}
