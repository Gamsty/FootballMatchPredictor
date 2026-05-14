/*
BestOfWeek — cross-league ranked picks for the week + combo builder.

Difference vs ValueBets:
  - ValueBets shows ALL picks above an edge threshold, grouped by league.
    Designed for "what's available, let me browse".
  - BestOfWeek shows the top N picks across all leagues as a single ranked list.
    Designed for "what should I actually bet on this week".

Ranking score:
    score = max(¼-Kelly, 0) × ensemble_agreement

Kelly already balances edge vs probability (a 3% edge on a 1.50 favorite is a
much better stake than a 10% edge on a 4.50 longshot). Multiplying by ensemble
agreement penalises picks where the base models materially disagree — those are
where our probability estimate is noisiest. Picks with no agreement signal
(legacy data) get treated as agreement=1.

Combo builder:
  Multiplies prob and odds across selected legs. Combined edge can stay positive
  for a couple of legs but margin compounds fast — the UI surfaces the margin
  drag explicitly, and warns when adding a 4th+ leg.

Reuses LogBetModal from ValueBets to avoid duplication.
*/

import { useState, useEffect, useRef } from 'react';
import { footballAPI } from '../services/api';
import { formatTime, formatMatchDate, COMPETITION_LABELS } from '../utils/constants';
import { LogBetModal } from './ValueBets';
import ComboPresets from './ComboPresets';
import CompoundMarkets from './CompoundMarkets';

const pct = (v) => `${(v * 100).toFixed(1)}%`;
const FRACTIONAL_KELLY = 0.25;
const SUSPICIOUS_EDGE = 0.20;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const BANKROLL_STORAGE_KEY = 'fmp.bankroll.nok';
const NT_ODDS_STORAGE_KEY = 'fmp.nt_odds.v1';
const TOP_N = 20;
const COMBO_WARN_LEGS = 4;

// Norsk Tipping odds are not on any public API. Users enter them manually inline
// (cached to localStorage per pick key). The actual betting target is NT, so
// edge_vs_NT is the only number that decides whether to place. We still show
// the Pinnacle edge — divergence between the two is a sharp signal.
function loadNTOdds() {
    try {
        const raw = localStorage.getItem(NT_ODDS_STORAGE_KEY);
        return raw ? JSON.parse(raw) : {};
    } catch {
        return {};
    }
}
function saveNTOdds(map) {
    try {
        localStorage.setItem(NT_ODDS_STORAGE_KEY, JSON.stringify(map));
    } catch {
        // Quota exceeded or storage disabled — silent fail; ephemeral state ok.
    }
}

const MARKET_BADGE = {
    h2h:         '1X2',
    totals_2_5:  'O/U 2.5',
    btts:        'BTTS',
};

const pickKey = (p) => `${p.match_id}-${p.market}-${p.outcome_key}`;

function BestOfWeek({ onSelectMatch, canLog = false }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [combo, setCombo] = useState([]);  // array of pick keys
    const [pickToLog, setPickToLog] = useState(null);
    const [logStatus, setLogStatus] = useState({});
    const [bankroll, setBankroll] = useState(() => {
        const stored = Number(localStorage.getItem(BANKROLL_STORAGE_KEY));
        return Number.isFinite(stored) && stored > 0 ? stored : 1000;
    });
    const [ntOdds, setNtOdds] = useState(loadNTOdds);
    const lastFetched = useRef(0);

    useEffect(() => {
        localStorage.setItem(BANKROLL_STORAGE_KEY, String(bankroll));
    }, [bankroll]);

    // Persist NT-odds map whenever it changes — this is the only state on the
    // page that is meaningful across reloads (per-device, no backend sync).
    useEffect(() => { saveNTOdds(ntOdds); }, [ntOdds]);

    const setNtOddsFor = (key, raw) => {
        const v = Number(raw);
        setNtOdds(prev => {
            if (!raw || !Number.isFinite(v) || v <= 1.0) {
                const rest = { ...prev };
                delete rest[key];
                return rest;
            }
            return { ...prev, [key]: Math.round(v * 100) / 100 };
        });
    };

    useEffect(() => {
        let controller = new AbortController();
        let retryTimer = null;

        const fetchOnce = async (attempt = 0) => {
            controller.abort();
            controller = new AbortController();
            setLoading(true);
            setError(null);
            try {
                // Slightly looser threshold (2%) than Value tab default (3%) so
                // we have a healthy pool to re-rank by Kelly. Top-N filter
                // tightens that back down on the client.
                //
                // Multi-market: h2h + totals 2.5. Costs 2x quota per league fetch
                // vs h2h-only, but doubles the universe of potential picks. BTTS
                // is only on The Odds API's per-event endpoint which we don't
                // wire up — surfacing BTTS would mean 73x quota cost per scan.
                const res = await footballAPI.getValueBets(
                    { days: 7, min_edge: 0.02, books: 'sharp', markets: 'h2h,totals' },
                    { signal: controller.signal },
                );
                setData(res);
                lastFetched.current = Date.now();
            } catch (err) {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                if (attempt === 0) {
                    retryTimer = setTimeout(() => fetchOnce(1), 3000);
                    return;
                }
                setError(err.message || 'Failed to load picks');
            } finally {
                setLoading(false);
            }
        };

        fetchOnce();

        const interval = setInterval(() => {
            if (document.visibilityState === 'visible') fetchOnce();
        }, REFRESH_INTERVAL_MS);

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
    }, []);

    if (loading && !data) {
        return (
            <div className="text-center py-20">
                <div className="inline-block w-3 h-3 bg-accent animate-pulse-soft rounded-full mb-4" />
                <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                    Ranking the week
                </p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-paper-tint border-l-2 border-danger p-6">
                <div className="eyebrow text-danger mb-1">Best of the week unavailable</div>
                <p className="text-ink-soft">{error}</p>
            </div>
        );
    }

    if (!data?.enabled) {
        return (
            <div className="bg-paper-tint border-l-2 border-accent p-6 max-w-2xl">
                <div className="eyebrow mb-2">Odds integration</div>
                <h3 className="display text-2xl text-ink mb-3">Not configured yet<span className="text-accent">.</span></h3>
                <p className="text-ink-soft text-sm leading-relaxed">
                    This view requires live bookmaker odds. Set the{' '}
                    <span className="mono text-xs bg-paper px-1.5 py-0.5 border border-line">ODDS_API_KEY</span>{' '}
                    environment variable on the backend.
                </p>
            </div>
        );
    }

    // Score and rank
    const scored = (data.value_bets || []).map(p => {
        const odds = p.odds_median ?? p.odds ?? 0;
        const prob = p.prob ?? 0;
        // Recompute Kelly against median odds for stability — backend stores
        // Kelly against best price which can be noisy when one book outlies.
        const b = odds - 1;
        const kelly = (b > 0 && prob > 0) ? Math.max(0, (prob * b - (1 - prob)) / b) : 0;
        const agreement = p.ensemble_agreement ?? 1;
        const score = kelly * agreement;
        return { ...p, _score: score, _kelly: kelly };
    });
    scored.sort((a, b) => b._score - a._score);
    const top = scored.slice(0, TOP_N);

    // Combo math — multiplicative assuming independence (sometimes wrong, e.g.
    // two picks in the same match are NOT independent — but our UI prevents that
    // by deduping on match_id below).
    const comboKeys = new Set(combo);
    const selectedPicks = top.filter(p => comboKeys.has(pickKey(p)));
    const comboProb = selectedPicks.reduce((acc, p) => acc * (p.prob ?? 0), 1);
    const comboOdds = selectedPicks.reduce(
        (acc, p) => acc * (p.odds_median ?? p.odds ?? 1), 1
    );
    const comboEV = selectedPicks.length > 0 ? (comboProb * comboOdds - 1) : 0;
    // Approximate margin drag: each leg's overround compounds.
    const comboMarginFactor = selectedPicks.reduce(
        (acc, p) => acc * (p.overround_median ?? p.overround_best ?? 1), 1
    );
    const comboMargin = selectedPicks.length > 0 ? (comboMarginFactor - 1) : 0;
    // Combined Kelly (¼) for stake recommendation
    const comboB = comboOdds - 1;
    const comboKellyFull = (comboB > 0 && comboProb > 0)
        ? Math.max(0, (comboProb * comboB - (1 - comboProb)) / comboB)
        : 0;
    const comboStake = bankroll > 0 && selectedPicks.length >= 2 && comboKellyFull > 0
        ? Math.max(1, Math.floor(bankroll * comboKellyFull * FRACTIONAL_KELLY))
        : 0;
    const comboReturn = comboStake * comboOdds;

    // Load a preset combo (from ComboPresets) into the active combo selection.
    // Replaces any prior selection — presets are presented as drop-in
    // recommendations, not additions.
    const loadComboFromPreset = (legs) => {
        setCombo(legs.map(pickKey));
    };

    const toggleCombo = (p) => {
        const key = pickKey(p);
        setCombo(prev => {
            // Same match? swap (only one leg per match — different selections
            // in the same fixture are not independent so the multiplicative
            // combo math would be flat-out wrong).
            const sameMatch = prev.find(k => {
                const sel = top.find(t => pickKey(t) === k);
                return sel && sel.match_id === p.match_id;
            });
            if (sameMatch === key) return prev.filter(k => k !== key);
            if (sameMatch) return [...prev.filter(k => k !== sameMatch), key];
            return [...prev, key];
        });
    };

    const quota = data.meta?.quota;
    const quotaLow = quota?.low;

    return (
        <div>
            {/* Controls strip */}
            <div className="flex flex-wrap items-center gap-3 mb-6 pb-4 border-b border-line">
                <div className="eyebrow">Bankroll</div>
                <div className="flex items-center gap-1">
                    <input
                        type="number"
                        min="0"
                        step="100"
                        value={bankroll || ''}
                        onChange={(e) => {
                            const v = Number(e.target.value);
                            setBankroll(Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);
                        }}
                        className="w-24 bg-paper border border-line px-2 py-1 mono text-xs text-ink focus:outline-none focus:border-accent transition-colors"
                        title="Your total bankroll in NOK. ¼-Kelly stakes are computed against this number."
                    />
                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted">NOK</span>
                </div>

                <div className="flex-1" />
                <div className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted text-right">
                    <div>Top {Math.min(TOP_N, top.length)} of {data.value_bets?.length || 0}</div>
                    {quota?.remaining != null && (
                        <div className={quotaLow ? 'text-warning' : 'text-ink-muted'}>
                            Quota: {quota.remaining} req remaining
                        </div>
                    )}
                </div>
            </div>

            {/* Auto-generated combo presets (Safest / Best edge / Treble) */}
            <ComboPresets
                scoredPicks={scored}
                bankroll={bankroll}
                onUseCombo={loadComboFromPreset}
            />

            {/* Combo builder — sticky at top when active */}
            {selectedPicks.length > 0 && (
                <div className="bg-paper-tint border-l-2 border-accent p-5 mb-8 sticky top-2 z-10">
                    <div className="flex items-center justify-between mb-3">
                        <div>
                            <div className="eyebrow">Combo</div>
                            <div className="display text-base text-ink mt-1">
                                {selectedPicks.length} {selectedPicks.length === 1 ? 'leg' : 'legs'} stacked
                                {selectedPicks.length === 1 && (
                                    <span className="text-ink-muted text-sm font-light"> · add another to build a combo</span>
                                )}
                            </div>
                        </div>
                        <button
                            onClick={() => setCombo([])}
                            className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted hover:text-accent transition-colors cursor-pointer"
                        >
                            Clear
                        </button>
                    </div>

                    {/* Legs */}
                    <div className="space-y-1.5 mb-4">
                        {selectedPicks.map(p => (
                            <div key={pickKey(p)} className="flex items-center justify-between py-1.5 px-3 bg-paper border border-line">
                                <div className="flex items-center gap-3 min-w-0">
                                    <button
                                        onClick={() => toggleCombo(p)}
                                        className="text-ink-muted hover:text-accent text-base leading-none transition-colors cursor-pointer"
                                        aria-label="Remove leg"
                                    >×</button>
                                    <span className="text-sm text-ink-soft truncate">
                                        {p.home_team?.short_name || p.home_team?.name}{' '}
                                        <span className="text-ink-muted">vs</span>{' '}
                                        {p.away_team?.short_name || p.away_team?.name}
                                    </span>
                                </div>
                                <div className="flex items-center gap-3">
                                    <span className="text-sm text-ink font-medium">{p.outcome}</span>
                                    <span className="mono text-xs text-ink-muted">
                                        @ {(p.odds_median ?? p.odds).toFixed(2)}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Combo summary */}
                    {selectedPicks.length >= 2 && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-4 border-t border-line">
                            <div>
                                <div className="eyebrow">Combined odds</div>
                                <div className="mono text-lg text-ink mt-0.5">{comboOdds.toFixed(2)}</div>
                            </div>
                            <div>
                                <div className="eyebrow">Hit probability</div>
                                <div className="mono text-lg text-ink mt-0.5">{pct(comboProb)}</div>
                            </div>
                            <div>
                                <div className="eyebrow">Combined edge</div>
                                <div className={'mono text-lg mt-0.5 ' + (comboEV > 0 ? 'text-positive' : 'text-warning')}>
                                    {comboEV >= 0 ? '+' : ''}{pct(comboEV)}
                                </div>
                            </div>
                            <div>
                                <div className="eyebrow">Margin drag</div>
                                <div className="mono text-lg text-ink-soft mt-0.5">−{pct(comboMargin)}</div>
                            </div>
                        </div>
                    )}

                    {selectedPicks.length >= 2 && bankroll > 0 && comboStake > 0 && (
                        <div className="mt-4 pt-4 border-t border-line flex items-center justify-between">
                            <div>
                                <div className="eyebrow">¼-Kelly stake</div>
                                <div className="display text-lg text-ink mt-0.5">
                                    {comboStake.toLocaleString()} <span className="mono text-xs text-ink-muted">NOK</span>
                                </div>
                            </div>
                            <div className="text-right">
                                <div className="eyebrow">Potential return</div>
                                <div className="display text-lg text-accent mt-0.5">
                                    {Math.round(comboReturn).toLocaleString()} NOK
                                    <span className="mono text-xs text-positive ml-2">
                                        +{Math.round(comboReturn - comboStake).toLocaleString()}
                                    </span>
                                </div>
                            </div>
                        </div>
                    )}

                    {selectedPicks.length >= COMBO_WARN_LEGS && (
                        <div className="mt-4 pt-4 border-t border-warning/40 bg-warning/5 -mx-5 -mb-5 px-5 py-3">
                            <p className="text-xs text-warning leading-relaxed">
                                <span className="font-semibold">{selectedPicks.length}-leg combo:</span>{' '}
                                hit probability drops fast with each added leg, and bookmaker margin compounds.
                                Even a positive combined edge here is high-variance — single bets generally maximise growth.
                            </p>
                        </div>
                    )}
                </div>
            )}

            {/* Empty state */}
            {top.length === 0 && (
                <div className="text-center py-16 max-w-md mx-auto">
                    <div className="eyebrow mb-3">No picks</div>
                    <h3 className="display text-2xl text-ink mb-3">
                        Quiet week<span className="text-accent">.</span>
                    </h3>
                    <p className="text-ink-soft text-sm">
                        No bets clear the 2% edge threshold across the next 7 days.
                        Check back after the next odds refresh.
                    </p>
                </div>
            )}

            {/* Ranked picks list */}
            {top.length > 0 && (
                <ol className="space-y-2">
                    {top.map((p, i) => {
                        const isSelected = comboKeys.has(pickKey(p));
                        const kellyStake = p._kelly * FRACTIONAL_KELLY * bankroll;
                        const edge = p.edge_median ?? p.edge ?? 0;
                        const isSuspicious = (p.edge_best ?? p.edge ?? 0) >= SUSPICIOUS_EDGE;
                        const logKey = pickKey(p);
                        const odds = p.odds_median ?? p.odds;
                        return (
                            <li
                                key={logKey}
                                className={
                                    'bg-paper border transition-colors ' +
                                    (isSelected ? 'border-accent' : 'border-line hover:border-ink-muted')
                                }
                            >
                                <div className="flex flex-wrap items-center gap-4 px-4 py-3">
                                    {/* Rank */}
                                    <div className="display text-2xl text-ink-muted w-8 text-center">
                                        {i + 1}
                                    </div>

                                    {/* Match + market */}
                                    <div className="min-w-[200px] flex-1">
                                        <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                                            {COMPETITION_LABELS[p.competition] || p.competition}
                                            <span className="mx-1.5">·</span>
                                            {formatMatchDate(p.date)} {formatTime(p.date)}
                                        </div>
                                        <button
                                            onClick={() => onSelectMatch && onSelectMatch(p)}
                                            className="text-ink font-medium mt-0.5 text-left hover:text-accent transition-colors cursor-pointer"
                                            title="View match detail"
                                        >
                                            {p.home_team?.name}
                                            <span className="text-ink-muted mx-2">vs</span>
                                            {p.away_team?.name}
                                        </button>
                                    </div>

                                    {/* Pick + market badge */}
                                    <div className="min-w-[130px]">
                                        <div className="eyebrow flex items-center gap-1.5">
                                            Pick
                                            {MARKET_BADGE[p.market] && (
                                                <span className="mono text-[0.6rem] tracking-normal normal-case text-ink-muted bg-paper-tint px-1 py-0.5 border border-line">
                                                    {MARKET_BADGE[p.market]}
                                                </span>
                                            )}
                                        </div>
                                        <div className="text-ink font-medium mt-0.5">{p.outcome}</div>
                                    </div>

                                    {/* Prob / Odds */}
                                    <div className="min-w-[80px]">
                                        <div className="eyebrow">Model · odds</div>
                                        <div className="mono text-sm text-ink mt-0.5">
                                            {pct(p.prob)} · {odds.toFixed(2)}
                                        </div>
                                    </div>

                                    {/* Edge vs sharp median */}
                                    <div className="min-w-[70px]">
                                        <div className="eyebrow">Edge</div>
                                        <div className={
                                            'mono text-sm font-semibold mt-0.5 ' +
                                            (isSuspicious ? 'text-warning' : 'text-positive')
                                        }>
                                            +{pct(edge)}
                                        </div>
                                    </div>

                                    {/* NT odds + edge vs NT — manual entry, persisted locally.
                                        This is the number that actually matters when betting
                                        against Norsk Tipping: their margins are 8-12% vs
                                        Pinnacle's 2-3%, so edge vs sharp markets routinely
                                        evaporates when measured against the book you'll
                                        actually place on. */}
                                    <div className="min-w-[110px]">
                                        <div className="eyebrow">vs NT</div>
                                        <div className="mt-0.5 flex items-center gap-1">
                                            <input
                                                type="number"
                                                min="1.01"
                                                step="0.05"
                                                value={ntOdds[logKey] ?? ''}
                                                onChange={(e) => setNtOddsFor(logKey, e.target.value)}
                                                placeholder="—"
                                                className="w-14 bg-paper border border-line px-1.5 py-0.5 mono text-xs text-ink focus:outline-none focus:border-accent transition-colors"
                                                title="Norsk Tipping odds for this pick (type from NT app). Edge vs NT shows below — that's your real edge."
                                            />
                                            {ntOdds[logKey] != null && (() => {
                                                const ntEdge = (p.prob ?? 0) * ntOdds[logKey] - 1;
                                                return (
                                                    <span className={
                                                        'mono text-xs font-semibold ' +
                                                        (ntEdge >= 0 ? 'text-positive' : 'text-danger')
                                                    }>
                                                        {ntEdge >= 0 ? '+' : ''}{pct(ntEdge)}
                                                    </span>
                                                );
                                            })()}
                                        </div>
                                    </div>

                                    {/* Stake */}
                                    <div className="min-w-[90px] text-right">
                                        <div className="eyebrow">Stake</div>
                                        <div className="mono text-sm text-ink mt-0.5">
                                            {bankroll > 0
                                                ? <>{Math.round(kellyStake).toLocaleString()} <span className="text-ink-muted text-xs">NOK</span></>
                                                : <span className="text-ink-muted text-xs">—</span>
                                            }
                                        </div>
                                    </div>

                                    {/* Actions */}
                                    <div className="flex items-center gap-1.5">
                                        <button
                                            onClick={() => toggleCombo(p)}
                                            className={
                                                'mono text-[0.65rem] uppercase tracking-[0.12em] px-2 py-1 border transition-colors cursor-pointer ' +
                                                (isSelected
                                                    ? 'bg-accent text-paper border-accent'
                                                    : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                                            }
                                            title={isSelected ? 'Remove from combo' : 'Add to combo'}
                                        >
                                            {isSelected ? '✓ Combo' : '+ Combo'}
                                        </button>
                                        {canLog && (
                                            logStatus[logKey] === 'logged' ? (
                                                <span className="mono text-[0.65rem] uppercase tracking-[0.12em] text-positive border-b border-positive/40 px-2 py-1">
                                                    Logged ✓
                                                </span>
                                            ) : (
                                                <button
                                                    onClick={() => setPickToLog({
                                                        pick: p,
                                                        defaultStake: bankroll > 0 ? Math.round(kellyStake) : 100,
                                                    })}
                                                    className="mono text-[0.65rem] uppercase tracking-[0.12em] px-2 py-1 border border-line text-ink-soft hover:text-ink hover:border-ink-muted transition-colors cursor-pointer"
                                                >
                                                    Log
                                                </button>
                                            )
                                        )}
                                    </div>
                                </div>
                            </li>
                        );
                    })}
                </ol>
            )}

            {/* Compound Markets — BTTS & Win, model-only with NT odds entry.
                Lives below the singles list because singles are the primary
                workflow; compounds are a "while you're here" supplement. */}
            <CompoundMarkets />

            {/* Log Bet modal — reused from ValueBets */}
            {pickToLog && (
                <LogBetModal
                    pick={pickToLog.pick}
                    defaultStake={pickToLog.defaultStake}
                    onClose={() => setPickToLog(null)}
                    onLogged={() => {
                        setLogStatus(prev => ({ ...prev, [pickKey(pickToLog.pick)]: 'logged' }));
                        setPickToLog(null);
                    }}
                />
            )}

            {/* Footnote */}
            {top.length > 0 && (
                <p className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mt-6 leading-relaxed">
                    Ranked by{' '}
                    <span className="text-ink">¼-Kelly × ensemble agreement</span>{' '} —
                    embeds edge AND probability AND model confidence into one score.
                    Type NT odds into the <span className="text-ink">vs NT</span> column to see your real edge —
                    Norsk Tipping margins are 8-12% vs Pinnacle's 2-3%, so the sharp edge is an upper bound.
                    Singles are mathematically optimal; combos amplify both edge and variance.
                    One leg per match enforced (multiple picks in the same fixture are correlated).
                </p>
            )}
        </div>
    );
}

export default BestOfWeek;
