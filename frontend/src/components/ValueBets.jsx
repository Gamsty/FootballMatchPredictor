/*
ValueBets — table of +EV picks (model probability × bookmaker odds − 1).

Rendered when the Dashboard "Value" tab is active.

What it shows beyond raw edge:
  - edge_median (gated by default) — robust to outlier prices
  - edge_best — what you'd actually realise at the best book; warned when >20%
  - market badge (1X2 / Over 2.5 / BTTS) when multi-market mode is on
  - overround per match — bookmaker margin signal
  - ensemble agreement — model-internal confidence proxy
  - stake in NOK based on user-provided bankroll (persists to localStorage)
  - auto-refresh every 5 min so the user isn't staring at stale odds

Falls back to a paper-aesthetic "not configured" panel when the backend
reports enabled=false (ODDS_API_KEY missing).
*/

import { useState, useEffect, useRef } from 'react';
import { footballAPI } from '../services/api';
import { formatTime, formatMatchDate, COMPETITION_LABELS } from '../utils/constants';

const pct = (v) => `${(v * 100).toFixed(1)}%`;
const FRACTIONAL_KELLY = 0.25;
const SUSPICIOUS_EDGE = 0.20;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const BANKROLL_STORAGE_KEY = 'fmp.bankroll.nok';

// Per-market display labels — keeps the UI compact when multi-market is on.
// BTTS is intentionally omitted: The Odds API only exposes BTTS via the
// per-event endpoint, which is 73x more expensive than bulk and currently
// not wired up. Backend silently drops the market if requested.
const MARKET_BADGE = {
    h2h:         '1X2',
    totals_2_5:  'O/U 2.5',
};

function ValueBets({ onSelectMatch }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [minEdge, setMinEdge] = useState(0.03);
    const [books, setBooks] = useState('sharp');
    const [pickToLog, setPickToLog] = useState(null);  // null or {pick, defaultStake}
    const [logStatus, setLogStatus] = useState({});    // {matchId-marketKey: 'logged' | 'error'}
    const [bankroll, setBankroll] = useState(() => {
        const stored = Number(localStorage.getItem(BANKROLL_STORAGE_KEY));
        return Number.isFinite(stored) && stored > 0 ? stored : 1000;
    });
    const lastFetched = useRef(0);

    // Persist bankroll across sessions — typical user has one number, no point
    // making them re-enter it every visit.
    useEffect(() => {
        localStorage.setItem(BANKROLL_STORAGE_KEY, String(bankroll));
    }, [bankroll]);

    // Fetch driver: re-runs when filter params change, also fires on mount.
    // We intentionally do NOT include bankroll in deps — staking is a pure
    // frontend computation and shouldn't trigger an API roundtrip.
    //
    // Three resilience features:
    //   1. AbortController — switching filters rapidly aborts the in-flight
    //      request so we don't pile up parallel calls and confuse react state
    //   2. Single auto-retry on transient errors (network blip, Azure cold start)
    //   3. Auto-refresh pauses when the tab is hidden — no point polling odds
    //      for a user who isn't looking
    useEffect(() => {
        let controller = new AbortController();
        let retryTimer = null;

        const fetchOnce = async (attempt = 0) => {
            // Always cancel any prior in-flight before starting a new one
            controller.abort();
            controller = new AbortController();
            setLoading(true);
            setError(null);
            try {
                const res = await footballAPI.getValueBets(
                    { days: 7, min_edge: minEdge, books },
                    { signal: controller.signal },
                );
                setData(res);
                lastFetched.current = Date.now();
                setError(null);
            } catch (err) {
                // Abort isn't an error — it means we started a new request on purpose
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                // One transient retry after 3s before surfacing the error
                if (attempt === 0) {
                    retryTimer = setTimeout(() => fetchOnce(1), 3000);
                    return;
                }
                setError(err.message || 'Failed to load value bets');
            } finally {
                setLoading(false);
            }
        };

        fetchOnce();

        // Auto-refresh every 5 min, but only when the tab is visible.
        // The interval handler checks visibility itself rather than swapping
        // intervals on visibilitychange — simpler and the cost of one ignored
        // tick per period is nothing.
        const interval = setInterval(() => {
            if (document.visibilityState === 'visible') fetchOnce();
        }, REFRESH_INTERVAL_MS);

        // When the tab becomes visible after being hidden, fire an immediate
        // refresh if our data is older than the refresh interval.
        const onVisibility = () => {
            if (document.visibilityState === 'visible' &&
                Date.now() - lastFetched.current > REFRESH_INTERVAL_MS) {
                fetchOnce();
            }
        };
        document.addEventListener('visibilitychange', onVisibility);

        return () => {
            controller.abort();
            clearInterval(interval);
            if (retryTimer) clearTimeout(retryTimer);
            document.removeEventListener('visibilitychange', onVisibility);
        };
    }, [minEdge, books]);

    if (loading && !data) {
        return (
            <div className="text-center py-20">
                <div className="inline-block w-3 h-3 bg-accent animate-pulse-soft rounded-full mb-4" />
                <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                    Scanning markets
                </p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-paper-tint border-l-2 border-danger p-6">
                <div className="eyebrow text-danger mb-1">Value bets unavailable</div>
                <p className="text-ink-soft">{error}</p>
            </div>
        );
    }

    if (!data?.enabled) {
        return (
            <div className="bg-paper-tint border-l-2 border-accent p-6 max-w-2xl">
                <div className="eyebrow mb-2">Odds integration</div>
                <h3 className="display text-2xl text-ink mb-3">Not configured yet<span className="text-accent">.</span></h3>
                <p className="text-ink-soft text-sm leading-relaxed mb-3">
                    Value detection requires live bookmaker odds. Set the{' '}
                    <span className="mono text-xs bg-paper px-1.5 py-0.5 border border-line">ODDS_API_KEY</span>{' '}
                    environment variable on the backend (free tier at{' '}
                    <a href="https://the-odds-api.com" target="_blank" rel="noopener noreferrer"
                       className="text-accent border-b border-accent/40 hover:border-accent">
                        the-odds-api.com
                    </a>{' '}— 500 requests/month).
                </p>
                <p className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                    {data?.meta?.message || 'Backend reported odds API disabled'}
                </p>
            </div>
        );
    }

    const picks = data.value_bets || [];

    // Group picks by match, then group matches by league. Within each league,
    // matches are ordered by kickoff (earliest first) — that's how a bettor
    // actually scans: "which league am I focusing on, what's coming up next".
    // Within a match, picks stay sorted by edge desc (best opportunity first).
    const matchesByLeague = new Map();   // competition → Map<match_id, picks[]>
    for (const pick of picks) {
        const comp = pick.competition || 'Other';
        if (!matchesByLeague.has(comp)) matchesByLeague.set(comp, new Map());
        const matchesInLeague = matchesByLeague.get(comp);
        if (!matchesInLeague.has(pick.match_id)) matchesInLeague.set(pick.match_id, []);
        matchesInLeague.get(pick.match_id).push(pick);
    }

    // Sort leagues by priority (Premier League first, then alphabetical)
    const LEAGUE_PRIORITY = [
        'Premier League', 'Championship', 'La Liga', 'Primera Division',
        'Bundesliga', 'Serie A', 'Ligue 1', 'Eredivisie',
        'Primeira Liga', 'UEFA Champions League',
    ];
    const sortedLeagues = Array.from(matchesByLeague.keys()).sort((a, b) => {
        const ia = LEAGUE_PRIORITY.indexOf(a);
        const ib = LEAGUE_PRIORITY.indexOf(b);
        if (ia !== -1 && ib !== -1) return ia - ib;
        if (ia !== -1) return -1;
        if (ib !== -1) return 1;
        return a.localeCompare(b);
    });

    // Within each league, sort matches by kickoff date (earliest first)
    const sortedMatchesPerLeague = sortedLeagues.map(league => {
        const matches = Array.from(matchesByLeague.get(league).entries());
        matches.sort(([, picksA], [, picksB]) =>
            new Date(picksA[0].date) - new Date(picksB[0].date));
        return { league, matches };
    });

    const quota = data.meta?.quota;
    const quotaLow = quota?.low;

    return (
        <div>
            {/* Controls strip */}
            <div className="flex flex-wrap items-center gap-3 mb-6 pb-4 border-b border-line">
                <div className="eyebrow">Min edge</div>
                <div className="flex items-center gap-1">
                    {[0.02, 0.03, 0.05, 0.10].map(v => (
                        <button
                            key={v}
                            onClick={() => setMinEdge(v)}
                            className={
                                'mono text-[0.7rem] uppercase tracking-[0.1em] px-2 py-1 border transition-colors cursor-pointer ' +
                                (minEdge === v
                                    ? 'bg-ink text-paper border-ink'
                                    : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                            }
                        >
                            {pct(v)}
                        </button>
                    ))}
                </div>

                <span className="w-px h-4 bg-line mx-1" aria-hidden="true" />

                <div className="eyebrow">Books</div>
                <div className="flex items-center gap-1">
                    {[
                        { id: 'sharp', label: 'Sharp', title: 'Pinnacle, exchanges, and liquid soft majors only — excludes grey-market books that drive fake edges.' },
                        { id: 'all',   label: 'All',   title: 'Includes every bookmaker. Expect inflated edges from palp errors and stale lines.' },
                    ].map(opt => (
                        <button
                            key={opt.id}
                            onClick={() => setBooks(opt.id)}
                            title={opt.title}
                            className={
                                'mono text-[0.7rem] uppercase tracking-[0.1em] px-2 py-1 border transition-colors cursor-pointer ' +
                                (books === opt.id
                                    ? 'bg-ink text-paper border-ink'
                                    : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                            }
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>

                <span className="w-px h-4 bg-line mx-1" aria-hidden="true" />

                <div className="eyebrow">Bankroll</div>
                <div className="flex items-center gap-1">
                    <input
                        type="number"
                        min="0"
                        step="100"
                        value={bankroll || ''}
                        onChange={(e) => {
                            // Defensively coerce: paste of negatives, NaN, or empty
                            // string all collapse to 0 (which disables stake display).
                            const v = Number(e.target.value);
                            setBankroll(Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);
                        }}
                        className="w-24 bg-paper border border-line px-2 py-1 mono text-xs text-ink focus:outline-none focus:border-accent transition-colors"
                        title="Your total bankroll in NOK. ¼-Kelly stakes are computed against this number. Stored locally — never sent to the backend."
                    />
                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted">NOK</span>
                </div>

                <div className="flex-1" />
                <div className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted text-right">
                    <div>
                        {picks.length} {picks.length === 1 ? 'pick' : 'picks'}
                        {' · '}
                        {data.meta?.matches_with_odds || 0}/{data.meta?.matches_scanned || 0} matched
                    </div>
                    {quota?.remaining != null && (
                        <div className={quotaLow ? 'text-warning' : 'text-ink-muted'}>
                            Quota: {quota.remaining} req remaining
                        </div>
                    )}
                </div>
            </div>

            {/* Empty state */}
            {picks.length === 0 && (
                <div className="text-center py-16 max-w-md mx-auto">
                    <div className="eyebrow mb-3">No value</div>
                    <h3 className="display text-2xl text-ink mb-3">
                        Market is efficient right now<span className="text-accent">.</span>
                    </h3>
                    <p className="text-ink-soft text-sm">
                        No bets clear the {pct(minEdge)} edge threshold across the next 7 days.
                        Try lowering the threshold, or check back after the next odds refresh.
                    </p>
                </div>
            )}

            {/* Picks — grouped by league with earliest kickoff first inside each */}
            {picks.length > 0 && (
                <div className="space-y-8">
                    {sortedMatchesPerLeague.map(({ league, matches }) => (
                        <div key={league}>
                            <div className="flex items-baseline gap-3 mb-3 pb-2 border-b border-line">
                                <h2 className="display text-xl text-ink">
                                    {COMPETITION_LABELS[league] || league}
                                </h2>
                                <span className="mono text-xs text-ink-muted">{matches.length}</span>
                            </div>
                            <div className="space-y-3">
                    {matches.map(([matchId, matchPicks]) => {
                        const first = matchPicks[0];
                        const agreement = first.ensemble_agreement;
                        return (
                            <div
                                key={matchId}
                                className="bg-paper border border-line hover:border-ink-muted transition-colors"
                            >
                                {/* Match header */}
                                <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-line bg-paper-tint">
                                    <div>
                                        <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mb-1">
                                            {COMPETITION_LABELS[first.competition] || first.competition}
                                            <span className="mx-2">·</span>
                                            {formatMatchDate(first.date)} {formatTime(first.date)}
                                            {agreement != null && (
                                                <span
                                                    className="ml-2"
                                                    title={`Ensemble agreement: ${pct(agreement)}. High (>85%) = base models agree, prediction is stable. Low (<70%) = XGBoost and RandomForest materially disagree, treat the probability as noisier than usual.`}
                                                >
                                                    · agreement <span className={agreement < 0.7 ? 'text-warning' : 'text-ink-soft'}>{pct(agreement)}</span>
                                                </span>
                                            )}
                                        </div>
                                        <div className="text-ink font-medium">
                                            {first.home_team.name}
                                            <span className="text-ink-muted mx-2">vs</span>
                                            {first.away_team.name}
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => onSelectMatch && onSelectMatch(first)}
                                        className="mono text-[0.65rem] uppercase tracking-[0.12em] text-accent hover:text-accent-soft border-b border-accent/40 transition-colors cursor-pointer hidden sm:block"
                                    >
                                        Detail →
                                    </button>
                                </div>

                                {/* Picks for this match */}
                                <div className="divide-y divide-line">
                                    {matchPicks.map((pick, i) => {
                                        const kellyStake = (pick.kelly || 0) * FRACTIONAL_KELLY * bankroll;
                                        const edgeForGate = data.meta?.edge_ref === 'best'
                                            ? pick.edge_best ?? pick.edge
                                            : pick.edge_median ?? pick.edge;
                                        const isSuspicious = (pick.edge_best ?? pick.edge) >= SUSPICIOUS_EDGE;
                                        return (
                                            <div key={i} className="px-4 py-3 flex flex-wrap items-center gap-4">
                                                {/* Market badge + outcome */}
                                                <div className="min-w-[120px]">
                                                    <div className="eyebrow flex items-center gap-1.5">
                                                        Pick
                                                        {pick.market && MARKET_BADGE[pick.market] && (
                                                            <span className="mono text-[0.6rem] tracking-normal normal-case text-ink-muted bg-paper-tint px-1 py-0.5 border border-line">
                                                                {MARKET_BADGE[pick.market]}
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div className="text-ink font-medium mt-0.5">{pick.outcome}</div>
                                                </div>

                                                {/* Model prob */}
                                                <div className="min-w-[70px]">
                                                    <div className="eyebrow">Model</div>
                                                    <div className="mono text-sm text-ink mt-0.5">{pct(pick.prob)}</div>
                                                </div>

                                                {/* Odds: best + median */}
                                                <div className="min-w-[120px]">
                                                    <div className="eyebrow">Best · median</div>
                                                    <div className="mono text-sm text-ink mt-0.5">
                                                        {pick.odds.toFixed(2)}
                                                        {pick.odds_median != null && pick.odds_median !== pick.odds && (
                                                            <span className="text-ink-muted text-xs ml-1">
                                                                · {pick.odds_median.toFixed(2)}
                                                            </span>
                                                        )}
                                                        <div className="text-ink-muted text-[0.65rem] mt-0.5">
                                                            {pick.bookmaker}
                                                            {pick.book_count > 1 && (
                                                                <span className="ml-1">({pick.book_count} books)</span>
                                                            )}
                                                        </div>
                                                    </div>
                                                </div>

                                                {/* Edge — gate value shown prominently, best in subscript when different */}
                                                <div className="min-w-[100px]">
                                                    <div className="eyebrow">
                                                        Edge ({data.meta?.edge_ref || 'median'})
                                                    </div>
                                                    {isSuspicious ? (
                                                        <div
                                                            className="mono text-sm text-warning font-semibold mt-0.5 cursor-help border-b border-dotted border-warning/50 inline-block"
                                                            title={`Edge against best book is ${pct(pick.edge_best ?? pick.edge)} — over 20% almost never represents real value (palp error, stale line, or model overconfidence).`}
                                                        >
                                                            +{pct(edgeForGate)}
                                                        </div>
                                                    ) : (
                                                        <div className="mono text-sm text-positive font-semibold mt-0.5">
                                                            +{pct(edgeForGate)}
                                                        </div>
                                                    )}
                                                </div>

                                                {/* Kelly stake — actual NOK + % of roll */}
                                                <div className="min-w-[110px] text-right">
                                                    <div className="eyebrow">¼-Kelly stake</div>
                                                    <div className="mono text-sm text-ink mt-0.5">
                                                        {bankroll > 0 ? (
                                                            <>
                                                                {Math.round(kellyStake).toLocaleString()} <span className="text-ink-muted text-xs">NOK</span>
                                                                <div className="text-ink-muted text-[0.65rem]">
                                                                    {pct((pick.kelly || 0) * FRACTIONAL_KELLY)} of roll
                                                                </div>
                                                            </>
                                                        ) : (
                                                            <span className="text-ink-muted text-xs">Set bankroll →</span>
                                                        )}
                                                    </div>
                                                </div>

                                                {/* Log Bet — opens modal pre-filled with this pick.
                                                    Defaults stake to Kelly-suggested amount when bankroll is set. */}
                                                <div className="ml-auto">
                                                    {logStatus[`${pick.match_id}-${pick.market}-${pick.outcome_key}`] === 'logged' ? (
                                                        <span className="mono text-[0.65rem] uppercase tracking-[0.12em] text-positive border-b border-positive/40">
                                                            Logged ✓
                                                        </span>
                                                    ) : (
                                                        <button
                                                            onClick={() => setPickToLog({
                                                                pick,
                                                                defaultStake: bankroll > 0
                                                                    ? Math.round(kellyStake)
                                                                    : 100,
                                                            })}
                                                            className="mono text-[0.65rem] uppercase tracking-[0.12em] px-2 py-1 border border-line text-ink-soft hover:text-ink hover:border-ink-muted transition-colors cursor-pointer"
                                                            title="Log this pick as a paper bet. Settled automatically when the match finishes."
                                                        >
                                                            Log bet
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>

                                {/* Per-match footer: overround signal */}
                                {(matchPicks[0]?.overround_best != null) && (
                                    <div className="px-4 py-2 mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted border-t border-line bg-paper-tint/50">
                                        Book margin: {((matchPicks[0].overround_best - 1) * 100).toFixed(1)}%
                                    </div>
                                )}
                            </div>
                        );
                    })}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Log Bet modal */}
            {pickToLog && (
                <LogBetModal
                    pick={pickToLog.pick}
                    defaultStake={pickToLog.defaultStake}
                    onClose={() => setPickToLog(null)}
                    onLogged={() => {
                        const key = `${pickToLog.pick.match_id}-${pickToLog.pick.market}-${pickToLog.pick.outcome_key}`;
                        setLogStatus(prev => ({ ...prev, [key]: 'logged' }));
                        setPickToLog(null);
                    }}
                />
            )}

            {/* Footnote */}
            {picks.length > 0 && (
                <p className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mt-6 leading-relaxed">
                    Edge = model probability × decimal odds − 1. Default gate is{' '}
                    <span className="text-ink">edge vs median</span> across trusted books — more honest than gating
                    on the best book alone.{' '}¼-Kelly stake is the recommended size assuming model probabilities are
                    noisy; full Kelly only maximises growth when probabilities are exact.{' '}
                    Edges in <span className="text-warning border-b border-dotted border-warning/50">warning style</span>{' '}
                    exceed 20% against the best book and almost certainly reflect a palp error or model overconfidence,
                    not real value.
                </p>
            )}
        </div>
    );
}

// ----------------------------------------------------------------------------
// LogBetModal — confirms a picked bet before POSTing to /api/bets.
// Pre-filled from the picked outcome; user can override stake + bookmaker.
// Exported so BestOfWeek can reuse the same modal.
// ----------------------------------------------------------------------------

export function LogBetModal({ pick, defaultStake, onClose, onLogged }) {
    const [stake, setStake] = useState(defaultStake);
    const [bookmaker, setBookmaker] = useState(pick.bookmaker || '');
    const [notes, setNotes] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    const handleSubmit = async () => {
        if (!stake || stake <= 0) {
            setError('Stake must be > 0');
            return;
        }
        setSubmitting(true);
        setError(null);
        try {
            await footballAPI.createBet({
                match_id: pick.match_id,
                market: pick.market,
                outcome_key: pick.outcome_key,
                outcome_label: pick.outcome,
                odds_at_bet: pick.odds,
                stake: Number(stake),
                bookmaker: bookmaker || pick.bookmaker || null,
                model_prob_at_bet: pick.prob,
                edge_at_bet: pick.edge_best ?? pick.edge,
                notes: notes || null,
                placed_via: 'frontend',
            });
            onLogged();
        } catch (err) {
            // Log to console too so devs can see the full axios error shape
            console.error('Bet log error:', err);
            let detail = err.response?.data?.error;
            if (!detail) {
                // No response body → likely network/CORS. Tell the user what's
                // probably wrong instead of just "Network Error".
                if (err.message?.includes('Network')) {
                    detail = 'Could not reach the backend. If you are running ' +
                             "frontend locally, check that VITE_API_URL in " +
                             ".env.development points to the right URL " +
                             "(localhost:5000/api for local backend).";
                } else {
                    detail = err.message || 'Failed to log bet';
                }
            }
            setError(detail);
        } finally {
            setSubmitting(false);
        }
    };

    const potentialReturn = stake * pick.odds;
    const potentialProfit = potentialReturn - stake;

    return (
        <div
            className="fixed inset-0 z-50 bg-ink/60 flex items-center justify-center p-4"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-paper w-full max-w-md border border-line">
                <div className="flex items-start justify-between px-5 py-4 border-b border-line">
                    <div>
                        <div className="eyebrow mb-1">Log paper bet</div>
                        <div className="display text-xl text-ink">
                            {pick.outcome}<span className="text-accent">.</span>
                        </div>
                        <div className="mono text-[0.65rem] text-ink-muted mt-1">
                            {pick.home_team?.name} vs {pick.away_team?.name}
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-ink-muted hover:text-ink text-2xl leading-none cursor-pointer"
                        aria-label="Close"
                    >×</button>
                </div>

                <div className="px-5 py-5 space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                        <ReadOnlyField label="Odds" value={pick.odds.toFixed(2)} />
                        <ReadOnlyField label="Model prob" value={pct(pick.prob)} />
                        <ReadOnlyField label="Edge"
                            value={`+${pct(pick.edge_best ?? pick.edge)}`}
                            className={(pick.edge_best ?? pick.edge) >= SUSPICIOUS_EDGE ? 'text-warning' : 'text-positive'} />
                        <ReadOnlyField label="Suggested book" value={pick.bookmaker || '—'} />
                    </div>

                    <div>
                        <label className="eyebrow block mb-1">Stake (NOK)</label>
                        <input
                            type="number"
                            min="1"
                            step="10"
                            value={stake}
                            onChange={(e) => setStake(Number(e.target.value))}
                            className="w-full bg-paper border border-line px-3 py-2 mono text-sm text-ink focus:outline-none focus:border-accent transition-colors"
                            autoFocus
                        />
                    </div>

                    <div>
                        <label className="eyebrow block mb-1">Bookmaker (optional)</label>
                        <input
                            type="text"
                            value={bookmaker}
                            onChange={(e) => setBookmaker(e.target.value)}
                            placeholder={pick.bookmaker || 'Where you placed the bet'}
                            className="w-full bg-paper border border-line px-3 py-2 text-sm text-ink focus:outline-none focus:border-accent transition-colors"
                        />
                    </div>

                    <div>
                        <label className="eyebrow block mb-1">Notes (optional)</label>
                        <input
                            type="text"
                            value={notes}
                            onChange={(e) => setNotes(e.target.value)}
                            placeholder="Why this pick, what you'd compare against, etc."
                            className="w-full bg-paper border border-line px-3 py-2 text-sm text-ink focus:outline-none focus:border-accent transition-colors"
                        />
                    </div>

                    <div className="bg-paper-tint border-l-2 border-accent px-3 py-2.5">
                        <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                            Potential return
                        </div>
                        <div className="display text-lg text-ink mt-0.5">
                            {Math.round(potentialReturn).toLocaleString()} NOK
                            <span className="mono text-xs text-positive ml-2">
                                +{Math.round(potentialProfit).toLocaleString()}
                            </span>
                        </div>
                    </div>

                    {error && (
                        <div className="text-danger text-sm">{error}</div>
                    )}
                </div>

                <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-line bg-paper-tint">
                    <button
                        onClick={onClose}
                        disabled={submitting}
                        className="mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 border border-line text-ink-soft hover:text-ink hover:border-ink-muted transition-colors cursor-pointer"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={submitting}
                        className="mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 bg-ink text-paper border border-ink hover:bg-accent hover:border-accent transition-colors cursor-pointer disabled:opacity-50"
                    >
                        {submitting ? 'Logging…' : 'Log bet'}
                    </button>
                </div>
            </div>
        </div>
    );
}

function ReadOnlyField({ label, value, className = 'text-ink' }) {
    return (
        <div>
            <div className="eyebrow">{label}</div>
            <div className={'mono text-sm mt-0.5 ' + className}>{value}</div>
        </div>
    );
}

export default ValueBets;
