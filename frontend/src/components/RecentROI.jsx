/*
RecentROI — landing-page strip showing the last 30 days of settled bets.

Why surface this publicly: a +ROI track record is the only honest proof that
the model picks +EV bets. Hiding bet history behind ?advanced=true makes the
site look like just-another-prediction-toy. Showing actual outcomes builds
credibility — and forces us to bet only on picks we'd defend in public.

Compact strip layout: top-line stats (ROI / count / P&L / win rate) + the top
3 leagues by ROI. Hidden when fewer than 5 settled bets (sample size is too
noisy to make a claim).
*/

import { useEffect, useState } from 'react';
import { footballAPI } from '../services/api';
import { COMPETITION_LABELS } from '../utils/constants';

const pct = (v) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
const signed = (v) => `${v >= 0 ? '+' : ''}${pct(v)}`;
const nok = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()} NOK`;
const MIN_BETS_TO_DISPLAY = 5;

function RecentROI() {
    const [data, setData] = useState(null);
    const [error, setError] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        // ISO date 30 days back, naive (matches backend convention of naive UTC).
        const since = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000)
            .toISOString().slice(0, 10);
        footballAPI.getBetsPerformance({ since }, { signal: controller.signal })
            .then(setData)
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(true);
            });
        return () => controller.abort();
    }, []);

    // Silent fallback — if there's no data or an error, the widget just doesn't
    // render. We don't want a half-broken strip on the landing page if the
    // backend is briefly unhappy.
    if (error || !data) return null;
    if ((data.settled_count ?? 0) < MIN_BETS_TO_DISPLAY) return null;

    const roi = data.roi ?? 0;
    const pl = data.total_profit_loss ?? 0;
    const winRate = data.win_rate ?? 0;
    const clv = data.avg_clv;

    // Top 3 leagues by ROI, requiring ≥3 bets per league for meaning.
    const leagueRows = Object.entries(data.by_league || {})
        .filter(([, agg]) => (agg.count ?? 0) >= 3)
        .sort(([, a], [, b]) => (b.roi ?? 0) - (a.roi ?? 0))
        .slice(0, 3);

    const roiClass = roi >= 0 ? 'text-positive' : 'text-danger';

    return (
        <section className="mb-10 bg-paper-tint border-l-2 border-accent px-5 py-4">
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-3">
                <div className="flex items-baseline gap-2">
                    <span className="eyebrow">Last 30 days</span>
                    <span className="mono text-[0.7rem] text-ink-muted">
                        {data.settled_count} settled
                        {data.pending_count > 0 && (
                            <span className="text-ink-muted"> · {data.pending_count} pending</span>
                        )}
                    </span>
                    {/* Visitor disclosure: operator bets exclusively at Norsk Tipping,
                        whose margins are 8–12% vs the ~2–3% on Pinnacle/exchanges.
                        Without this caller might assume sharp-market replicability. */}
                    <span
                        className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted/80 cursor-help border-b border-dotted border-ink-muted/30"
                        title="All paper bets logged here are priced at Norsk Tipping (Norwegian betting monopoly). NT margins are typically 8–12% vs ~2–3% on Pinnacle and exchanges, so the same picks placed on sharper books would carry materially higher edges than what's shown."
                    >
                        @ NT
                    </span>
                </div>

                <div className="flex items-baseline gap-1.5">
                    <span className={'display text-2xl ' + roiClass}>{signed(roi)}</span>
                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted">ROI</span>
                </div>

                <div className="flex items-baseline gap-1.5">
                    <span className={'mono text-base ' + (pl >= 0 ? 'text-positive' : 'text-danger')}>
                        {nok(pl)}
                    </span>
                </div>

                <div className="flex items-baseline gap-1.5">
                    <span className="mono text-sm text-ink">{pct(winRate)}</span>
                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted">win rate</span>
                </div>

                {clv != null && (
                    <div className="flex items-baseline gap-1.5">
                        <span className={'mono text-sm ' + (clv >= 0 ? 'text-positive' : 'text-ink-soft')}>
                            {signed(clv)}
                        </span>
                        <span
                            className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted cursor-help border-b border-dotted border-ink-muted/40"
                            title="Closing Line Value: avg (placed_odds / closing_odds − 1). Positive over many bets is the only short-run proof of +EV."
                        >
                            CLV
                        </span>
                    </div>
                )}
            </div>

            {leagueRows.length > 0 && (
                <div className="mt-3 pt-3 border-t border-line flex flex-wrap items-baseline gap-x-5 gap-y-1.5">
                    <span
                        className="eyebrow cursor-help border-b border-dotted border-ink-muted/40"
                        title="Singles only. Combos span multiple leagues and skew per-league ROI when bucketed under one of them, so they're excluded from this breakdown. Combo P/L is still in the top-line ROI above."
                    >
                        By league (singles)
                    </span>
                    {leagueRows.map(([league, agg]) => (
                        <div key={league} className="flex items-baseline gap-1.5">
                            <span className="text-sm text-ink">
                                {COMPETITION_LABELS[league] || league}
                            </span>
                            <span className={'mono text-xs ' + (agg.roi >= 0 ? 'text-positive' : 'text-danger')}>
                                {signed(agg.roi)}
                            </span>
                            <span className="mono text-[0.65rem] text-ink-muted">({agg.count})</span>
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
}

export default RecentROI;
