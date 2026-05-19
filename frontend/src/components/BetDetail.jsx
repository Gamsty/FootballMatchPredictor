/*
BetDetail — modal showing one bet's full lifecycle.

Sections:
  - Match summary (single) or "N-leg combo" header + leg list (combo)
  - Pick + odds + stake
  - Status + P/L + settle time
  - CLV breakdown if closing odds exist
  - Notes + bookmaker + model version
  - Delete (canEdit only)

Reads from the bet dict already loaded by the parent — no extra fetch.
*/

import { formatMatchDate } from '../utils/constants';
import { MARKET_BADGE } from '../utils/marketBadges';

const pct = (v) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
const nok = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()} NOK`;

function BetDetail({ bet, onClose, onDelete }) {
    if (!bet) return null;
    const m = bet.match;
    const isCombo = bet.market === 'combo' && Array.isArray(bet.combo_legs) && bet.combo_legs.length > 0;
    const marketBadge = MARKET_BADGE[bet.market];
    const settled = bet.status === 'won' || bet.status === 'lost';
    const clv = (bet.closing_odds && bet.odds_at_bet)
        ? bet.odds_at_bet / bet.closing_odds - 1
        : null;

    return (
        <div
            className="fixed inset-0 z-[60] bg-ink/60 flex items-stretch sm:items-center justify-center sm:p-4 overflow-y-auto animate-fade-in"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-paper w-full max-w-2xl sm:my-8 border-y sm:border border-line animate-slide-in" style={{ boxShadow: 'var(--shadow-modal)' }}>
                {/* Header */}
                <div className="flex items-start justify-between px-4 sm:px-6 py-4 border-b border-line">
                    <div>
                        <div className="eyebrow mb-1">Bet #{bet.id} · {bet.status}</div>
                        <h2 className="display text-xl sm:text-2xl text-ink">
                            {isCombo
                                ? <>{bet.combo_legs.length}-leg combo<span className="text-accent">.</span></>
                                : <>{m?.home} <span className="text-ink-muted">vs</span> {m?.away}<span className="text-accent">.</span></>
                            }
                        </h2>
                        {!isCombo && m && (
                            <p className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted mt-1">
                                {formatMatchDate(m.date)} · {m.competition}
                                {m.home_score != null && m.away_score != null && (
                                    <span className="ml-2 text-ink-soft">
                                        ({m.home_score} − {m.away_score})
                                    </span>
                                )}
                            </p>
                        )}
                    </div>
                    <button
                        onClick={onClose}
                        className="text-ink-muted hover:text-ink text-2xl leading-none cursor-pointer"
                        aria-label="Close"
                    >×</button>
                </div>

                {/* Body */}
                <div className="px-4 sm:px-6 py-5 sm:py-6 space-y-5 sm:space-y-6">
                    {/* Pick */}
                    <Section label="Pick">
                        {isCombo ? (
                            <div className="space-y-2">
                                {bet.combo_legs.map((leg, i) => (
                                    <ComboLegRow key={i} leg={leg} />
                                ))}
                            </div>
                        ) : (
                            <div className="flex items-baseline gap-3 flex-wrap">
                                {marketBadge && (
                                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] bg-paper-tint border border-line text-ink-soft px-2 py-0.5">
                                        {marketBadge}
                                    </span>
                                )}
                                <span className="text-ink text-base">
                                    {bet.outcome_label || bet.outcome_key}
                                </span>
                                {bet.model_prob_at_bet != null && (
                                    <span className="mono text-[0.75rem] text-ink-muted">
                                        model: {(bet.model_prob_at_bet * 100).toFixed(1)}%
                                    </span>
                                )}
                            </div>
                        )}
                    </Section>

                    {/* Numbers grid */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <StatBox label="Odds"   value={bet.odds_at_bet?.toFixed(2) ?? '—'} />
                        <StatBox label="Stake"  value={`${bet.stake} NOK`} />
                        <StatBox
                            label="P/L"
                            value={bet.profit_loss != null ? nok(bet.profit_loss) : '—'}
                            valueClass={
                                bet.profit_loss > 0 ? 'text-positive' :
                                bet.profit_loss < 0 ? 'text-danger' : 'text-ink-muted'
                            }
                        />
                        <StatBox
                            label="Edge at bet"
                            value={bet.edge_at_bet != null
                                ? `${bet.edge_at_bet >= 0 ? '+' : ''}${(bet.edge_at_bet * 100).toFixed(1)}%`
                                : '—'}
                            valueClass={
                                bet.edge_at_bet > 0 ? 'text-positive' :
                                bet.edge_at_bet < 0 ? 'text-danger' : 'text-ink'
                            }
                        />
                    </div>

                    {/* CLV */}
                    {bet.closing_odds && (
                        <Section label="Closing line value">
                            <div className="bg-paper-tint border border-line px-4 py-3">
                                <div className="grid grid-cols-3 gap-3">
                                    <div>
                                        <div className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted">Bet odds</div>
                                        <div className="mono text-sm text-ink">{bet.odds_at_bet?.toFixed(2)}</div>
                                    </div>
                                    <div>
                                        <div className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted">Closing odds</div>
                                        <div className="mono text-sm text-ink">{bet.closing_odds.toFixed(2)}</div>
                                    </div>
                                    <div>
                                        <div className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted">CLV</div>
                                        <div className={
                                            'mono text-sm font-semibold ' +
                                            (clv > 0 ? 'text-positive' : clv < 0 ? 'text-danger' : 'text-ink')
                                        }>
                                            {clv != null ? pct(clv) : '—'}
                                        </div>
                                    </div>
                                </div>
                                <p className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted mt-3">
                                    CLV = bet odds / closing odds − 1. Positive = beat the closing line.
                                </p>
                            </div>
                        </Section>
                    )}

                    {/* Metadata */}
                    <Section label="Metadata">
                        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 mono text-[0.75rem]">
                            <Meta label="Placed" value={fmtDateTime(bet.placed_at)} />
                            <Meta label="Settled" value={settled ? fmtDateTime(bet.settled_at) : 'pending'} />
                            <Meta label="Bookmaker" value={bet.bookmaker || '—'} />
                            <Meta label="Model version" value={bet.model_version_at_bet || '—'} />
                        </dl>
                        {bet.notes && (
                            <div className="mt-3 bg-paper-tint border-l-2 border-line px-3 py-2">
                                <div className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted mb-1">Notes</div>
                                <p className="text-ink-soft text-sm">{bet.notes}</p>
                            </div>
                        )}
                    </Section>

                    {onDelete && (
                        <div className="pt-2 border-t border-line">
                            <button
                                onClick={() => onDelete(bet.id)}
                                className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted hover:text-danger border-b border-dotted border-ink-muted/40 hover:border-danger transition-colors cursor-pointer"
                            >
                                Delete bet
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

// Per-leg row inside the combo Pick section. Shows snapshot data (teams,
// odds, model prob) PLUS live match state — score and resolved leg result —
// when the backend has enriched the leg via _enrich_combo_legs.
function ComboLegRow({ leg }) {
    const hasScore = leg.home_score != null && leg.away_score != null;
    const result = leg.result;  // 'won' | 'lost' | 'void' | 'pending' | undefined (old bets)
    const resultColor = result === 'won' ? 'text-positive'
                      : result === 'lost' ? 'text-danger'
                      : result === 'void' ? 'text-ink-muted'
                      : 'text-ink-soft';
    const resultLabel = result === 'pending' ? leg.match_status || 'pending'
                      : result ? result.toUpperCase()
                      : null;
    return (
        <div className="bg-paper-tint border border-line px-3 py-2">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="text-ink text-sm flex items-baseline gap-2">
                    <span>{leg.home_team} <span className="text-ink-muted">vs</span> {leg.away_team}</span>
                    {hasScore && (
                        <span className="mono text-[0.75rem] text-ink-soft">
                            ({leg.home_score}−{leg.away_score})
                        </span>
                    )}
                </div>
                <div className="mono text-[0.75rem] text-ink-soft flex items-center gap-3 flex-wrap">
                    <span>→ {leg.outcome_label || leg.outcome_key}</span>
                    <span className="text-ink">@ {leg.odds?.toFixed(2)}</span>
                    {leg.prob != null && (
                        <span className="text-ink-muted">hit {(leg.prob * 100).toFixed(0)}%</span>
                    )}
                    {resultLabel && (
                        <span className={'font-semibold uppercase tracking-[0.05em] ' + resultColor}>
                            {resultLabel}
                        </span>
                    )}
                </div>
            </div>
        </div>
    );
}

function Section({ label, children }) {
    return (
        <section>
            <div className="eyebrow mb-2">{label}</div>
            {children}
        </section>
    );
}

function StatBox({ label, value, valueClass = 'text-ink' }) {
    return (
        <div className="bg-paper-tint border border-line px-3 py-2.5">
            <div className="eyebrow">{label}</div>
            <div className={'mono text-base mt-0.5 font-semibold ' + valueClass}>{value}</div>
        </div>
    );
}

function Meta({ label, value }) {
    return (
        <>
            <div className="text-ink-muted uppercase tracking-[0.1em] text-[0.6rem]">{label}</div>
            <div className="text-ink">{value}</div>
        </>
    );
}

function fmtDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
         + ' · '
         + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export default BetDetail;
