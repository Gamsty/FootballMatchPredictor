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

    const plColor = bet.profit_loss > 0 ? 'text-positive'
                  : bet.profit_loss < 0 ? 'text-danger'
                  : 'text-ink-muted';

    return (
        <div
            onClick={handleRowClick}
            className={
                'bg-paper border border-line transition-colors ' +
                (dense ? 'px-3 py-2 ' : 'px-4 py-3 ') +
                (clickable ? 'hover:border-ink-muted cursor-pointer' : 'hover:border-ink-muted/60')
            }
        >
            {/* Top row: match/combo header + (mobile only) delete handle */}
            <div className="flex items-start justify-between gap-3 sm:hidden">
                <BetHeader bet={bet} isCombo={isCombo} m={m} dense={dense} />
                {onDelete && (
                    <DeleteButton onClick={() => onDelete(bet.id)} />
                )}
            </div>

            {/* Mobile metrics row — 3-col grid below the header */}
            <div className="sm:hidden grid grid-cols-3 gap-3 mt-2 pt-2 border-t border-line-soft">
                <MobileCell label="Pick" valueClass="text-ink">
                    <div className="flex items-baseline gap-1.5 flex-wrap">
                        {marketBadge && !isCombo && (
                            <span className="mono text-[0.55rem] uppercase tracking-[0.08em] text-ink-muted bg-paper-tint px-1 py-0.5 border border-line">
                                {marketBadge}
                            </span>
                        )}
                        <span className="text-[0.8rem]">
                            {isCombo ? `${bet.combo_legs.length} legs` : (bet.outcome_label || bet.outcome_key)}
                        </span>
                    </div>
                </MobileCell>
                <MobileCell label="Odds · stake">
                    <span className="mono text-[0.8rem] text-ink">
                        {bet.odds_at_bet?.toFixed(2)}
                        <span className="text-ink-muted"> · {bet.stake} NOK</span>
                    </span>
                </MobileCell>
                <MobileCell label="P/L · status" align="right">
                    <div className={'mono text-[0.8rem] font-semibold ' + plColor}>
                        {bet.profit_loss != null ? nok(bet.profit_loss) : '—'}
                    </div>
                    <div className={'mono text-[0.6rem] font-semibold uppercase tracking-[0.05em] ' + statusColor}>
                        {bet.status}
                    </div>
                </MobileCell>
            </div>

            {/* Desktop layout — flex row with all cells inline */}
            <div className="hidden sm:flex flex-wrap items-center gap-4">
                <div className="flex-1 min-w-[200px]">
                    <BetHeader bet={bet} isCombo={isCombo} m={m} dense={dense} />
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
                    <div className={'mono text-sm font-semibold mt-0.5 ' + plColor}>
                        {bet.profit_loss != null ? nok(bet.profit_loss) : '—'}
                    </div>
                </div>

                {onDelete && (
                    <DeleteButton onClick={() => onDelete(bet.id)} />
                )}
            </div>
        </div>
    );
}

// Match/combo header — shared between mobile (top row) and desktop (first cell).
function BetHeader({ bet, isCombo, m, dense }) {
    if (isCombo) {
        return (
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
        );
    }
    return (
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
    );
}

function MobileCell({ label, children, align = 'left' }) {
    return (
        <div className={align === 'right' ? 'text-right' : ''}>
            <div className="mono text-[0.55rem] uppercase tracking-[0.12em] text-ink-muted">{label}</div>
            <div className="mt-0.5">{children}</div>
        </div>
    );
}

function DeleteButton({ onClick }) {
    return (
        <button
            data-action="delete"
            onClick={(e) => { e.stopPropagation(); onClick(); }}
            className="text-ink-muted hover:text-danger text-2xl sm:text-lg leading-none cursor-pointer px-2 py-1 -mr-2 sm:p-0 sm:m-0"
            aria-label="Delete bet"
            title="Delete bet"
        >×</button>
    );
}

export default BetRow;
