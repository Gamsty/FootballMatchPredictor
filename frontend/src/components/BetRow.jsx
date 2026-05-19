/*
BetRow — one bet rendered as a hairline-bordered paper row.

Used by BetLog, SegmentDetail, and the Recent Activity section of
PerformanceHub. Layout adapts to single vs combo. Click opens detail modal
when onSelect is provided; delete button only renders if onDelete is set.

`dense` mode hides the per-leg list for combos — used inside the recent
activity strip to keep the row compact.
*/

import { formatMatchDate } from '../utils/constants';
import { MARKET_BADGE } from '../utils/marketBadges';

const nok = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()} NOK`;

const STATUS_COLOR = {
    pending: 'text-ink-soft',
    won: 'text-positive',
    lost: 'text-danger',
    void: 'text-ink-muted',
};

function BetRow({ bet, onDelete, onSelect, dense = false }) {
    const m = bet.match;
    const isCombo = bet.market === 'combo' && Array.isArray(bet.combo_legs) && bet.combo_legs.length > 0;
    const marketBadge = MARKET_BADGE[bet.market];
    const statusColor = STATUS_COLOR[bet.status] || 'text-ink';
    const clickable = !!onSelect;

    const handleRowClick = (e) => {
        // Don't trigger select when clicking the delete button or its descendants.
        if (e.target.closest('[data-action="delete"]')) return;
        if (clickable) onSelect(bet);
    };

    return (
        <div
            onClick={handleRowClick}
            className={
                'bg-paper border border-line transition-colors flex flex-wrap items-center gap-4 ' +
                (dense ? 'px-3 py-2 ' : 'px-4 py-3 ') +
                (clickable ? 'hover:border-ink-muted cursor-pointer' : 'hover:border-ink-muted/60')
            }
        >
            <div className="flex-1 min-w-[200px]">
                {isCombo ? (
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
                        {!dense && (
                            <div className="space-y-0.5">
                                {bet.combo_legs.map((leg, i) => (
                                    <div key={i} className="mono text-[0.65rem] text-ink-muted">
                                        {leg.home_team} <span className="text-ink-muted/60">vs</span> {leg.away_team}
                                        <span className="text-ink-soft ml-1.5">→ {leg.outcome_label || leg.outcome_key}</span>
                                        <span className="text-ink-muted ml-1.5">@ {leg.odds?.toFixed(2)}</span>
                                    </div>
                                ))}
                            </div>
                        )}
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
                <div className="eyebrow flex items-center gap-1.5">
                    Pick
                    {marketBadge && !isCombo && (
                        <span className="mono text-[0.6rem] tracking-normal normal-case text-ink-muted bg-paper-tint px-1 py-0.5 border border-line">
                            {marketBadge}
                        </span>
                    )}
                </div>
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
                    data-action="delete"
                    onClick={(e) => { e.stopPropagation(); onDelete(bet.id); }}
                    className="text-ink-muted hover:text-danger text-lg leading-none cursor-pointer"
                    aria-label="Delete bet"
                    title="Delete bet"
                >×</button>
            )}
        </div>
    );
}

export default BetRow;
