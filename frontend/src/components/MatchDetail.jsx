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
    getConfidenceColor, getConfidenceLabel, getRecommendedBets,
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
            className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 backdrop-blur-sm overflow-y-auto p-4"
            onClick={handleBackdropClick}
        >
            <div ref={panelRef} className="bg-gray-900 rounded-2xl shadow-2xl w-full max-w-2xl my-8 border border-gray-800">
                {/* Header */}
                <div className="bg-gray-800 rounded-t-2xl p-5 border-b border-gray-700/50">
                    <div className="flex justify-between items-start">
                        <div>
                            <div className="text-gray-400 text-xs mb-2">
                                {compLabel} &middot; {formatMatchDate(match.date)} {formatTime(match.date)}
                            </div>
                            <div className="flex items-center gap-3 text-white">
                                {match.home_team.crest && (
                                    <img src={match.home_team.crest} alt="" className="w-7 h-7 object-contain" />
                                )}
                                <span className="text-lg font-bold">{match.home_team.name}</span>
                                <span className="text-sm text-gray-500">vs</span>
                                <span className="text-lg font-bold">{match.away_team.name}</span>
                                {match.away_team.crest && (
                                    <img src={match.away_team.crest} alt="" className="w-7 h-7 object-contain" />
                                )}
                            </div>
                        </div>
                        <button
                            onClick={onClose}
                            className="text-gray-500 hover:text-gray-300 text-xl font-bold p-1 transition-colors"
                        >
                            &times;
                        </button>
                    </div>
                </div>

                {/* Body */}
                <div className="p-5 space-y-4">
                    {loading && (
                        <div className="text-center py-12">
                            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto mb-3" />
                            <p className="text-gray-500 text-sm">Loading predictions...</p>
                        </div>
                    )}

                    {error && <div className="text-center py-8 text-red-400 text-sm">{error}</div>}

                    {marketData && (
                        <>
                            {/* Recommended Bets - Three options */}
                            {recommendedBets && recommendedBets.length > 0 && (
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                    {recommendedBets.map((bet) => {
                                        const colors = {
                                            best:    { bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'text-blue-400/70', text: 'text-blue-200', prob: 'text-blue-300' },
                                            safest:  { bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', label: 'text-emerald-400/70', text: 'text-emerald-200', prob: 'text-emerald-300' },
                                        };
                                        const typeLabels = { best: 'Best Bet', safest: 'Safest Bet' };
                                        const c = colors[bet.type] || colors.best;
                                        return (
                                            <div key={bet.type}>
                                                <div className={`${c.bg} border ${c.border} rounded-xl p-3`}>
                                                    <div className={`text-[10px] ${c.label} uppercase font-semibold tracking-wider mb-1.5`}>
                                                        {typeLabels[bet.type]}
                                                    </div>
                                                    <div className={`text-sm font-bold ${c.text} mb-1`}>{bet.label}</div>
                                                    <div className="flex items-center justify-between">
                                                        <span className={`text-sm font-bold ${c.prob} font-mono`}>{formatPercentage(bet.prob)}</span>
                                                        <span className="text-xs text-gray-500">@ {formatOdds(bet.prob)}</span>
                                                    </div>
                                                    {bet.reason && (
                                                        <button
                                                            onClick={() => setExpandedReason(expandedReason === bet.type ? null : bet.type)}
                                                            className={`text-[11px] mt-2 font-medium transition-colors ${c.label} hover:underline`}
                                                        >
                                                            {expandedReason === bet.type ? 'Hide reason' : 'Why this bet?'}
                                                        </button>
                                                    )}
                                                </div>
                                                {expandedReason === bet.type && bet.reason && (
                                                    <div className={`${c.bg} border ${c.border} border-t-0 rounded-b-xl px-3 py-3 -mt-1`}>
                                                        <ul className="space-y-2">
                                                            {bet.reason.map((line, i) => (
                                                                <li key={i} className="flex gap-2 text-[11px] text-gray-400 leading-snug">
                                                                    <span className={`${c.prob} mt-0.5 shrink-0`}>&#8226;</span>
                                                                    <span>{line}</span>
                                                                </li>
                                                            ))}
                                                        </ul>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}

                            {/* Match Result */}
                            <Section title="Match Result" defaultOpen>
                                <ResultBar prediction={marketData.match_result} match={match} />
                            </Section>

                            {/* Double Chance */}
                            {marketData.double_chance && (
                                <Section title="Double Chance">
                                    <div className="grid grid-cols-3 gap-2">
                                        {Object.entries(marketData.double_chance).map(([key, dc]) => (
                                            <div key={key} className="bg-gray-800/60 rounded-lg p-3 text-center border border-gray-700/30">
                                                <div className="text-sm font-bold text-gray-200 mb-0.5">{key}</div>
                                                <div className="text-[10px] text-gray-500 mb-1.5">{dc.description}</div>
                                                <div className="text-sm font-semibold text-blue-300 font-mono">{formatPercentage(dc.probability)}</div>
                                                <div className="text-[10px] text-gray-600">@ {dc.odds}</div>
                                            </div>
                                        ))}
                                    </div>
                                </Section>
                            )}

                            {/* Market Categories */}
                            {Object.entries(MARKET_CATEGORIES).map(([catKey, cat]) => {
                                const availableMarkets = cat.markets.filter(m => marketData.markets?.[m]);
                                if (availableMarkets.length === 0) return null;
                                return (
                                    <Section key={catKey} title={`${cat.icon} ${cat.label}`}>
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

                            {/* Combo Bets */}
                            {marketData.combos && Object.keys(marketData.combos).length > 0 && (
                                <Section title="Combo Bets">
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


/* Sub-components */

function Section({ title, children, defaultOpen = false }) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="rounded-xl border border-gray-800 overflow-hidden">
            <button
                onClick={() => setOpen(!open)}
                className="w-full flex items-center justify-between px-4 py-3 text-left bg-gray-800/40 hover:bg-gray-800/60 transition-colors"
            >
                <h3 className="text-sm font-semibold text-gray-300">{title}</h3>
                <span className="text-gray-600 text-xs">{open ? '▲' : '▼'}</span>
            </button>
            {open && <div className="px-4 py-3 bg-gray-900/50">{children}</div>}
        </div>
    );
}

function ResultBar({ prediction, match }) {
    if (!prediction?.probabilities) return null;
    const { home_win, draw, away_win } = prediction.probabilities;

    return (
        <div className="space-y-2.5">
            <ProbBar label={match.home_team.short_name || match.home_team.name} prob={home_win} color="bg-emerald-500" />
            <ProbBar label="Draw" prob={draw} color="bg-gray-400" />
            <ProbBar label={match.away_team.short_name || match.away_team.name} prob={away_win} color="bg-sky-500" />

            {prediction.odds && (
                <div className="flex gap-4 pt-1 text-[10px] text-gray-600">
                    <span>H @ {prediction.odds.home_win || '-'}</span>
                    <span>D @ {prediction.odds.draw || '-'}</span>
                    <span>A @ {prediction.odds.away_win || '-'}</span>
                </div>
            )}
        </div>
    );
}

function ProbBar({ label, prob, color }) {
    return (
        <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400 w-24 truncate">{label}</span>
            <div className="flex-1 bg-gray-800 rounded-full h-2.5 overflow-hidden">
                <div className={`${color} h-full rounded-full transition-all duration-500`} style={{ width: `${prob * 100}%` }} />
            </div>
            <span className="text-xs text-gray-300 w-12 text-right font-mono">{formatPercentage(prob)}</span>
        </div>
    );
}

function MarketRow({ label, data }) {
    if (!data || data.error) return null;

    // Find the predicted (highest probability) outcome
    const probs = data.probabilities || {};
    const entries = Object.entries(probs);
    const best = entries.length > 0 ? entries.reduce((a, b) => b[1] > a[1] ? b : a) : null;

    return (
        <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-gray-800/40 border border-gray-800/60">
            <span className="text-xs text-gray-400">{label}</span>
            <div className="flex items-center gap-3">
                {entries.map(([outcomeLabel, prob]) => {
                    const isBest = best && outcomeLabel === best[0];
                    return (
                        <div key={outcomeLabel} className="flex items-center gap-1">
                            <span className={`text-[10px] ${isBest ? 'text-blue-300' : 'text-gray-600'}`}>{outcomeLabel}</span>
                            <span className={`text-xs font-mono ${isBest ? 'text-blue-200 font-semibold' : 'text-gray-500'}`}>
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
        <div className="space-y-4">
            {Object.entries(groups).map(([title, items]) => {
                if (items.length === 0) return null;
                // Sort by probability descending
                items.sort((a, b) => b.probability - a.probability);
                return (
                    <div key={title}>
                        <div className="text-[10px] text-gray-500 uppercase font-semibold tracking-wider mb-2">{title}</div>
                        <div className="grid grid-cols-2 gap-1.5">
                            {items.map(combo => (
                                <div key={combo.key}
                                    className="flex items-center justify-between py-1.5 px-2.5 rounded-lg bg-gray-800/40 border border-gray-800/60"
                                >
                                    <span className="text-[11px] text-gray-400 truncate mr-2">{combo.description}</span>
                                    <div className="flex items-center gap-2 shrink-0">
                                        <span className="text-xs font-mono text-gray-300">{formatPercentage(combo.probability)}</span>
                                        <span className="text-[10px] text-gray-600">@ {combo.odds || '-'}</span>
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
