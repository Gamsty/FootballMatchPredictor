/*
CompoundMarkets — model-only compound-bet ideas (BTTS & Win etc.).

Why this exists separately from BestOfWeek's main picks:
  - The Odds API doesn't quote BTTS or BTTS-and-result on its bulk endpoint.
    Surfacing those via the per-event endpoint would burn 73x quota per scan.
  - But Norsk Tipping's most popular tippekupong markets ARE compound (BTTS
    & Win, Result + Over/Under). And NT is the only book we're betting on.
  - Our model already produces compound probabilities (`combos` in the
    prediction payload). So we can show the model's number and let the user
    type the NT odds — same edge calculation as the singles, no API spend.

Display:
  - Top N matches by best-BTTS-Yes-and-Win probability, filtered to ≥25%
    (lower is too speculative to bet at NT's margins).
  - For each match, the highest-probability "Win & BTTS Yes" combo is shown.
  - User enters NT odds → edge vs NT computed inline.
  - NT odds persisted to localStorage (different key from singles to avoid
    collision between "Home Win" singles and "Home Win & BTTS" compounds).
*/

import { useEffect, useState } from 'react';
import { footballAPI } from '../services/api';
import { formatTime, formatMatchDate, COMPETITION_LABELS } from '../utils/constants';

const pct = (v) => `${(v * 100).toFixed(1)}%`;
const NT_COMPOUND_STORAGE_KEY = 'fmp.nt_compound_odds.v1';
const MIN_PROB = 0.25;
const TOP_N = 10;

// Which compound keys we surface. Stick to "X & BTTS Yes" — the result+goals
// combos like "Home Win & Over 2.5" are already implicit in the totals market
// of singles. BTTS-No combos are a less common NT line and have lower
// probability so they'd flood out the more interesting picks.
const BTTS_WIN_KEYS = ['H_btts_yes', 'D_btts_yes', 'A_btts_yes'];

function loadCompoundOdds() {
    try {
        const raw = localStorage.getItem(NT_COMPOUND_STORAGE_KEY);
        return raw ? JSON.parse(raw) : {};
    } catch {
        return {};
    }
}
function saveCompoundOdds(map) {
    try {
        localStorage.setItem(NT_COMPOUND_STORAGE_KEY, JSON.stringify(map));
    } catch {
        // localStorage full or disabled — silent fail
    }
}

function CompoundMarkets() {
    const [matches, setMatches] = useState(null);
    const [error, setError] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [ntOdds, setNtOdds] = useState(loadCompoundOdds);

    useEffect(() => {
        const controller = new AbortController();
        footballAPI.getUpcomingPredictions({ days: 7, sort_by: 'date' })
            .then(data => setMatches(data.matches || []))
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(true);
            });
        return () => controller.abort();
    }, []);

    useEffect(() => { saveCompoundOdds(ntOdds); }, [ntOdds]);

    const setOddsFor = (key, raw) => {
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

    if (error || !matches) return null;

    // For each match, find the best BTTS & Win combo (highest probability of
    // the three result variants). Skip matches where the best is below MIN_PROB
    // or where the combo data is missing.
    const candidates = matches
        .map(m => {
            const combos = m.combos || {};
            let best = null;
            for (const key of BTTS_WIN_KEYS) {
                const c = combos[key];
                if (!c || c.probability == null) continue;
                if (!best || c.probability > best.probability) {
                    best = { ...c, key };
                }
            }
            if (!best || best.probability < MIN_PROB) return null;
            return { match: m, best };
        })
        .filter(Boolean)
        .sort((a, b) => b.best.probability - a.best.probability)
        .slice(0, TOP_N);

    if (candidates.length === 0) return null;

    return (
        <section className="mt-12">
            <button
                onClick={() => setExpanded(e => !e)}
                className="w-full flex items-baseline justify-between pb-3 border-b border-line cursor-pointer text-left hover:opacity-80 transition-opacity"
                aria-expanded={expanded}
            >
                <div>
                    <div className="eyebrow mb-1">Compound markets</div>
                    <h3 className="display text-xl text-ink">
                        BTTS &amp; Win<span className="text-accent">.</span>
                        <span className="text-ink-muted text-sm font-light ml-3">
                            Type NT odds to find edge — we don't have these on the sharp side
                        </span>
                    </h3>
                </div>
                <span className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                    {candidates.length} matches · {expanded ? '−' : '+'}
                </span>
            </button>

            {expanded && (
                <div className="mt-4 space-y-2">
                    {candidates.map(({ match, best }) => {
                        const compoundKey = `${match.id}-${best.key}`;
                        const ntPrice = ntOdds[compoundKey];
                        const edge = ntPrice ? best.probability * ntPrice - 1 : null;
                        return (
                            <div
                                key={compoundKey}
                                className="bg-paper border border-line hover:border-ink-muted transition-colors px-4 py-3 flex flex-wrap items-center gap-4"
                            >
                                <div className="flex-1 min-w-[220px]">
                                    <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                                        {COMPETITION_LABELS[match.competition] || match.competition}
                                        <span className="mx-1.5">·</span>
                                        {formatMatchDate(match.date)} {formatTime(match.date)}
                                    </div>
                                    <div className="text-ink font-medium mt-0.5">
                                        {match.home_team?.name}
                                        <span className="text-ink-muted mx-2">vs</span>
                                        {match.away_team?.name}
                                    </div>
                                </div>

                                <div className="min-w-[170px]">
                                    <div className="eyebrow">Pick</div>
                                    <div className="text-ink text-sm mt-0.5">{best.description}</div>
                                </div>

                                <div className="min-w-[80px]">
                                    <div className="eyebrow">Model prob</div>
                                    <div className="mono text-sm text-ink mt-0.5">{pct(best.probability)}</div>
                                </div>

                                <div className="min-w-[80px]">
                                    <div className="eyebrow">Fair odds</div>
                                    <div className="mono text-sm text-ink-soft mt-0.5">{best.odds?.toFixed(2)}</div>
                                </div>

                                <div className="min-w-[110px]">
                                    <div className="eyebrow">NT odds</div>
                                    <div className="mt-0.5 flex items-center gap-1.5">
                                        <input
                                            type="number"
                                            min="1.01"
                                            step="0.05"
                                            value={ntPrice ?? ''}
                                            onChange={(e) => setOddsFor(compoundKey, e.target.value)}
                                            placeholder="—"
                                            className="w-16 bg-paper border border-line px-1.5 py-0.5 mono text-xs text-ink focus:outline-none focus:border-accent transition-colors"
                                            title="Norsk Tipping odds for this compound pick"
                                        />
                                        {edge != null && (
                                            <span className={
                                                'mono text-xs font-semibold ' +
                                                (edge >= 0 ? 'text-positive' : 'text-danger')
                                            }>
                                                {edge >= 0 ? '+' : ''}{pct(edge)}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {expanded && (
                <p className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mt-4 leading-relaxed">
                    Compound probabilities are derived from the BTTS and result models assuming
                    rough independence — that's an approximation: a team that scores tends to
                    concede less, but only weakly. Edge here should be treated as a noisier
                    upper bound than the singles' edge. Margin on NT compound markets is typically
                    15-20%, so demand a thicker edge than for singles before placing.
                </p>
            )}
        </section>
    );
}

export default CompoundMarkets;
