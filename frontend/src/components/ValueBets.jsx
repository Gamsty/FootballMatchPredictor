/*
ValueBets — table of +EV picks (model probability × bookmaker odds − 1).

Rendered when the Dashboard "Value" tab is active. Calls /api/value-bets and
groups picks vertically by match. Shows edge%, decimal odds, best book, and a
Kelly stake guide. Falls back to a paper-aesthetic "not configured" panel when
the backend reports enabled=false (ODDS_API_KEY missing).
*/

import { useState, useEffect } from 'react';
import { footballAPI } from '../services/api';
import { formatTime, formatMatchDate, COMPETITION_LABELS } from '../utils/constants';

// Pretty-print a fraction as a percentage with one decimal.
const pct = (v) => `${(v * 100).toFixed(1)}%`;

// Recommend conservative fractional Kelly (¼-Kelly) — full Kelly assumes the
// model probability is exact, which it never quite is. Quarter-Kelly mitigates
// the variance from estimation error.
const FRACTIONAL_KELLY = 0.25;

function ValueBets({ onSelectMatch }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [minEdge, setMinEdge] = useState(0.03);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        footballAPI.getValueBets({ days: 7, min_edge: minEdge })
            .then(res => { if (!cancelled) setData(res); })
            .catch(err => { if (!cancelled) setError(err.message || 'Failed to load value bets'); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [minEdge]);

    if (loading) {
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

    // Group picks by match — multiple outcomes on the same match (rare but possible)
    // get rendered together so the user sees the full picture.
    const byMatch = new Map();
    for (const pick of picks) {
        if (!byMatch.has(pick.match_id)) byMatch.set(pick.match_id, []);
        byMatch.get(pick.match_id).push(pick);
    }

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
                <div className="flex-1" />
                <div className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                    {picks.length} {picks.length === 1 ? 'pick' : 'picks'}
                    {' · '}
                    {data.meta?.matches_with_odds || 0}/{data.meta?.matches_scanned || 0} matched
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

            {/* Picks */}
            {picks.length > 0 && (
                <div className="space-y-3">
                    {Array.from(byMatch.entries()).map(([matchId, matchPicks]) => {
                        const first = matchPicks[0];
                        return (
                            <div
                                key={matchId}
                                className="bg-paper border border-line hover:border-ink-muted transition-colors"
                            >
                                {/* Match header */}
                                <div className="flex items-center justify-between px-4 py-3 border-b border-line bg-paper-tint">
                                    <div>
                                        <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mb-1">
                                            {COMPETITION_LABELS[first.competition] || first.competition}
                                            <span className="mx-2">·</span>
                                            {formatMatchDate(first.date)} {formatTime(first.date)}
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
                                    {matchPicks.map((pick, i) => (
                                        <div key={i} className="px-4 py-3 flex flex-wrap items-center gap-4">
                                            {/* Outcome */}
                                            <div className="min-w-[100px]">
                                                <div className="eyebrow">Pick</div>
                                                <div className="text-ink font-medium mt-0.5">{pick.outcome}</div>
                                            </div>

                                            {/* Model prob */}
                                            <div className="min-w-[80px]">
                                                <div className="eyebrow">Model</div>
                                                <div className="mono text-sm text-ink mt-0.5">{pct(pick.prob)}</div>
                                            </div>

                                            {/* Odds */}
                                            <div className="min-w-[100px]">
                                                <div className="eyebrow">Best odds</div>
                                                <div className="mono text-sm text-ink mt-0.5">
                                                    {pick.odds.toFixed(2)}
                                                    <span className="text-ink-muted text-xs ml-1.5">{pick.bookmaker}</span>
                                                </div>
                                            </div>

                                            {/* Edge — the headline number */}
                                            <div className="min-w-[80px]">
                                                <div className="eyebrow">Edge</div>
                                                <div className="mono text-sm text-positive font-semibold mt-0.5">
                                                    +{pct(pick.edge)}
                                                </div>
                                            </div>

                                            {/* Kelly */}
                                            <div className="min-w-[100px] ml-auto text-right">
                                                <div className="eyebrow">¼-Kelly stake</div>
                                                <div className="mono text-sm text-ink mt-0.5">
                                                    {pct(pick.kelly * FRACTIONAL_KELLY)}
                                                    <span className="text-ink-muted text-xs ml-1">of roll</span>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Footnote */}
            {picks.length > 0 && (
                <p className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mt-6 leading-relaxed">
                    Edge = model probability × decimal odds − 1.{' '}
                    ¼-Kelly is the recommended stake assuming the model probability is noisy;
                    full Kelly maximises growth only when probabilities are exactly correct.
                </p>
            )}
        </div>
    );
}

export default ValueBets;
