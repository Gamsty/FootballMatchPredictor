/*
Dashboard Page — Main Landing Page

Shows upcoming match predictions grouped by league with:
    - Category tabs: "Today" and "Upcoming" (next 7 days)
    - Filter buttons: High Confidence, Upset Pick, Banker (multi-select)
    - Match cards: compact prediction cards in a responsive grid
    - Accumulator builder: select bets across matches, calculate combined odds/returns
    - Match detail modal: click a card to see all betting markets

Data flow:
    1. Fetches predictions from /api/predictions/upcoming (7 days ahead)
    2. Client-side filters by tab (today vs upcoming) and category tags
    3. Groups matches by league, sorted by priority (PL first, then alphabetical)
    4. Auto-refreshes every 5 minutes
*/

import { useState, useEffect, useCallback } from 'react';
import { footballAPI, getBetToken, describeApiError, isCanceled } from '../services/api';
import MatchCard from '../components/MatchCard';
import FilterBar from '../components/FilterBar';
import CategoryTabs from '../components/CategoryTabs';
import MatchDetail from '../components/MatchDetail';
import ValueBets, { LogBetModal } from '../components/ValueBets';
import BestOfWeek, { LogComboModal } from '../components/BestOfWeek';
import CalibrationView from '../components/CalibrationView';
import PerformanceHub from '../components/PerformanceHub';
import RecentROI from '../components/RecentROI';
import Settings from '../components/Settings';
import MethodNote from '../components/MethodNote';
import BottomNav from '../components/BottomNav';
import {
    isToday, COMPETITION_LABELS, formatOdds,
    calculateAccumulator
} from '../utils/constants';

// "Advanced mode" gates the betting-workflow features (Value tab, Bets log,
// Log-bet button on picks) behind ?advanced=true in the URL. The main product
// is plain predictions for users who bet manually elsewhere — advanced mode is
// for the operator (me) who wants to also track CLV / paper bets.
//
// Toggle via URL: https://...?advanced=true
// Persisted to localStorage so a single ?advanced=true visit unlocks it for
// the device until the user clears it via ?advanced=false.
const ADVANCED_STORAGE_KEY = 'fmp.advanced.enabled';

function readAdvancedMode() {
    if (typeof window === 'undefined') return false;
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get('advanced');
    if (fromUrl === 'true' || fromUrl === '1') {
        localStorage.setItem(ADVANCED_STORAGE_KEY, '1');
        return true;
    }
    if (fromUrl === 'false' || fromUrl === '0') {
        localStorage.removeItem(ADVANCED_STORAGE_KEY);
        return false;
    }
    return localStorage.getItem(ADVANCED_STORAGE_KEY) === '1';
}


function Dashboard() {
    // Data state
    const [matches, setMatches] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    // Background auto-refresh state, kept apart from `loading` so a poll never
    // takes the slate off screen. `staleSince` is set when a background refresh
    // fails, so the header can say the numbers may be out of date instead of
    // silently showing old ones.
    const [refreshing, setRefreshing] = useState(false);
    const [staleSince, setStaleSince] = useState(null);

    // UI state
    const [activeTab, setActiveTab] = useState('today');
    const [selectedMatch, setSelectedMatch] = useState(null);
    const [showCalibration, setShowCalibration] = useState(false);
    // Advanced mode is sticky to localStorage. Held in state so the toggle
    // in the meta-links bar can flip it without a full page reload — the
    // old read-once-on-mount pattern made the URL the only way to toggle.
    const [advancedMode, setAdvancedMode] = useState(readAdvancedMode);
    const toggleAdvanced = () => {
        const next = !advancedMode;
        if (next) {
            localStorage.setItem(ADVANCED_STORAGE_KEY, '1');
        } else {
            localStorage.removeItem(ADVANCED_STORAGE_KEY);
        }
        setAdvancedMode(next);
    };
    const [filters, setFilters] = useState({
        categories: [],
    });

    // Accumulator state
    const [accumulator, setAccumulator] = useState([]);
    const [stake, setStake] = useState(100);
    // Holds the in-flight combo when user clicks "Log accumulator" — drives
    // the LogComboModal. null when no modal is open.
    const [comboToLog, setComboToLog] = useState(null);
    // Same for the 1-leg case — accumulator with a single selection logs
    // as a single bet via LogBetModal instead of /api/bets/combo.
    const [pickToLog, setPickToLog] = useState(null);
    const [comboLogged, setComboLogged] = useState(false);

    // Fetch all matches for the next 7 days. Tab filtering happens client-side
    // (see filteredMatches below), so the fetch itself doesn't depend on activeTab.
    //
    // `background` separates the 5-minute auto-refresh from the first load. A
    // background pass must not raise the loading flag (the whole slate is behind
    // `!loading`, so every refresh collapsed the page to a spinner and threw the
    // reader's scroll position away) and must not clear the list on failure —
    // one flaky poll should not wipe good data off the screen.
    const fetchPredictions = useCallback(async ({ background = false } = {}) => {
        if (!background) {
            setLoading(true);
            setError(null);
        } else {
            setRefreshing(true);
        }
        try {
            const params = { days: 7, sort_by: 'date' };
            const data = await footballAPI.getUpcomingPredictions(params);
            setMatches(data.matches || []);
            setError(null);
        } catch (err) {
            if (isCanceled(err)) return;
            console.error(err);
            if (background) {
                // Keep what's on screen; the next tick will try again.
                setStaleSince((prev) => prev ?? Date.now());
            } else {
                setError(describeApiError(err, 'Failed to load predictions. Make sure the backend is running.'));
            }
            return;
        } finally {
            if (background) setRefreshing(false);
            else setLoading(false);
        }
        setStaleSince(null);
    }, []);

    useEffect(() => { fetchPredictions(); }, [fetchPredictions]);

    // Auto-refresh every 5 minutes
    useEffect(() => {
        const interval = setInterval(() => fetchPredictions({ background: true }), 5 * 60 * 1000);
        return () => clearInterval(interval);
    }, [fetchPredictions]);

    // Fixture refresh is handled exclusively by the nightly Azure Container Apps Job
    // (backend/jobs/retrain.py). The /api/fixtures/refresh endpoint requires an admin
    // token so it can't be triggered from this public UI — exposing it to all visitors
    // would burn our football-data.org free-tier quota (10 req/min).

    // Client-side filtering: tabs + categories
    const activeCategories = filters.categories || [];
    let filteredMatches = [...matches];

    // Tab date filtering
    if (activeTab === 'today') {
        filteredMatches = filteredMatches.filter(m => isToday(m.date));
    } else if (activeTab === 'upcoming') {
        filteredMatches = filteredMatches.filter(m => !isToday(m.date));
    }

    // Category tag filtering
    if (activeCategories.length > 0) {
        filteredMatches = filteredMatches.filter(m =>
            activeCategories.some(cat => (m.tags || []).includes(cat))
        );
    }

    // Match counts (from all matches, not filtered — so tab badges are always accurate)
    const matchCounts = {
        today: matches.filter(m => isToday(m.date)).length,
        upcoming: matches.filter(m => !isToday(m.date)).length,
    };

    // Group by league, ordered by priority
    const LEAGUE_ORDER = [
        'Premier League',
        'Championship',
        'La Liga',
        'Bundesliga',
        'Serie A',
        'Ligue 1',
        'Eredivisie',
        'Primeira Liga',
        'UEFA Champions League',
    ];

    const groupedMatches = {};
    filteredMatches.forEach(match => {
        const league = match.competition || 'Other';
        if (!groupedMatches[league]) groupedMatches[league] = [];
        groupedMatches[league].push(match);
    });
    Object.values(groupedMatches).forEach(leagueMatches => {
        leagueMatches.sort((a, b) => new Date(a.date) - new Date(b.date));
    });

    // Sort leagues: priority order first, then alphabetical for any others
    const sortedLeagues = Object.keys(groupedMatches).sort((a, b) => {
        const idxA = LEAGUE_ORDER.indexOf(a);
        const idxB = LEAGUE_ORDER.indexOf(b);
        if (idxA !== -1 && idxB !== -1) return idxA - idxB;
        if (idxA !== -1) return -1;
        if (idxB !== -1) return 1;
        return a.localeCompare(b);
    });

    // Accumulator helpers — one bet per match, but the user picks which one
    // by clicking the + next to either Best or Safest. Clicking the currently
    // selected bet removes it; clicking a different bet swaps it.
    const accumulatorBetByMatchId = Object.fromEntries(
        accumulator.map(s => [s.matchId, s.label])
    );

    const toggleAccumulator = (match, bet) => {
        const current = accumulatorBetByMatchId[match.id];
        if (current === bet.label) {
            setAccumulator(prev => prev.filter(s => s.matchId !== match.id));
        } else {
            setAccumulator(prev => [
                ...prev.filter(s => s.matchId !== match.id),
                {
                    matchId: match.id,
                    homeTeam: match.home_team.short_name || match.home_team.name,
                    awayTeam: match.away_team.short_name || match.away_team.name,
                    label: bet.label,
                    prob: bet.prob,
                    // Backend-compatible market identifiers from collectCandidates.
                    // The accumulator-as-bet log path on /api/bets/combo needs these;
                    // older selections (before constants.js carried betMarket) won't
                    // be loggable until re-added, but that's fine — they only live
                    // in component state and die on reload.
                    market: bet.betMarket,
                    outcomeKey: bet.betOutcomeKey,
                    // Implied odds from model probability — only fair if the picker
                    // wants to track the model's own prediction. For real bets at
                    // a bookmaker you'd want NT odds, but the log modal lets you
                    // override per-leg stake (whole combo) so this is the baseline.
                    odds: bet.prob ? 1 / bet.prob : null,
                },
            ]);
        }
    };

    const accResult = calculateAccumulator(accumulator, stake);

    return (
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-10 py-6 sm:py-14 pb-24 sm:pb-14">
            {/* Hero — display tightens on phone but keeps the editorial weight.
                Lead paragraph reads as a single column on mobile vs ~60ch on
                desktop. Meta-links bar uses flex-wrap so it doesn't overflow. */}
            <section className="mb-8 sm:mb-10">
                <div className="eyebrow mb-3 sm:mb-4 flex items-center gap-3 flex-wrap">
                    <span>Today's slate</span>
                    {/* Background refresh is deliberately quiet — a subtle marker
                        rather than swapping the page for a spinner. A failed poll
                        keeps the last good slate on screen and says so. */}
                    {refreshing && (
                        <span className="mono text-[0.6rem] normal-case tracking-normal text-ink-muted">
                            refreshing…
                        </span>
                    )}
                    {staleSince && !refreshing && (
                        <span
                            className="mono text-[0.6rem] normal-case tracking-normal text-warning cursor-help border-b border-dotted border-warning/40"
                            title={`Last successful update ${new Date(staleSince).toLocaleTimeString()}. Showing the most recent data that loaded.`}
                        >
                            couldn't refresh — showing last known
                        </span>
                    )}
                </div>
                <h1 className="display text-[2rem] sm:text-5xl md:text-6xl font-light leading-[0.95] mb-4 sm:mb-5">
                    Match
                    <span className="display-italic"> predictions</span>
                    <span className="text-accent">.</span>
                </h1>
                <p className="text-ink-soft text-sm sm:text-lg max-w-2xl leading-relaxed font-light">
                    Powered by a <span className="text-ink font-medium">stacked-ensemble</span> model
                    (XGBoost + RandomForest) trained on{' '}
                    <span className="text-ink font-medium">40,000+ historical matches</span> across nine
                    leagues. Retrained nightly on Azure with AUC validation against production.
                </p>
                <div className="flex items-center flex-wrap gap-x-3 gap-y-2 mt-5 sm:mt-6 mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                    {/* The 'shown' counter is for the match-grid tabs only. Hide on Value,
                        Best Picks, Performance, Settings, and Method — those have their own toolbars. */}
                    {activeTab !== 'value' && activeTab !== 'best' && activeTab !== 'performance' && activeTab !== 'settings' && activeTab !== 'method' && (
                        <>
                            <span>{filteredMatches.length} shown</span>
                            <span className="w-1 h-1 rounded-full bg-ink-muted/40" />
                        </>
                    )}
                    <button
                        onClick={() => setActiveTab('method')}
                        className="text-accent hover:text-accent-soft transition-colors inline-flex items-center gap-1.5 cursor-pointer"
                    >
                        <span className="border-b border-accent/40 hover:border-accent-soft">How it works</span>
                        <span aria-hidden="true">→</span>
                    </button>
                    <span className="w-1 h-1 rounded-full bg-ink-muted/40" />
                    <button
                        onClick={() => setShowCalibration(true)}
                        className="text-ink-soft hover:text-ink transition-colors inline-flex items-center gap-1.5 cursor-pointer"
                        title="Empirical check on whether model probabilities match actual win rates."
                    >
                        <span className="border-b border-ink-muted/40 hover:border-ink">Calibration</span>
                        <span aria-hidden="true">→</span>
                    </button>
                    <span className="w-1 h-1 rounded-full bg-ink-muted/40" />
                    <button
                        onClick={() => setActiveTab('performance')}
                        className="text-ink-soft hover:text-ink transition-colors inline-flex items-center gap-1.5 cursor-pointer"
                        title="Bet log + ROI / CLV tracking. Read-only for visitors; advanced mode enables logging & deletion."
                    >
                        <span className="border-b border-ink-muted/40 hover:border-ink">Performance</span>
                        <span aria-hidden="true">→</span>
                    </button>
                    <span className="w-1 h-1 rounded-full bg-ink-muted/40" />
                    <button
                        onClick={toggleAdvanced}
                        title={advancedMode
                            ? 'Advanced mode is ON — logging, Value tab, and Performance delete enabled. Click to turn off.'
                            : 'Advanced mode is OFF — logging and bet management hidden. Click to turn on.'}
                        className={
                            'inline-flex items-center gap-1.5 px-2 py-0.5 border transition-colors cursor-pointer ' +
                            (advancedMode
                                ? 'bg-ink text-paper border-ink hover:bg-accent hover:border-accent'
                                : 'border-line text-ink-muted hover:text-ink hover:border-ink-muted')
                        }
                    >
                        <span>ADV</span>
                        <span aria-hidden="true">{advancedMode ? '●' : '○'}</span>
                    </button>
                    {/* Auth status — only relevant when advanced mode is on
                        since unauthenticated visitors can't log anything. */}
                    {advancedMode && (
                        <button
                            onClick={() => setActiveTab('settings')}
                            title={getBetToken()
                                ? 'Bet-write token is set. Logging will succeed.'
                                : 'No bet-write token — POST /api/bets will return 401. Click to open Settings.'}
                            className={
                                'inline-flex items-center gap-1.5 px-2 py-0.5 border transition-colors cursor-pointer ' +
                                (getBetToken()
                                    ? 'border-positive text-positive hover:bg-positive hover:text-paper'
                                    : 'border-danger text-danger hover:bg-danger hover:text-paper')
                            }
                        >
                            <span>AUTH</span>
                            <span aria-hidden="true">{getBetToken() ? '●' : '○'}</span>
                        </button>
                    )}
                </div>
            </section>

            {/* Recent-ROI strip — public proof of +EV. Renders nothing until at
                least 5 settled bets are on record (avoids early-sample noise). */}
            <RecentROI />

            {/* Tabs — Value tab is hidden unless advancedMode is enabled */}
            <CategoryTabs
                activeTab={activeTab}
                onTabChange={setActiveTab}
                matchCounts={matchCounts}
                showValueTab={advancedMode}
            />

            {/* Best Picks tab — top ranked picks across all leagues + combo builder */}
            {activeTab === 'best' && (
                <BestOfWeek onSelectMatch={setSelectedMatch} canLog={advancedMode} />
            )}

            {/* Value tab — completely separate render path (odds-driven, not match-grid) */}
            {advancedMode && activeTab === 'value' && (
                <ValueBets onSelectMatch={setSelectedMatch} />
            )}

            {/* Performance tab — bet tracking + ROI / CLV / segment breakdowns */}
            {activeTab === 'performance' && (
                <PerformanceHub canEdit={advancedMode} />
            )}

            {/* Method tab — public deep-dive. Replaces the About modal. */}
            {activeTab === 'method' && <MethodNote />}

            {/* Settings tab — advanced-mode only, but the toggle itself lives
                here so users can flip OFF advanced from inside Settings */}
            {advancedMode && activeTab === 'settings' && (
                <Settings
                    advancedMode={advancedMode}
                    onAdvancedChange={(next) => {
                        if (next) {
                            localStorage.setItem(ADVANCED_STORAGE_KEY, '1');
                        } else {
                            localStorage.removeItem(ADVANCED_STORAGE_KEY);
                        }
                        setAdvancedMode(next);
                        // When turning off, bounce back to Today so the user
                        // doesn't see an empty Settings tab they no longer
                        // have access to (the tab disappears from nav).
                        if (!next) setActiveTab('today');
                    }}
                />
            )}

            {/* Match-grid view (today + upcoming tabs) */}
            {activeTab !== 'value' && activeTab !== 'best' && activeTab !== 'performance' && activeTab !== 'settings' && activeTab !== 'method' && (
            <>
            {/* Filters */}
            <FilterBar filters={filters} onFilterChange={setFilters} />

            {/* Accumulator Bar — collapses tighter on mobile so it doesn't
                eat the entire viewport while the user is still picking. */}
            {accumulator.length > 0 && (
                <div className="bg-paper-tint border-l-2 border-accent p-3 sm:p-5 mb-6 sm:mb-8">
                    <div className="flex items-center justify-between mb-3 sm:mb-4">
                        <div>
                            <div className="eyebrow">Accumulator</div>
                            <div className="display text-sm sm:text-base text-ink mt-1">
                                {accumulator.length} {accumulator.length === 1 ? 'selection' : 'selections'} stacked.
                            </div>
                        </div>
                        <button
                            onClick={() => setAccumulator([])}
                            className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted hover:text-accent transition-colors cursor-pointer py-1"
                        >
                            Clear all
                        </button>
                    </div>

                    {/* Selections */}
                    <div className="space-y-1.5 mb-3 sm:mb-4">
                        {accumulator.map(sel => (
                            <div key={sel.matchId} className="flex items-center justify-between py-2 px-3 bg-paper border border-line gap-2">
                                <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
                                    <button
                                        onClick={() => setAccumulator(prev => prev.filter(s => s.matchId !== sel.matchId))}
                                        className="text-ink-muted hover:text-accent text-lg leading-none transition-colors cursor-pointer flex-shrink-0"
                                        aria-label="Remove selection"
                                    >×</button>
                                    <span className="text-xs sm:text-sm text-ink-soft truncate">
                                        {sel.homeTeam} <span className="text-ink-muted">vs</span> {sel.awayTeam}
                                    </span>
                                </div>
                                <div className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
                                    <span className="text-xs sm:text-sm text-ink font-medium">{sel.label}</span>
                                    <span
                                        className="mono text-[0.65rem] sm:text-xs text-ink-muted cursor-help border-b border-dotted border-ink-muted/30"
                                        title="Model-implied odds (1/probability). The actual NT price is usually lower — log the bet to enter your real NT odds."
                                    >
                                        @ {formatOdds(sel.prob)}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Stake + Returns. Single-selection drops the "combined"
                        framing — it's just the leg odds — and labels the price
                        as 'model' so the operator knows it's implied-from-prob,
                        not their NT odds. */}
                    <div className="flex items-center gap-3 pt-3 sm:pt-4 border-t border-line flex-wrap">
                        <div className="flex items-center gap-2">
                            <span className="eyebrow">Stake</span>
                            <input
                                type="number"
                                inputMode="numeric"
                                value={stake}
                                onChange={(e) => setStake(Math.max(0, Number(e.target.value)))}
                                className="w-20 sm:w-24 bg-paper border border-line px-2 py-1 mono text-sm text-ink focus:outline-none focus:border-accent transition-colors"
                            />
                            <span className="mono text-xs text-ink-muted">NOK</span>
                        </div>
                        <div className="flex-1" />
                        <div className="text-right">
                            {/* Every leg is priced at 1/probability, so the combined
                                figure is the model's own fair price with no margin in
                                it. A bookmaker takes its cut on EVERY leg, so the gap
                                compounds: at NT's typical 8-12% per leg a four-fold
                                pays roughly 28-40% less than the number below. The
                                single-leg case already said "model odds"; the multi-leg
                                case dropped that framing exactly where it matters most. */}
                            <div className="mono text-[0.6rem] sm:text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                                Model odds: <span className="text-ink">{accResult.totalOdds}</span>
                            </div>
                            <div className="display text-base sm:text-lg text-accent mt-0.5">
                                {accResult.potentialReturn.toLocaleString()} NOK
                                <span className="mono text-[0.65rem] sm:text-xs text-accent-soft ml-2">+{accResult.profit.toLocaleString()}</span>
                            </div>
                            <div
                                className="mono text-[0.55rem] sm:text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted mt-1 cursor-help border-b border-dotted border-ink-muted/30 inline-block"
                                title="Legs are priced at 1/probability — the model's fair odds, with no bookmaker margin. Norsk Tipping takes 8-12% per leg, which compounds across a combo, so the real payout is materially lower. Log the bet to enter the price you actually got."
                            >
                                at fair odds · NT pays less
                            </div>
                        </div>
                        {/* Log button — advanced-mode only. Single leg routes to
                            POST /api/bets via LogBetModal, 2+ legs routes to
                            POST /api/bets/combo via LogComboModal. Every selection
                            in `accumulator` carries the backend-compatible
                            market/outcomeKey (see toggleAccumulator). */}
                        {advancedMode && accumulator.length >= 1 && (
                            <button
                                onClick={() => {
                                    const loggable = accumulator.filter(s => s.market && s.outcomeKey);
                                    if (loggable.length === 0) {
                                        alert('No loggable picks — re-add via match cards.');
                                        return;
                                    }
                                    if (loggable.length === 1) {
                                        const sel = loggable[0];
                                        setPickToLog({
                                            pick: {
                                                match_id: sel.matchId,
                                                market: sel.market,
                                                outcome_key: sel.outcomeKey,
                                                outcome: sel.label,
                                                outcome_label: sel.label,
                                                odds: sel.odds || (1 / sel.prob),
                                                prob: sel.prob,
                                                home_team: { name: sel.homeTeam },
                                                away_team: { name: sel.awayTeam },
                                            },
                                            defaultStake: stake || 100,
                                            defaultOdds: sel.odds || (1 / sel.prob),
                                        });
                                        return;
                                    }
                                    const combinedProb = loggable.reduce((acc, s) => acc * s.prob, 1);
                                    const combinedOdds = loggable.reduce((acc, s) => acc * (s.odds || (1 / s.prob)), 1);
                                    setComboToLog({
                                        legs: loggable.map(sel => ({
                                            match_id: sel.matchId,
                                            market: sel.market,
                                            outcome_key: sel.outcomeKey,
                                            outcome: sel.label,
                                            odds: sel.odds || (1 / sel.prob),
                                            prob: sel.prob,
                                            // LogComboModal preview reads .name / .short_name,
                                            // wrap the plain strings the accumulator stores.
                                            home_team: { name: sel.homeTeam },
                                            away_team: { name: sel.awayTeam },
                                        })),
                                        combinedOdds,
                                        combinedProb,
                                        // Legs are priced at 1/prob, so prob x odds - 1
                                        // is exactly 0 — an identity, not a measurement.
                                        // null tells LogComboModal to omit the field.
                                        combinedEdge: null,
                                        defaultStake: stake || 100,
                                    });
                                }}
                                className="mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 bg-ink text-paper border border-ink hover:bg-accent hover:border-accent transition-colors cursor-pointer"
                                title="Log this selection as a tracked bet"
                            >
                                {accumulator.length === 1 ? 'Log bet' : 'Log accumulator'}
                            </button>
                        )}
                    </div>
                </div>
            )}

            {/* Content */}
            {loading && (
                <div className="text-center py-20">
                    <div className="inline-block w-3 h-3 bg-accent animate-pulse-soft rounded-full mb-4" />
                    <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">Fetching matches</p>
                </div>
            )}

            {error && (
                <div className="bg-paper-tint border-l-2 border-danger p-6">
                    <div className="eyebrow text-danger mb-1">Connection error</div>
                    <p className="text-ink-soft">{error}</p>
                    <button
                        onClick={fetchPredictions}
                        className="mt-3 mono text-[0.7rem] uppercase tracking-[0.12em] text-accent hover:text-accent-soft border-b border-accent/40 transition-colors cursor-pointer"
                    >
                        Try again →
                    </button>
                </div>
            )}

            {!loading && !error && filteredMatches.length === 0 && (
                <div className="text-center py-20 max-w-md mx-auto">
                    {activeCategories.length > 0 ? (
                        <>
                            <div className="eyebrow mb-3">Empty</div>
                            <h3 className="display text-2xl text-ink mb-3">No matches match your filters.</h3>
                            <p className="text-ink-soft text-sm">
                                Try removing a category filter, or switch tabs to see more matches.
                            </p>
                        </>
                    ) : activeTab === 'today' && matchCounts.upcoming > 0 ? (
                        <>
                            <div className="eyebrow mb-3">Quiet day</div>
                            <h3 className="display text-2xl text-ink mb-4">
                                No matches scheduled for today<span className="text-accent">.</span>
                            </h3>
                            <button
                                onClick={() => setActiveTab('upcoming')}
                                className="mono text-[0.7rem] uppercase tracking-[0.12em] text-accent hover:text-accent-soft border-b border-accent/40 transition-colors cursor-pointer"
                            >
                                See {matchCounts.upcoming} upcoming match{matchCounts.upcoming === 1 ? '' : 'es'} →
                            </button>
                        </>
                    ) : activeTab === 'today' ? (
                        <>
                            <div className="eyebrow mb-3">Quiet day</div>
                            <h3 className="display text-2xl text-ink mb-3">
                                No matches scheduled for today<span className="text-accent">.</span>
                            </h3>
                            <p className="text-ink-soft text-sm">New fixtures sync automatically every night.</p>
                        </>
                    ) : (
                        <>
                            <div className="eyebrow mb-3">Empty</div>
                            <h3 className="display text-2xl text-ink mb-3">
                                No upcoming matches<span className="text-accent">.</span>
                            </h3>
                            <p className="text-ink-soft text-sm">New fixtures sync automatically every night.</p>
                        </>
                    )}
                </div>
            )}

            {!loading && !error && filteredMatches.length > 0 && (
                <div className="space-y-10">
                    {sortedLeagues.map(league => ({ league, leagueMatches: groupedMatches[league] })).map(({ league, leagueMatches }) => (
                        <div key={league}>
                            <div className="flex items-baseline gap-3 mb-4 pb-2 border-b border-line">
                                <h2 className="display text-xl text-ink">
                                    {COMPETITION_LABELS[league] || league}
                                </h2>
                                <span className="mono text-xs text-ink-muted">{leagueMatches.length}</span>
                            </div>

                            <div
                                className="grid gap-4"
                                style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 360px))' }}
                            >
                                {leagueMatches.map(match => (
                                    <MatchCard
                                        key={match.id}
                                        match={match}
                                        onClick={setSelectedMatch}
                                        selectedBet={accumulatorBetByMatchId[match.id]}
                                        onToggleAccumulator={toggleAccumulator}
                                    />
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            )}
            </>
            )}

            {/* Match Detail Modal — passes accumulator state so MatchDetail's
                "+ to slip" buttons feed the same shared slip Dashboard renders
                as the accumulator bar. User stacks across multiple matches /
                markets, then closes the modal to log from the slip. */}
            {selectedMatch && (
                <MatchDetail
                    match={selectedMatch}
                    onClose={() => setSelectedMatch(null)}
                    canLog={advancedMode}
                    accumulatorBetByMatchId={accumulatorBetByMatchId}
                    accumulatorCount={accumulator.length}
                    onAddToSlip={toggleAccumulator}
                />
            )}

            {/* Calibration Modal */}
            {showCalibration && <CalibrationView onClose={() => setShowCalibration(false)} />}

            {/* Single-leg log path — reused from ValueBets. */}
            {pickToLog && (
                <LogBetModal
                    pick={pickToLog.pick}
                    defaultStake={pickToLog.defaultStake}
                    defaultOdds={pickToLog.defaultOdds}
                    onClose={() => setPickToLog(null)}
                    onLogged={() => {
                        setComboLogged(true);  // reuse the toast — same UX
                        setPickToLog(null);
                        setAccumulator([]);
                        setTimeout(() => setComboLogged(false), 3000);
                    }}
                />
            )}

            {/* Reused from Best Picks — same modal, same POST endpoint. */}
            {comboToLog && (
                <LogComboModal
                    summary={comboToLog}
                    onClose={() => setComboToLog(null)}
                    onLogged={() => {
                        setComboLogged(true);
                        setComboToLog(null);
                        setAccumulator([]);   // empty the slip after successful log
                        setTimeout(() => setComboLogged(false), 3000);
                    }}
                />
            )}

            {comboLogged && (
                <div
                    className="fixed right-6 z-50 bg-paper border border-positive px-4 py-2 mono text-[0.7rem] uppercase tracking-[0.12em] text-positive"
                    style={{ bottom: 'calc(env(safe-area-inset-bottom) + 80px)' }}
                >
                    Bet logged ✓
                </div>
            )}

            {/* Mobile bottom nav — hidden on sm+; CategoryTabs handles desktop. */}
            <BottomNav
                activeTab={activeTab}
                onTabChange={setActiveTab}
                matchCounts={matchCounts}
            />
        </div>
    );
}

export default Dashboard;
