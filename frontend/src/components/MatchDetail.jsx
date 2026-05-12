/*
Match Detail Component — Full Match Prediction Modal

Opens when a MatchCard is clicked. Shows all betting markets for a single match.

Sections:
    1. Header: team names with crests, competition, date/time
    2. Recommended bets (Best + Safest) with expandable reasons
    3. Match Result: H/D/A probability bars with implied odds
    4. Double Chance: 1X, X2, 12 probabilities
    5. Market categories: Goals (BTTS, O/U), Half-Time, Corners, Cards
    6. Combo bets: Result+BTTS, Result+O/U, BTTS+O/U combinations

Features:
    - Closes on Escape key or click outside the modal
    - Uses cached prediction data from dashboard (avoids re-fetching)
    - Falls back to API call if market data isn't cached
*/

import { useState, useEffect, useRef } from 'react';
import { footballAPI } from '../services/api';
import {
    formatPercentage, formatTime, formatMatchDate, formatOdds,
    getRecommendedBets,
    MARKET_LABELS, MARKET_CATEGORIES, COMPETITION_LABELS
} from '../utils/constants';

function MatchDetail({ match, onClose }) {
    const [marketData, setMarketData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [expandedReason, setExpandedReason] = useState(null);
    const panelRef = useRef(null);

    useEffect(() => {
        if (match.markets && Object.keys(match.markets).length > 0) {
            setMarketData({
                match_result: match.prediction,
                double_chance: match.double_chance,
                markets: match.markets,
                combos: match.combos || {},
            });
            setLoading(false);
            return;
        }

        const fetchMarkets = async () => {
            try {
                const data = await footballAPI.predictAllMarkets(
                    match.home_team.id, match.away_team.id
                );
                setMarketData(data);
            } catch (err) {
                setError('Failed to load market predictions');
                console.error(err);
            } finally {
                setLoading(false);
            }
        };
        fetchMarkets();
    }, [match]);

    // Close on Escape
    useEffect(() => {
        const handleEscape = (e) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', handleEscape);
        return () => window.removeEventListener('keydown', handleEscape);
    }, [onClose]);

    // Close on click outside
    const handleBackdropClick = (e) => {
        if (panelRef.current && !panelRef.current.contains(e.target)) {
            onClose();
        }
    };

    const compLabel = COMPETITION_LABELS[match.competition] || match.competition;
    const recommendedBets = getRecommendedBets(match);

    return (
        <div
            className="fixed inset-0 z-50 flex items-start justify-center bg-ink/40 backdrop-blur-sm overflow-y-auto p-4 animate-fade-in"
            onClick={handleBackdropClick}
        >
            <div
                ref={panelRef}
                className="bg-paper border border-line w-full max-w-2xl my-8
                           shadow-[0_24px_60px_-20px_rgba(21,17,13,0.35)] animate-slide-in"
            >
                {/* Header */}
                <div className="bg-paper-tint p-6 border-b border-line">
                    <div className="flex justify-between items-start gap-4">
                        <div>
                            <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted mb-3">
                                {compLabel} <span className="text-line">·</span> {formatMatchDate(match.date)} <span className="text-line">·</span> {formatTime(match.date)}
                            </div>
                            <div className="flex items-center gap-3 flex-wrap">
                                {match.home_team.crest && (
                                    <img src={match.home_team.crest} alt="" className="w-7 h-7 object-contain" />
                                )}
                                <span className="display text-xl text-ink">{match.home_team.name}</span>
                                <span className="mono text-xs text-ink-muted uppercase tracking-[0.12em]">vs</span>
                                <span className="display text-xl text-ink-soft">{match.away_team.name}</span>
                                {match.away_team.crest && (
                                    <img src={match.away_team.crest} alt="" className="w-7 h-7 object-contain" />
                                )}
                            </div>
                        </div>
                        <button
                            onClick={onClose}
                            className="text-ink-muted hover:text-accent text-2xl leading-none p-1 transition-colors cursor-pointer"
                            aria-label="Close"
                        >
                            &times;
                        </button>
                    </div>
                </div>

                {/* Body */}
                <div className="p-6 space-y-5">
                    {loading && (
                        <div className="text-center py-12">
                            <div className="inline-block w-3 h-3 bg-accent animate-pulse-soft rounded-full mb-3" />
                            <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">Fetching markets</p>
                        </div>
                    )}

                    {error && (
                        <div className="bg-danger/5 border-l-2 border-danger p-4">
                            <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-danger mb-1">Error</div>
                            <p className="text-ink-soft text-sm">{error}</p>
                        </div>
                    )}

                    {marketData && (
                        <>
                            {/* Recommended bets */}
                            {recommendedBets && recommendedBets.length > 0 && (
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                    {recommendedBets.map((bet) => {
                                        const typeLabels = { best: 'Best bet', safest: 'Safest bet' };
                                        return (
                                            <div key={bet.type} className="border border-line p-4">
                                                <div className="mono text-[0.62rem] uppercase tracking-[0.15em] text-accent mb-2">
                                                    {typeLabels[bet.type] || bet.type}
                                                </div>
                                                <div className="display text-lg text-ink mb-2">{bet.label}</div>
                                                <div className="flex items-baseline justify-between">
                                                    <span className="mono text-sm text-ink font-medium">{formatPercentage(bet.prob)}</span>
                                                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted">
                                                        @ {formatOdds(bet.prob)}
                                                    </span>
                                                </div>
                                                {bet.reason && (
                                                    <>
                                                        <button
                                                            onClick={() => setExpandedReason(expandedReason === bet.type ? null : bet.type)}
                                                            className="mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-muted hover:text-accent mt-3 transition-colors cursor-pointer"
                                                        >
                                                            {expandedReason === bet.type ? '— hide reasoning' : '+ why this bet'}
                                                        </button>
                                                        {expandedReason === bet.type && (
                                                            <ul className="mt-2 space-y-1 pt-2 border-t border-line">
                                                                {bet.reason.map((line, i) => (
                                                                    <li key={i} className="flex gap-2 text-xs text-ink-soft leading-snug">
                                                                        <span className="text-accent mt-0.5 shrink-0">·</span>
                                                                        <span>{line}</span>
                                                                    </li>
                                                                ))}
                                                            </ul>
                                                        )}
                                                    </>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}

                            <Section title="Match result" defaultOpen>
                                <ResultBar prediction={marketData.match_result} match={match} />
                            </Section>

                            {marketData.double_chance && (
                                <Section title="Double chance">
                                    <div className="grid grid-cols-3 gap-3">
                                        {Object.entries(marketData.double_chance).map(([key, dc]) => (
                                            <div key={key} className="border border-line p-3 text-center">
                                                <div className="mono text-[0.6rem] uppercase tracking-[0.12em] text-accent mb-1">{key}</div>
                                                <div className="display text-sm text-ink mb-1">{dc.description}</div>
                                                <div className="mono text-sm text-ink font-medium">{formatPercentage(dc.probability)}</div>
                                                <div className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted mt-0.5">@ {dc.odds}</div>
                                            </div>
                                        ))}
                                    </div>
                                </Section>
                            )}

                            {Object.entries(MARKET_CATEGORIES).map(([catKey, cat]) => {
                                const availableMarkets = cat.markets.filter(m => marketData.markets?.[m]);
                                if (availableMarkets.length === 0) return null;
                                return (
                                    <Section key={catKey} title={cat.label}>
                                        <div className="space-y-1.5">
                                            {availableMarkets.map(marketKey => (
                                                <MarketRow
                                                    key={marketKey}
                                                    label={MARKET_LABELS[marketKey] || marketKey}
                                                    data={marketData.markets[marketKey]}
                                                />
                                            ))}
                                        </div>
                                    </Section>
                                );
                            })}

                            {marketData.combos && Object.keys(marketData.combos).length > 0 && (
                                <Section title="Combo bets">
                                    <ComboTable combos={marketData.combos} />
                                </Section>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}


/* Sub-components — paper aesthetic */

function Section({ title, children, defaultOpen = false }) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="border border-line">
            <button
                onClick={() => setOpen(!open)}
                className="w-full flex items-baseline justify-between px-4 py-3 text-left bg-paper hover:bg-paper-tint transition-colors cursor-pointer"
            >
                <h3 className="display text-base text-ink">{title}</h3>
                <span className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted">
                    {open ? '— hide' : '+ show'}
                </span>
            </button>
            {open && <div className="px-4 py-4 bg-paper-tint border-t border-line">{children}</div>}
        </div>
    );
}

function ResultBar({ prediction, match }) {
    if (!prediction?.probabilities) return null;
    const { home_win, draw, away_win } = prediction.probabilities;

    return (
        <div className="space-y-3">
            <ProbBar label={match.home_team.short_name || match.home_team.name} prob={home_win} accent="ink" />
            <ProbBar label="Draw" prob={draw} accent="muted" />
            <ProbBar label={match.away_team.short_name || match.away_team.name} prob={away_win} accent="accent" />

            {prediction.odds && (
                <div className="flex gap-4 pt-2 mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted border-t border-line mt-2">
                    <span>H @ {prediction.odds.home_win || '-'}</span>
                    <span>D @ {prediction.odds.draw || '-'}</span>
                    <span>A @ {prediction.odds.away_win || '-'}</span>
                </div>
            )}
        </div>
    );
}

function ProbBar({ label, prob, accent }) {
    // accent: 'ink' (home), 'muted' (draw), 'accent' (away)
    const barColor = {
        ink: 'bg-ink',
        muted: 'bg-ink-muted/40',
        accent: 'bg-accent-soft',
    }[accent];
    return (
        <div className="flex items-center gap-3">
            <span className="text-xs text-ink-soft w-28 truncate">{label}</span>
            <div className="flex-1 bg-line h-[3px]">
                <div className={`${barColor} h-full transition-all duration-500`} style={{ width: `${prob * 100}%` }} />
            </div>
            <span className="mono text-xs text-ink w-12 text-right font-medium">{formatPercentage(prob)}</span>
        </div>
    );
}

function MarketRow({ label, data }) {
    if (!data || data.error) return null;

    const probs = data.probabilities || {};
    const entries = Object.entries(probs);
    const best = entries.length > 0 ? entries.reduce((a, b) => b[1] > a[1] ? b : a) : null;

    return (
        <div className="flex items-center justify-between py-2 px-3 bg-paper border border-line">
            <span className="text-xs text-ink-soft">{label}</span>
            <div className="flex items-center gap-3">
                {entries.map(([outcomeLabel, prob]) => {
                    const isBest = best && outcomeLabel === best[0];
                    return (
                        <div key={outcomeLabel} className="flex items-center gap-1">
                            <span className={`mono text-[0.6rem] uppercase tracking-[0.1em] ${isBest ? 'text-accent' : 'text-ink-muted'}`}>
                                {outcomeLabel}
                            </span>
                            <span className={`mono text-xs ${isBest ? 'text-ink font-medium' : 'text-ink-muted'}`}>
                                {formatPercentage(prob)}
                            </span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function ComboTable({ combos }) {
    const groups = {
        'Result + BTTS': [],
        'Result + Over/Under': [],
        'BTTS + Over/Under': [],
    };

    Object.entries(combos).forEach(([key, combo]) => {
        if (key.includes('btts') && (key.startsWith('H_') || key.startsWith('D_') || key.startsWith('A_'))) {
            groups['Result + BTTS'].push({ key, ...combo });
        } else if (key.startsWith('H_over') || key.startsWith('H_under') || key.startsWith('D_over') || key.startsWith('D_under') || key.startsWith('A_over') || key.startsWith('A_under')) {
            groups['Result + Over/Under'].push({ key, ...combo });
        } else if (key.startsWith('btts_yes_over') || key.startsWith('btts_yes_under')) {
            groups['BTTS + Over/Under'].push({ key, ...combo });
        }
    });

    return (
        <div className="space-y-5">
            {Object.entries(groups).map(([title, items]) => {
                if (items.length === 0) return null;
                items.sort((a, b) => b.probability - a.probability);
                return (
                    <div key={title}>
                        <div className="mono text-[0.62rem] uppercase tracking-[0.15em] text-accent mb-2">{title}</div>
                        <div className="grid grid-cols-2 gap-2">
                            {items.map(combo => (
                                <div key={combo.key}
                                    className="flex items-center justify-between py-2 px-3 bg-paper border border-line"
                                >
                                    <span className="text-xs text-ink-soft truncate mr-2">{combo.description}</span>
                                    <div className="flex items-center gap-2 shrink-0">
                                        <span className="mono text-xs text-ink font-medium">{formatPercentage(combo.probability)}</span>
                                        <span className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted">@ {combo.odds || '-'}</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

export default MatchDetail;
