/*
PerfSummary — 4 KPI cards + footnote line.

Reads the same payload shape as GET /api/bets/performance, so the same
component renders the global summary in PerformanceHub AND a filtered
(since=/market=) summary in SegmentDetail.

When settled_count===0 (bets logged but none resolved yet), the cards show
"—" with hints instead of misleading 0% / 0 NOK values.
*/

const pct = (v) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
const nok = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()} NOK`;

function Card({ label, value, valueClass = 'text-ink', hint, size = 'md' }) {
    const valueSize = size === 'lg' ? 'text-3xl' : 'text-xl';
    return (
        <div className="bg-paper-tint border border-line px-3 py-2.5">
            <div className="eyebrow">{label}</div>
            <div className={'display mt-0.5 ' + valueSize + ' ' + valueClass}>{value}</div>
            {hint && <div className="mono text-[0.6rem] text-ink-muted mt-0.5">{hint}</div>}
        </div>
    );
}

function PerfSummary({ perf, size = 'md', showFootnote = true }) {
    if (!perf) return null;

    if (perf.total_bets === 0) {
        return (
            <div className="bg-paper-tint border-l-2 border-accent p-4">
                <div className="eyebrow mb-1">Performance</div>
                <p className="text-ink-soft text-sm">{perf.message || 'No bets yet.'}</p>
            </div>
        );
    }

    // Pre-settlement state: bets logged but none have resolved yet. The
    // backend returns 0/0/0 here, which renders as "0% ROI / 0% Win rate" —
    // technically true but misleading (looks like every bet lost). Surface
    // explicit "—" with a hint so the operator reads it correctly.
    const noSettled = perf.settled_count === 0;
    const roiClass = perf.roi > 0 ? 'text-positive' : perf.roi < 0 ? 'text-danger' : 'text-ink';
    const plClass = perf.total_profit_loss > 0 ? 'text-positive'
                  : perf.total_profit_loss < 0 ? 'text-danger'
                  : 'text-ink';

    return (
        <div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Card size={size} label="P/L"
                      value={noSettled ? '—' : nok(perf.total_profit_loss)}
                      valueClass={noSettled ? 'text-ink-muted' : plClass}
                      hint={noSettled ? 'nothing settled yet' : null} />
                <Card size={size} label="ROI"
                      value={noSettled ? '—' : pct(perf.roi)}
                      valueClass={noSettled ? 'text-ink-muted' : roiClass}
                      hint={noSettled ? `${perf.pending_count} pending` : null} />
                <Card size={size} label="Win rate"
                      value={noSettled ? '—' : pct(perf.win_rate)}
                      valueClass={noSettled ? 'text-ink-muted' : 'text-ink'}
                      hint={noSettled
                          ? (perf.expected_win_rate != null ? `model expects ${pct(perf.expected_win_rate)}` : null)
                          : (perf.expected_win_rate != null ? `model expected ${pct(perf.expected_win_rate)}` : null)} />
                <Card size={size} label="Avg CLV"
                      value={perf.avg_clv != null ? pct(perf.avg_clv) : '—'}
                      hint={perf.clv_sample_size ? `n=${perf.clv_sample_size}` : 'no closing odds yet'} />
            </div>
            {showFootnote && (
                <p className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mt-3 leading-relaxed">
                    Total bets: <span className="text-ink">{perf.total_bets}</span>
                    {perf.singles_count != null && (
                        <> (<span className="text-ink">{perf.singles_count}</span> singles ·{' '}
                            <span className="text-ink">{perf.combos_count}</span> combos)</>
                    )}
                    {' · '}Settled: <span className="text-ink">{perf.settled_count}</span>
                    {' · '}Pending: <span className="text-ink">{perf.pending_count}</span>
                    {perf.void_count > 0 && (
                        <> {' · '}Void: <span className="text-ink">{perf.void_count}</span></>
                    )}
                    {' · '}Avg edge (singles):{' '}
                    <span
                        className="text-ink cursor-help border-b border-dotted border-ink-muted/40"
                        title="Average edge across all logged single-leg bets (settled + pending). Combos are excluded — their multiplicative edge isn't directly comparable."
                    >
                        {perf.avg_edge_at_bet != null ? pct(perf.avg_edge_at_bet) : 'n/a'}
                    </span>
                    {perf.expected_win_rate != null
                        && perf.settled_count >= 10
                        && perf.win_rate < perf.expected_win_rate - 0.05 && (
                        <span className="text-warning ml-2">
                            ← winning {pct(perf.expected_win_rate - perf.win_rate)} less than model predicted
                        </span>
                    )}
                </p>
            )}
        </div>
    );
}

export default PerfSummary;
