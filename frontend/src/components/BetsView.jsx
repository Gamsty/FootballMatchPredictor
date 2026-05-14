/*
BetsView — paper bet log + performance summary.

Two panels:
  1. Performance summary — ROI, win rate, edge realisation, CLV. Read at-a-glance.
  2. Bet history table — filterable by status (pending / won / lost). Delete via row.

Bets are created from the Value tab (LogBetModal) and auto-settled by the
backend when matches finish.
*/

import { useState, useEffect } from 'react';
import { footballAPI } from '../services/api';
import { formatMatchDate } from '../utils/constants';

const pct = (v) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
const nok = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()} NOK`;

function BetsView({ onClose, canEdit = false }) {
    const [bets, setBets] = useState(null);
    const [perf, setPerf] = useState(null);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState('');
    const [error, setError] = useState(null);

    const load = () => {
        const controller = new AbortController();
        setLoading(true);
        setError(null);
        Promise.all([
            footballAPI.listBets(statusFilter ? { status: statusFilter, limit: 200 } : { limit: 200 },
                                  { signal: controller.signal }),
            footballAPI.getBetsPerformance({}, { signal: controller.signal }),
        ])
            .then(([b, p]) => { setBets(b); setPerf(p); })
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(err.message || 'Failed to load bets');
            })
            .finally(() => setLoading(false));
        return () => controller.abort();
    };

    useEffect(load, [statusFilter]);

    const handleDelete = async (betId) => {
        if (!confirm('Delete this bet?')) return;
        await footballAPI.deleteBet(betId);
        load();
    };

    return (
        <div
            className="fixed inset-0 z-50 bg-ink/60 flex items-start sm:items-center justify-center p-4 overflow-y-auto"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-paper w-full max-w-4xl my-8 border border-line">
                <div className="flex items-start justify-between px-6 py-4 border-b border-line">
                    <div>
                        <div className="eyebrow mb-1">Tracking</div>
                        <h2 className="display text-3xl text-ink">
                            Bet log<span className="text-accent">.</span>
                        </h2>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-ink-muted hover:text-ink text-2xl leading-none cursor-pointer"
                        aria-label="Close"
                    >×</button>
                </div>

                <div className="px-6 py-6 space-y-6">
                    {error && (
                        <div className="bg-paper-tint border-l-2 border-danger p-4">
                            <p className="text-ink-soft text-sm">{error}</p>
                        </div>
                    )}

                    {/* Performance summary */}
                    {perf && (
                        <PerfSummary perf={perf} />
                    )}

                    {/* Status filter */}
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="eyebrow">Filter</span>
                        {[
                            { id: '', label: 'All' },
                            { id: 'pending', label: 'Pending' },
                            { id: 'won', label: 'Won' },
                            { id: 'lost', label: 'Lost' },
                        ].map(opt => (
                            <button
                                key={opt.id}
                                onClick={() => setStatusFilter(opt.id)}
                                className={
                                    'mono text-[0.7rem] uppercase tracking-[0.1em] px-2 py-1 border transition-colors cursor-pointer ' +
                                    (statusFilter === opt.id
                                        ? 'bg-ink text-paper border-ink'
                                        : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                                }
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>

                    {/* Bets table */}
                    {loading && (
                        <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                            Loading bets…
                        </p>
                    )}

                    {!loading && bets?.count === 0 && (
                        <div className="text-center py-12 max-w-md mx-auto">
                            <div className="eyebrow mb-3">Empty log</div>
                            <h3 className="display text-2xl text-ink mb-3">
                                No bets yet<span className="text-accent">.</span>
                            </h3>
                            <p className="text-ink-soft text-sm">
                                Click "Log bet" on any pick in the Value tab to start tracking.
                                ROI and CLV become meaningful once bets settle.
                            </p>
                        </div>
                    )}

                    {!loading && bets?.count > 0 && (
                        <div className="space-y-1.5">
                            {bets.bets.map(b => (
                                <BetRow
                                    key={b.id}
                                    bet={b}
                                    onDelete={canEdit ? handleDelete : null}
                                />
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function PerfSummary({ perf }) {
    if (perf.total_bets === 0) {
        return (
            <div className="bg-paper-tint border-l-2 border-accent p-4">
                <div className="eyebrow mb-1">Performance</div>
                <p className="text-ink-soft text-sm">{perf.message || 'No bets yet.'}</p>
            </div>
        );
    }
    const roiClass = perf.roi > 0 ? 'text-positive' : perf.roi < 0 ? 'text-danger' : 'text-ink';
    const plClass = perf.total_profit_loss > 0 ? 'text-positive' : perf.total_profit_loss < 0 ? 'text-danger' : 'text-ink';
    return (
        <div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Card label="ROI" value={pct(perf.roi)} valueClass={roiClass} />
                <Card label="P/L" value={nok(perf.total_profit_loss)} valueClass={plClass} />
                <Card label="Win rate"
                      value={pct(perf.win_rate)}
                      hint={perf.expected_win_rate != null ? `model expected ${pct(perf.expected_win_rate)}` : null} />
                <Card label="Avg CLV"
                      value={perf.avg_clv != null ? pct(perf.avg_clv) : '—'}
                      hint={perf.clv_sample_size ? `n=${perf.clv_sample_size}` : 'no closing odds yet'} />
            </div>
            <p className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mt-3 leading-relaxed">
                Total bets: <span className="text-ink">{perf.total_bets}</span>
                {perf.singles_count != null && (
                    <> (<span className="text-ink">{perf.singles_count}</span> singles ·{' '}
                        <span className="text-ink">{perf.combos_count}</span> combos)</>
                )}
                {' · '}Settled: <span className="text-ink">{perf.settled_count}</span>
                {' · '}Pending: <span className="text-ink">{perf.pending_count}</span>
                {' · '}Avg edge (singles):{' '}
                <span
                    className="text-ink cursor-help border-b border-dotted border-ink-muted/40"
                    title="Average edge across all logged single-leg bets (settled + pending). Combos are excluded — their multiplicative edge isn't directly comparable."
                >
                    {perf.avg_edge_at_bet != null ? pct(perf.avg_edge_at_bet) : 'n/a'}
                </span>
                {perf.expected_win_rate != null && perf.win_rate < perf.expected_win_rate - 0.05 && (
                    <span className="text-warning ml-2">
                        ← winning {pct(perf.expected_win_rate - perf.win_rate)} less than model predicted
                    </span>
                )}
            </p>
        </div>
    );
}

function Card({ label, value, valueClass = 'text-ink', hint }) {
    return (
        <div className="bg-paper-tint border border-line px-3 py-2.5">
            <div className="eyebrow">{label}</div>
            <div className={'display text-xl mt-0.5 ' + valueClass}>{value}</div>
            {hint && <div className="mono text-[0.6rem] text-ink-muted mt-0.5">{hint}</div>}
        </div>
    );
}

function BetRow({ bet, onDelete }) {
    const m = bet.match;
    const isCombo = bet.market === 'combo' && Array.isArray(bet.combo_legs) && bet.combo_legs.length > 0;
    const statusColor = {
        pending: 'text-ink-soft',
        won: 'text-positive',
        lost: 'text-danger',
        void: 'text-ink-muted',
    }[bet.status] || 'text-ink';
    return (
        <div className="bg-paper border border-line hover:border-ink-muted transition-colors px-4 py-3 flex flex-wrap items-center gap-4">
            <div className="flex-1 min-w-[200px]">
                {isCombo ? (
                    // Combo: render each leg succinctly; outcome on the right.
                    // The anchored match (bet.match) is just the earliest leg —
                    // not informative on its own for combo rows.
                    <div>
                        <div className="text-ink text-sm mb-0.5 flex items-baseline gap-2 flex-wrap">
                            <span>{bet.combo_legs.length}-leg combo</span>
                            <span className="mono text-[0.65rem] text-ink-muted">
                                @ {bet.odds_at_bet?.toFixed(2)}
                            </span>
                            {bet.edge_at_bet != null && (
                                <span
                                    className="mono text-[0.65rem] text-positive cursor-help border-b border-dotted border-positive/30"
                                    title="Combined edge = (∏ leg probabilities × ∏ leg odds) − 1. Multiplicative — not directly comparable to a single bet's edge."
                                >
                                    edge {bet.edge_at_bet >= 0 ? '+' : ''}{(bet.edge_at_bet * 100).toFixed(1)}%
                                </span>
                            )}
                            {bet.model_prob_at_bet != null && (
                                <span className="mono text-[0.65rem] text-ink-muted">
                                    · hit {(bet.model_prob_at_bet * 100).toFixed(1)}%
                                </span>
                            )}
                        </div>
                        <div className="space-y-0.5">
                            {bet.combo_legs.map((leg, i) => (
                                <div key={i} className="mono text-[0.65rem] text-ink-muted">
                                    {leg.home_team} <span className="text-ink-muted/60">vs</span> {leg.away_team}
                                    <span className="text-ink-soft ml-1.5">→ {leg.outcome_label || leg.outcome_key}</span>
                                    <span className="text-ink-muted ml-1.5">@ {leg.odds?.toFixed(2)}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                ) : (
                    <>
                        <div className="text-ink text-sm">
                            {m?.home} <span className="text-ink-muted">vs</span> {m?.away}
                        </div>
                        <div className="mono text-[0.65rem] text-ink-muted mt-0.5">
                            {m?.date && formatMatchDate(m.date)} · {m?.competition || ''}
                            {m?.home_score != null && m?.away_score != null && (
                                <span className="ml-2 text-ink-soft">
                                    ({m.home_score} − {m.away_score})
                                </span>
                            )}
                        </div>
                    </>
                )}
            </div>

            <div className="min-w-[100px]">
                <div className="eyebrow">Pick</div>
                <div className="text-ink text-sm mt-0.5">
                    {isCombo ? `Combo (${bet.combo_legs.length} legs)` : (bet.outcome_label || bet.outcome_key)}
                </div>
            </div>

            <div className="min-w-[80px]">
                <div className="eyebrow">Odds · stake</div>
                <div className="mono text-sm text-ink mt-0.5">
                    {bet.odds_at_bet?.toFixed(2)}
                    <span className="text-ink-muted ml-1">@ {bet.stake} NOK</span>
                </div>
            </div>

            <div className="min-w-[80px]">
                <div className="eyebrow">Status</div>
                <div className={'mono text-sm font-semibold mt-0.5 uppercase tracking-[0.05em] ' + statusColor}>
                    {bet.status}
                </div>
            </div>

            <div className="min-w-[90px] text-right">
                <div className="eyebrow">P/L</div>
                <div className={'mono text-sm font-semibold mt-0.5 ' +
                    (bet.profit_loss > 0 ? 'text-positive' :
                     bet.profit_loss < 0 ? 'text-danger' : 'text-ink-muted')}>
                    {bet.profit_loss != null ? nok(bet.profit_loss) : '—'}
                </div>
            </div>

            {onDelete && (
                <button
                    onClick={() => onDelete(bet.id)}
                    className="text-ink-muted hover:text-danger text-lg leading-none cursor-pointer"
                    aria-label="Delete bet"
                    title="Delete bet"
                >×</button>
            )}
        </div>
    );
}

export default BetsView;
