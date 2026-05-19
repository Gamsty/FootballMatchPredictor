/*
SegmentDetail — slide-in drill view for a single market or league.

Triggered from PerformanceHub's "By market" / "By league" tables. Shows:
  - Segment header + back link
  - Segment-specific KPI strip (PerfSummary, filtered)
  - Bet list for the segment (client-side filtered from full list)

For markets, uses GET /api/bets/performance?market=<key> for the KPIs
(backend already supports this query param). For leagues, computes the KPIs
client-side from the filtered bet list since the endpoint doesn't support
?league= and adding it would mean another backend round-trip.
*/

import { useState, useEffect, useMemo } from 'react';
import { footballAPI } from '../services/api';
import PerfSummary from './PerfSummary';
import BetRow from './BetRow';

function SegmentDetail({ segment, onClose, onSelectBet }) {
    // canEdit not used here — delete actions live in BetDetail so segment rows
    // stay clean. The parent (PerformanceHub) owns the delete cascade.
    const [perf, setPerf] = useState(null);
    const [bets, setBets] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError(null);

        // Always fetch the bet list — segment filter is applied client-side.
        const promises = [
            footballAPI.listBets({ limit: 500 }, { signal: controller.signal }),
        ];
        // For market drills, the backend can compute the segment perf for us.
        // For leagues we compute below.
        if (segment.type === 'market') {
            promises.push(
                footballAPI.getBetsPerformance({ market: segment.key }, { signal: controller.signal })
            );
        }

        Promise.all(promises)
            .then((results) => {
                setBets(results[0]);
                if (segment.type === 'market') {
                    setPerf(results[1]);
                }
            })
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(err.message || 'Failed to load segment');
            })
            .finally(() => setLoading(false));
        return () => controller.abort();
    }, [segment.type, segment.key]);

    // Client-side filter: which bets belong to this segment.
    const segmentBets = useMemo(() => {
        if (!bets?.bets) return [];
        if (segment.type === 'market') {
            return bets.bets.filter(b => b.market === segment.key);
        }
        // League: match against bet.match.competition. Combos are excluded
        // from per-league bucketing (their anchor leg's league isn't
        // representative — keeps parity with backend by_league logic).
        return bets.bets.filter(b =>
            b.market !== 'combo' && b.match?.competition === segment.key
        );
    }, [bets, segment]);

    // For leagues, compute KPIs from segmentBets so we don't need a new endpoint.
    const computedPerf = useMemo(() => {
        if (segment.type === 'market') return null;   // backend provides for markets
        if (!segmentBets) return null;
        return computeSegmentPerf(segmentBets);
    }, [segment.type, segmentBets]);

    const effectivePerf = perf || computedPerf;

    return (
        <div
            className="fixed inset-0 z-50 bg-ink/40 flex justify-end animate-fade-in"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-paper w-full max-w-3xl sm:border-l border-line overflow-y-auto animate-slide-in">
                {/* Header */}
                <div className="sticky top-0 bg-paper/95 backdrop-blur-md border-b border-line z-10">
                    <div className="px-4 sm:px-6 py-4 flex items-start justify-between gap-4">
                        <div>
                            <button
                                onClick={onClose}
                                className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted hover:text-accent mb-2 transition-colors cursor-pointer inline-flex items-center gap-1.5"
                            >
                                <span aria-hidden="true">←</span> Back to Performance
                            </button>
                            <div className="eyebrow mb-1">
                                {segment.type === 'market' ? 'Market drill' : 'League drill'}
                            </div>
                            <h2 className="display text-2xl sm:text-3xl text-ink">
                                {segment.label}<span className="text-accent">.</span>
                            </h2>
                        </div>
                        <button
                            onClick={onClose}
                            className="text-ink-muted hover:text-ink text-2xl leading-none cursor-pointer p-2 -m-2"
                            aria-label="Close"
                        >×</button>
                    </div>
                </div>

                <div className="px-4 sm:px-6 py-5 sm:py-6 space-y-6">
                    {error && (
                        <div className="bg-paper-tint border-l-2 border-danger p-4">
                            <p className="text-ink-soft text-sm">{error}</p>
                        </div>
                    )}

                    {loading && (
                        <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                            Loading segment…
                        </p>
                    )}

                    {effectivePerf && (
                        <PerfSummary perf={effectivePerf} />
                    )}

                    <div>
                        <div className="eyebrow mb-3">Bets in this segment</div>
                        {segmentBets.length === 0 ? (
                            <div className="bg-paper-tint border border-line px-4 py-6 text-center">
                                <p className="text-ink-soft text-sm">No bets logged for this segment.</p>
                            </div>
                        ) : (
                            <div className="space-y-1.5">
                                {segmentBets.map(b => (
                                    <BetRow
                                        key={b.id}
                                        bet={b}
                                        onSelect={onSelectBet}
                                    />
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

// Mirror the shape /api/bets/performance returns so PerfSummary just works.
// Computed client-side for leagues (the backend endpoint only supports market=,
// not league=, and adding it would be a separate backend change).
function computeSegmentPerf(bets) {
    if (!bets || bets.length === 0) {
        return { total_bets: 0, message: 'No bets in this segment yet.' };
    }
    const settled = bets.filter(b => b.status === 'won' || b.status === 'lost');
    const pending = bets.filter(b => b.status === 'pending');
    const voids = bets.filter(b => b.status === 'void');
    const won = settled.filter(b => b.status === 'won');
    const totalStake = settled.reduce((s, b) => s + (b.stake || 0), 0);
    const totalPl = settled.reduce((s, b) => s + (b.profit_loss || 0), 0);
    const roi = totalStake > 0 ? totalPl / totalStake : 0;
    const winRate = settled.length > 0 ? won.length / settled.length : 0;
    const withEdge = bets.filter(b => b.edge_at_bet != null && b.market !== 'combo');
    const avgEdge = withEdge.length > 0
        ? withEdge.reduce((s, b) => s + b.edge_at_bet, 0) / withEdge.length
        : null;
    const withProb = bets.filter(b => b.model_prob_at_bet != null && b.market !== 'combo');
    const avgProb = withProb.length > 0
        ? withProb.reduce((s, b) => s + b.model_prob_at_bet, 0) / withProb.length
        : null;
    return {
        total_bets: bets.length,
        settled_count: settled.length,
        pending_count: pending.length,
        void_count: voids.length,
        won_count: won.length,
        total_stake: totalStake,
        total_profit_loss: totalPl,
        roi,
        win_rate: winRate,
        avg_edge_at_bet: avgEdge,
        expected_win_rate: avgProb,
        singles_count: bets.filter(b => b.market !== 'combo').length,
        combos_count: bets.filter(b => b.market === 'combo').length,
        // CLV requires closing odds — leave null for the client-side path.
        // The market drill (which gets perf from backend) will show real CLV;
        // the league drill will show "—" with the "no closing odds yet" hint.
        avg_clv: null,
        clv_sample_size: 0,
    };
}

export default SegmentDetail;
