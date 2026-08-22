/*
PerformanceHub — full-page Performance tab on Dashboard.

Surfaces:
  1. Editorial header + period selector (All / 30d / 7d)
  2. KPI strip — P/L, ROI, Win rate vs expected, Avg CLV (PerfSummary, size lg)
  3. Open positions — pending bets summary (count, risk, potential return)
  4. By market — sortable table, click row → SegmentDetail
  5. By league — sortable table, click row → SegmentDetail
  6. Recent activity — last 6 bets, click → BetDetail
  7. Full log link → BetLog

All data comes from existing endpoints:
  - GET /api/bets/performance?since=ISO  → KPIs + by_market + by_league
  - GET /api/bets?limit=200             → bet list (filtered client-side)

No backend changes needed. since= param is computed from the selected period.
*/

import { useState, useEffect, useMemo } from 'react';
import { footballAPI, describeApiError } from '../services/api';
import PerfSummary from './PerfSummary';
import BetRow from './BetRow';
import SegmentDetail from './SegmentDetail';
import BetDetail from './BetDetail';
import BetLog from './BetLog';
import { COMPETITION_LABELS } from '../utils/constants';
import { MARKET_BADGE } from '../utils/marketBadges';

const PERIODS = [
    { id: 'all',  label: 'All',    days: null },
    { id: '30d',  label: '30d',    days: 30  },
    { id: '7d',   label: '7d',     days: 7   },
];

const pct = (v) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
const nok = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()} NOK`;

function isoSinceFromDays(days) {
    if (!days) return null;
    const dt = new Date();
    dt.setDate(dt.getDate() - days);
    return dt.toISOString();
}

// Friendly market label for segment tables. MARKET_BADGE itself intentionally
// omits h2h (the implicit default on bet rows shouldn't get a chip), but in the
// "By market" segment list a lowercase "h2h" / "combo" reads as a dev label —
// so we resolve those two special cases here.
function segmentLabel(key) {
    return MARKET_BADGE[key]
        || (key === 'h2h' ? 'Match result' : key === 'combo' ? 'Combo' : key);
}

function PerformanceHub({ canEdit = false }) {
    const [period, setPeriod] = useState('all');
    const [perf, setPerf] = useState(null);
    const [bets, setBets] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // Drill-in state
    const [segment, setSegment] = useState(null);     // { type: 'market'|'league', key, label }
    const [selectedBet, setSelectedBet] = useState(null);
    const [showLog, setShowLog] = useState(false);

    const since = useMemo(() => {
        const p = PERIODS.find(x => x.id === period);
        return isoSinceFromDays(p?.days);
    }, [period]);

    // Bets list — independent of period. Fetch once on mount; bets don't
    // change when the user toggles 7d / 30d / All (the period only constrains
    // the perf aggregation, not which bets are shown in Recent Activity).
    useEffect(() => {
        const controller = new AbortController();
        footballAPI.listBets({ limit: 200 }, { signal: controller.signal })
            .then(setBets)
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(prev => prev || (err.message || 'Failed to load bets'));
            });
        return () => controller.abort();
    }, []);

    // Perf aggregation — re-fetched when period changes. Kept separate from
    // the bets fetch so toggling period doesn't burn a /api/bets round-trip.
    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError(null);
        const perfParams = since ? { since } : {};
        footballAPI.getBetsPerformance(perfParams, { signal: controller.signal })
            .then(setPerf)
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(describeApiError(err, 'Failed to load performance'));
            })
            .finally(() => setLoading(false));
        return () => controller.abort();
    }, [since]);

    const refreshAll = async () => {
        const perfParams = since ? { since } : {};
        const [p, b] = await Promise.all([
            footballAPI.getBetsPerformance(perfParams),
            footballAPI.listBets({ limit: 200 }),
        ]);
        setPerf(p);
        setBets(b);
    };

    const handleDelete = async (betId) => {
        if (!confirm('Delete this bet?')) return;
        await footballAPI.deleteBet(betId);
        await refreshAll();
    };

    // Settle-now status: 'idle' | 'running' | { n: <count> }
    const [settleStatus, setSettleStatus] = useState('idle');
    const handleSettleNow = async () => {
        setSettleStatus('running');
        try {
            const r = await footballAPI.settleBets();
            await refreshAll();
            setSettleStatus({ n: r.settled || 0 });
            setTimeout(() => setSettleStatus('idle'), 4000);
        } catch (e) {
            setSettleStatus('idle');
            setError(e.message || 'Settle failed');
        }
    };

    // Pending bets — for "Open positions" strip
    const pending = useMemo(() => {
        if (!bets?.bets) return { count: 0, risk: 0, potential: 0 };
        const p = bets.bets.filter(b => b.status === 'pending');
        const risk = p.reduce((s, b) => s + (b.stake || 0), 0);
        const potential = p.reduce((s, b) => s + (b.stake || 0) * (b.odds_at_bet || 0), 0);
        return { count: p.length, risk, potential };
    }, [bets]);

    // Activity feed — bets settled in the last 48h, newest first. Surfaces
    // weekend results without scrolling through the full bet log.
    // Date.now() inside useMemo trips the react-hooks/purity rule; safe here
    // because cutoff is only read for filtering — a slight re-render drift
    // never causes the feed contents to flip mid-frame.
    const activityFeed = useMemo(() => {
        if (!bets?.bets) return [];
        // eslint-disable-next-line react-hooks/purity
        const cutoff = Date.now() - 48 * 60 * 60 * 1000;
        return bets.bets
            .filter(b => b.settled_at && new Date(b.settled_at).getTime() >= cutoff)
            .sort((a, b) => new Date(b.settled_at) - new Date(a.settled_at))
            .slice(0, 5);
    }, [bets]);

    // Recent activity — most recent 6 bets (API returns newest first)
    const recent = useMemo(() => (bets?.bets || []).slice(0, 6), [bets]);

    if (loading && !perf) {
        return (
            <div className="text-center py-20">
                <div className="inline-block w-3 h-3 bg-accent animate-pulse-soft rounded-full mb-4" />
                <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">Loading performance</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-paper-tint border-l-2 border-danger p-6">
                <div className="eyebrow text-danger mb-1">Failed to load</div>
                <p className="text-ink-soft text-sm">{error}</p>
            </div>
        );
    }

    return (
        <div className="space-y-8 sm:space-y-10">
            {/* Header + period selector */}
            <header className="flex flex-wrap items-end justify-between gap-4 pb-4 border-b border-line">
                <div>
                    <div className="eyebrow mb-2">Tracking</div>
                    <h2 className="display text-4xl text-ink leading-[1] font-light">
                        Performance<span className="text-accent">.</span>
                    </h2>
                    <PeriodSubtitle perf={perf} period={period} />
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    {canEdit && (
                        <button
                            onClick={handleSettleNow}
                            disabled={settleStatus === 'running'}
                            title="Force-settle any pending bets whose match has finished. Runs implicitly on every refresh too — this is just the explicit trigger."
                            className="mono text-[0.65rem] uppercase tracking-[0.1em] px-3 py-2 sm:py-1.5 border border-line text-ink-soft hover:text-ink hover:border-ink-muted transition-colors cursor-pointer disabled:opacity-50 mr-1"
                        >
                            {settleStatus === 'running'
                                ? 'Settling…'
                                : (typeof settleStatus === 'object' ? `Settled ${settleStatus.n} ✓` : 'Settle now')}
                        </button>
                    )}
                    <span className="eyebrow mr-2 hidden sm:inline">Period</span>
                    {PERIODS.map(p => (
                        <button
                            key={p.id}
                            onClick={() => setPeriod(p.id)}
                            className={
                                'mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 sm:py-1.5 border transition-colors cursor-pointer ' +
                                (period === p.id
                                    ? 'bg-ink text-paper border-ink'
                                    : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                            }
                        >
                            {p.label}
                        </button>
                    ))}
                </div>
            </header>

            {/* KPI strip */}
            <PerfSummary perf={perf} size="lg" showFootnote />

            {/* Open positions — stacked on mobile (3 rows), inline on desktop. */}
            {pending.count > 0 && (
                <section>
                    <div className="eyebrow mb-3">Open positions</div>
                    <div className="bg-paper-tint border-l-2 border-accent px-4 py-3">
                        <div className="mono text-sm text-ink flex flex-col sm:flex-row sm:items-baseline sm:gap-2 gap-y-0.5">
                            <span><span className="text-ink">{pending.count}</span> pending</span>
                            <span className="hidden sm:inline text-ink-muted">·</span>
                            <span><span className="text-ink-soft">{Math.round(pending.risk).toLocaleString()} NOK</span> at risk</span>
                            <span className="hidden sm:inline text-ink-muted">·</span>
                            <span><span className="text-accent">{Math.round(pending.potential).toLocaleString()} NOK</span> potential return</span>
                        </div>
                    </div>
                </section>
            )}

            {/* Activity feed — last 48h of settled bets. Shows the immediate
                aftermath of a weekend without scrolling the whole log. Hidden
                when nothing has settled recently to keep the hub uncluttered. */}
            {activityFeed.length > 0 && (
                <section>
                    <div className="flex items-baseline justify-between mb-3">
                        <div className="eyebrow">Activity feed</div>
                        <div className="mono text-[0.65rem] text-ink-muted">
                            settled in last 48h
                        </div>
                    </div>
                    <ul className="border border-line divide-y divide-line-soft">
                        {activityFeed.map(b => (
                            <ActivityRow key={b.id} bet={b} onSelect={setSelectedBet} />
                        ))}
                    </ul>
                </section>
            )}

            {/* By market */}
            <SegmentSection
                title="By market"
                rows={Object.entries(perf?.by_market || {}).map(([key, agg]) => ({
                    key,
                    label: segmentLabel(key),
                    ...agg,
                    expected: perf?.expected_win_rate,
                }))}
                onSelect={(row) => setSegment({
                    type: 'market', key: row.key,
                    label: segmentLabel(row.key),
                })}
                emptyHint="No settled bets in this period yet."
            />

            {/* By league */}
            <SegmentSection
                title="By league"
                rows={Object.entries(perf?.by_league || {}).map(([key, agg]) => ({
                    key,
                    label: COMPETITION_LABELS[key] || key,
                    ...agg,
                }))}
                onSelect={(row) => setSegment({ type: 'league', key: row.key, label: row.label })}
                emptyHint={
                    perf?.by_league_excludes_combos
                        ? 'Per-league split shows singles only — combo P/L is in the top-line ROI.'
                        : 'No settled bets in this period yet.'
                }
                showCombosNote={perf?.by_league_excludes_combos && Object.keys(perf?.by_league || {}).length > 0}
            />

            {/* Recent activity */}
            <section>
                <div className="flex items-baseline justify-between mb-3">
                    <div>
                        <div className="eyebrow">Recent activity</div>
                    </div>
                    <button
                        onClick={() => setShowLog(true)}
                        className="mono text-[0.7rem] uppercase tracking-[0.1em] text-accent hover:text-accent-soft border-b border-accent/40 transition-colors cursor-pointer"
                    >
                        Full bet log →
                    </button>
                </div>
                {recent.length === 0 ? (
                    <div className="text-center py-12 max-w-md mx-auto">
                        <div className="eyebrow mb-3">Quiet log</div>
                        <h3 className="display text-2xl text-ink mb-3">
                            No bets logged yet<span className="text-accent">.</span>
                        </h3>
                        <p className="text-ink-soft text-sm">
                            First settled bet populates this view. Use the Value tab or Best Picks
                            combo builder to log paper bets and track ROI / CLV over time.
                        </p>
                    </div>
                ) : (
                    <div className="space-y-1.5">
                        {recent.map(b => (
                            <BetRow
                                key={b.id}
                                bet={b}
                                onSelect={setSelectedBet}
                                onDelete={canEdit ? handleDelete : null}
                                dense
                            />
                        ))}
                    </div>
                )}
            </section>

            {/* Drill-ins */}
            {segment && (
                <SegmentDetail
                    segment={segment}
                    onClose={() => setSegment(null)}
                    onSelectBet={setSelectedBet}
                />
            )}

            {selectedBet && (
                <BetDetail
                    bet={selectedBet}
                    onClose={() => setSelectedBet(null)}
                    onDelete={canEdit ? (id) => { handleDelete(id); setSelectedBet(null); } : null}
                />
            )}

            {showLog && (
                <BetLog
                    onClose={() => setShowLog(false)}
                    canEdit={canEdit}
                    onSelectBet={setSelectedBet}
                    onChange={refreshAll}
                />
            )}
        </div>
    );
}

function PeriodSubtitle({ perf, period }) {
    if (!perf || perf.total_bets === 0) {
        return (
            <p className="text-ink-soft text-sm mt-2 max-w-2xl font-light">
                No bets in this period<span className="text-accent">.</span>
            </p>
        );
    }
    const periodLabel = period === 'all' ? 'all-time'
                      : period === '30d' ? 'last 30 days'
                      : 'last 7 days';
    const leagues = perf.by_league ? Object.keys(perf.by_league).length : 0;
    const leagueWord = leagues === 1 ? 'league' : 'leagues';
    return (
        <p className="text-ink-soft text-sm mt-2 max-w-2xl font-light">
            Across <span className="text-ink mono">{perf.settled_count}</span> settled bets
            {leagues > 0 && (
                <> in <span className="text-ink mono">{leagues}</span> {leagueWord}</>
            )}
            {' '}({periodLabel})<span className="text-accent">.</span>
        </p>
    );
}

// Shared market/league segment table. Two layouts share data:
//   - Mobile (<sm): each row is a stacked card (segment name + 2x2 metric grid)
//   - Desktop (sm+): traditional 6-col table with header row
// The 6-col table never fit on a phone — values truncated to ellipsis at
// best, completely unreadable at worst. Cards trade horizontal scanning
// for vertical scrolling, which is what mobile users do anyway.
function SegmentSection({ title, rows, onSelect, emptyHint, showCombosNote = false }) {
    const sorted = [...rows].sort((a, b) => (b.stake || 0) - (a.stake || 0));
    return (
        <section>
            <div className="flex items-baseline justify-between mb-3">
                <div className="eyebrow">{title}</div>
                <div className="mono text-[0.65rem] text-ink-muted">
                    {sorted.length} {sorted.length === 1 ? 'segment' : 'segments'}
                </div>
            </div>
            {sorted.length === 0 ? (
                <div className="bg-paper-tint border border-line px-4 py-6 text-center">
                    <p className="text-ink-soft text-sm">{emptyHint}</p>
                </div>
            ) : (
                <>
                    {/* Mobile cards */}
                    <div className="sm:hidden space-y-2">
                        {sorted.map(row => (
                            <SegmentCard key={row.key} row={row} onSelect={onSelect} />
                        ))}
                    </div>
                    {/* Desktop table */}
                    <div className="hidden sm:block border border-line">
                        <div className="grid grid-cols-[1.4fr_repeat(4,minmax(0,1fr))_auto] gap-4 px-4 py-2 bg-paper-tint border-b border-line">
                            <div className="eyebrow">Segment</div>
                            <div className="eyebrow text-right">Bets</div>
                            <div className="eyebrow text-right">Stake</div>
                            <div className="eyebrow text-right">P/L</div>
                            <div className="eyebrow text-right">ROI</div>
                            <div className="eyebrow text-right pl-3">Win rate</div>
                        </div>
                        {sorted.map(row => (
                            <button
                                key={row.key}
                                onClick={() => onSelect(row)}
                                className="w-full grid grid-cols-[1.4fr_repeat(4,minmax(0,1fr))_auto] gap-4 px-4 py-3 border-b border-line-soft last:border-b-0 text-left hover:bg-paper-tint transition-colors cursor-pointer items-baseline group"
                            >
                                <div className="text-ink text-sm flex items-center gap-2 min-w-0">
                                    <span className="truncate">{row.label}</span>
                                    <span className="text-accent opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true">→</span>
                                </div>
                                <div className="mono text-sm text-ink text-right">{row.count ?? 0}</div>
                                <div className="mono text-sm text-ink-soft text-right">{Math.round(row.stake || 0).toLocaleString()}</div>
                                <div className={
                                    'mono text-sm font-semibold text-right ' +
                                    (row.pl > 0 ? 'text-positive' : row.pl < 0 ? 'text-danger' : 'text-ink-muted')
                                }>
                                    {nok(row.pl)}
                                </div>
                                <div className={
                                    'mono text-sm font-semibold text-right ' +
                                    (row.roi > 0 ? 'text-positive' : row.roi < 0 ? 'text-danger' : 'text-ink-muted')
                                }>
                                    {pct(row.roi)}
                                </div>
                                <div className="mono text-sm text-ink text-right pl-3">
                                    {pct(row.win_rate)}
                                </div>
                            </button>
                        ))}
                    </div>
                </>
            )}
            {showCombosNote && (
                <p className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted mt-2">
                    Combos excluded — their match anchors to one league, skewing the per-league split.
                </p>
            )}
        </section>
    );
}

function SegmentCard({ row, onSelect }) {
    const plClass = row.pl > 0 ? 'text-positive' : row.pl < 0 ? 'text-danger' : 'text-ink-muted';
    const roiClass = row.roi > 0 ? 'text-positive' : row.roi < 0 ? 'text-danger' : 'text-ink-muted';
    return (
        <button
            onClick={() => onSelect(row)}
            className="w-full bg-paper border border-line hover:border-ink-muted px-3 py-3 text-left transition-colors cursor-pointer block"
        >
            <div className="flex items-baseline justify-between gap-2 mb-2">
                <div className="text-ink text-sm font-medium truncate flex items-center gap-1.5">
                    {row.label}
                    <span className="text-accent" aria-hidden="true">→</span>
                </div>
                <div className="mono text-[0.65rem] text-ink-muted whitespace-nowrap">
                    {row.count ?? 0} bets · {Math.round(row.stake || 0).toLocaleString()} NOK
                </div>
            </div>
            <div className="grid grid-cols-3 gap-3 pt-2 border-t border-line-soft">
                <div>
                    <div className="mono text-[0.55rem] uppercase tracking-[0.12em] text-ink-muted">P/L</div>
                    <div className={'mono text-sm font-semibold ' + plClass}>{nok(row.pl)}</div>
                </div>
                <div>
                    <div className="mono text-[0.55rem] uppercase tracking-[0.12em] text-ink-muted">ROI</div>
                    <div className={'mono text-sm font-semibold ' + roiClass}>{pct(row.roi)}</div>
                </div>
                <div>
                    <div className="mono text-[0.55rem] uppercase tracking-[0.12em] text-ink-muted">Win</div>
                    <div className="mono text-sm text-ink">{pct(row.win_rate)}</div>
                </div>
            </div>
        </button>
    );
}

// One row in the Activity feed — compact: which match, what outcome,
// won/lost chip, P/L, relative time ('2h ago'). Click opens BetDetail.
function ActivityRow({ bet, onSelect }) {
    const isCombo = bet.market === 'combo' && Array.isArray(bet.combo_legs) && bet.combo_legs.length > 0;
    const m = bet.match;
    const statusColor = bet.status === 'won' ? 'text-positive'
                      : bet.status === 'lost' ? 'text-danger'
                      : 'text-ink-muted';
    const plColor = bet.profit_loss > 0 ? 'text-positive'
                  : bet.profit_loss < 0 ? 'text-danger'
                  : 'text-ink-muted';
    return (
        <li>
            <button
                onClick={() => onSelect?.(bet)}
                className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-paper-tint transition-colors cursor-pointer"
            >
                <div className="min-w-0 flex-1">
                    <div className="text-sm text-ink truncate">
                        {isCombo
                            ? `${bet.combo_legs.length}-leg combo`
                            : <>{m?.home} <span className="text-ink-muted">vs</span> {m?.away}</>
                        }
                    </div>
                    <div className="mono text-[0.6rem] text-ink-muted uppercase tracking-[0.08em] mt-0.5">
                        {isCombo
                            ? `${bet.combo_legs.length} legs · @ ${bet.odds_at_bet?.toFixed(2)}`
                            : (bet.outcome_label || bet.outcome_key)}
                        {' · '}{relativeTime(bet.settled_at)}
                    </div>
                </div>
                <div className="text-right shrink-0">
                    <div className={'mono text-[0.65rem] uppercase tracking-[0.08em] font-semibold ' + statusColor}>
                        {bet.status}
                    </div>
                    <div className={'mono text-sm font-semibold ' + plColor}>
                        {bet.profit_loss != null ? nok(bet.profit_loss) : '—'}
                    </div>
                </div>
            </button>
        </li>
    );
}

function relativeTime(iso) {
    if (!iso) return '';
    const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
    if (seconds < 60) return 'just now';
    const mins = Math.floor(seconds / 60);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

export default PerformanceHub;
