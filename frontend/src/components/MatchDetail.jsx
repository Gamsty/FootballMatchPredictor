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

// ---- Frontend → backend market-key mapping ----------------------------------
// MARKET_LABELS uses display-oriented keys (over_2_5, total_cards_over_3_5),
// while _BET_OUTCOME_RESOLVERS on the backend uses settle-oriented keys
// (totals_2_5, cards_3_5). The accumulator + log endpoints need the backend
// names. Returns null when no backend resolver exists for that frontend
// market — such picks aren't loggable (e.g. corners_result, home_wins_…).
function backendMarketFor(frontendKey) {
    if (['btts', 'ht_result', 'h2h', 'double_chance'].includes(frontendKey)) return frontendKey;
    if (frontendKey.startsWith('over_')) {
        return `totals_${frontendKey.replace('over_', '')}`;
    }
    if (frontendKey.startsWith('total_cards_over_')) {
        return `cards_${frontendKey.replace('total_cards_over_', '')}`;
    }
    if (frontendKey.startsWith('total_corners_over_')) {
        return `corners_${frontendKey.replace('total_corners_over_', '')}`;
    }
    return null;
}

// Map display outcome label → backend outcome_key. Market data exposes
// probabilities keyed by display labels ('Over', 'Under', 'Yes', etc.);
// backend resolvers expect lowercase identifiers.
const OUTCOME_KEY_MAP = {
    'Over': 'over', 'Under': 'under',
    'Yes': 'yes', 'No': 'no',
    'Home': 'home', 'Draw': 'draw', 'Away': 'away',
    'HOME_WIN': 'home', 'DRAW': 'draw', 'AWAY_WIN': 'away',
    '1X': '1x', 'X2': 'x2', '12': '12',
};
function backendOutcomeFor(outcomeLabel) {
    return OUTCOME_KEY_MAP[outcomeLabel] ?? String(outcomeLabel).toLowerCase();
}

// MatchDetail now feeds clicks into the shared accumulator slip instead of
// opening LogBetModal directly. Operator picks one or more outcomes, then
// confirms via the accumulator bar on the Dashboard (which auto-routes:
// 1 leg → single bet POST, 2+ → combo POST). Lets the user assemble multi-
// market combos from inside a single match's detail view, plus stack picks
// from multiple matches before deciding to log.
function MatchDetail({ match, onClose, canLog = false,
                      accumulatorBetByMatchId = {},
                      accumulatorCount = 0,
                      onAddToSlip = () => {} }) {
    const [marketData, setMarketData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [expandedReason, setExpandedReason] = useState(null);
    const panelRef = useRef(null);

    // Accumulator stores one selection per match (keyed by match.id). If this
    // match already has a selection, the toggle helper expects bet.label to
    // recognise "same pick clicked again → remove". We use the leg's display
    // label for that comparison since it's what gets stored in the slip.
    const currentSlipLabel = accumulatorBetByMatchId[match.id];
    const handleSlip = (label, prob, betMarket, betOutcomeKey) => {
        // Drop unloggable picks early — backendMarketFor returns null for
        // markets without a settle resolver (corners_result, etc.).
        if (!betMarket || !betOutcomeKey) return;
        onAddToSlip(match, { label, prob, betMarket, betOutcomeKey });
    };

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
        // Fragment-wrap so LogBetModal is a SIBLING of the backdrop div, not a
        // child — otherwise its bubble-up clicks reach `handleBackdropClick`
        // (which uses panelRef.contains, and the modal isn't inside the panel)
        // and close MatchDetail mid-submit, killing the POST without UI feedback.
        <>
        <div
            className="fixed inset-0 z-50 flex items-stretch sm:items-start justify-center bg-ink/40 backdrop-blur-sm overflow-y-auto sm:p-4 animate-fade-in"
            onClick={handleBackdropClick}
        >
            <div
                ref={panelRef}
                className="bg-paper border-y sm:border border-line w-full max-w-2xl sm:my-8
                           shadow-[0_24px_60px_-20px_rgba(21,17,13,0.35)] animate-slide-in"
            >
                {/* Header */}
                <div className="bg-paper-tint p-4 sm:p-6 border-b border-line">
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
                        <div className="flex items-start gap-2 shrink-0">
                            {canLog && accumulatorCount > 0 && (
                                <button
                                    onClick={onClose}
                                    title="Close to view + log your slip"
                                    className="mono text-[0.65rem] uppercase tracking-[0.12em] px-2 py-1.5 bg-accent text-paper border border-accent hover:bg-accent-soft transition-colors cursor-pointer inline-flex items-center gap-1.5"
                                >
                                    <span>Slip</span>
                                    <span className="mono">{accumulatorCount}</span>
                                </button>
                            )}
                            <button
                                onClick={onClose}
                                className="text-ink-muted hover:text-accent text-2xl leading-none p-1 transition-colors cursor-pointer"
                                aria-label="Close"
                            >
                                &times;
                            </button>
                        </div>
                    </div>
                </div>

                {/* Body */}
                <div className="p-4 sm:p-6 space-y-5">
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
                                        // bet carries betMarket + betOutcomeKey from collectCandidates
                                        const canLogThis = canLog && bet.betMarket && bet.betOutcomeKey;
                                        const inSlip = currentSlipLabel === bet.label;
                                        return (
                                            <div key={bet.type} className={'border p-4 transition-colors ' + (inSlip ? 'border-accent bg-accent/5' : 'border-line')}>
                                                <div className="mono text-[0.62rem] uppercase tracking-[0.15em] text-accent mb-2">
                                                    {typeLabels[bet.type] || bet.type}
                                                </div>
                                                <div className="display text-lg text-ink mb-2">{bet.label}</div>
                                                <div className="flex items-baseline justify-between gap-2">
                                                    <span className="mono text-sm text-ink font-medium">{formatPercentage(bet.prob)}</span>
                                                    <span className="mono text-[0.65rem] uppercase tracking-[0.1em] text-ink-muted">
                                                        @ {formatOdds(bet.prob)}
                                                    </span>
                                                </div>
                                                {canLogThis && (
                                                    <button
                                                        onClick={() => handleSlip(bet.label, bet.prob, bet.betMarket, bet.betOutcomeKey)}
                                                        className={
                                                            'mono text-[0.65rem] uppercase tracking-[0.1em] mt-3 px-2.5 py-1.5 border transition-colors cursor-pointer block w-full text-center ' +
                                                            (inSlip
                                                                ? 'bg-accent text-paper border-accent hover:bg-accent-soft hover:border-accent-soft'
                                                                : 'border-line text-ink-soft hover:text-paper hover:bg-ink hover:border-ink')
                                                        }
                                                    >
                                                        {inSlip ? '✓ in slip — remove' : '+ add to slip'}
                                                    </button>
                                                )}
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
                                <ResultBar
                                    prediction={marketData.match_result}
                                    match={match}
                                    canLog={canLog}
                                    onLog={setPickToLog}
                                />
                            </Section>

                            {marketData.double_chance && (
                                <Section title="Double chance">
                                    <div className="grid grid-cols-3 gap-3">
                                        {Object.entries(marketData.double_chance).map(([key, dc]) => {
                                            const outcomeKey = key.toLowerCase();  // '1X' → '1x'
                                            return (
                                                <div key={key} className="border border-line p-3 text-center">
                                                    <div className="mono text-[0.6rem] uppercase tracking-[0.12em] text-accent mb-1">{key}</div>
                                                    <div className="display text-sm text-ink mb-1">{dc.description}</div>
                                                    <div className="mono text-sm text-ink font-medium">{formatPercentage(dc.probability)}</div>
                                                    <div className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted mt-0.5">@ {dc.odds}</div>
                                                    {canLog && (
                                                        <button
                                                            onClick={() => setPickToLog(makePick({
                                                                match,
                                                                market: 'double_chance',
                                                                outcomeKey,
                                                                outcomeLabel: dc.description,
                                                                prob: dc.probability,
                                                            }))}
                                                            className="mono text-[0.6rem] uppercase tracking-[0.1em] mt-2 px-2 py-1 border border-line text-ink-soft hover:text-paper hover:bg-ink hover:border-ink transition-colors cursor-pointer w-full"
                                                        >
                                                            Log →
                                                        </button>
                                                    )}
                                                </div>
                                            );
                                        })}
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
                                                    frontendKey={marketKey}
                                                    match={match}
                                                    canLog={canLog}
                                                    onLog={setPickToLog}
                                                />
                                            ))}
                                        </div>
                                    </Section>
                                );
                            })}

                            {marketData.combos && Object.keys(marketData.combos).length > 0 && (
                                <Section title="Combo bets">
                                    <ComboTable
                                        combos={marketData.combos}
                                        match={match}
                                        canLog={canLog}
                                        onLog={setPickToLog}
                                    />
                                </Section>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>

        {pickToLog && (
            <LogBetModal
                pick={pickToLog}
                defaultStake={100}
                defaultOdds={pickToLog.odds}
                onClose={() => setPickToLog(null)}
                onLogged={() => {
                    setPickToLog(null);
                    setLogFlash(true);
                    setTimeout(() => setLogFlash(false), 3000);
                }}
            />
        )}

        {logFlash && (
            <div className="fixed bottom-6 right-6 z-[60] bg-paper border border-positive px-4 py-2 mono text-[0.7rem] uppercase tracking-[0.12em] text-positive">
                Bet logged ✓
            </div>
        )}
        </>
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

function ResultBar({ prediction, match, canLog = false, onLog }) {
    if (!prediction?.probabilities) return null;
    const { home_win, draw, away_win } = prediction.probabilities;

    const handleLog = (outcomeKey, label, prob) => {
        onLog?.(makePick({
            match, market: 'h2h', outcomeKey, outcomeLabel: label, prob,
        }));
    };

    return (
        <div className="space-y-3">
            <ProbBar
                label={match.home_team.short_name || match.home_team.name}
                prob={home_win} accent="ink"
                canLog={canLog}
                onLog={() => handleLog('home', `${match.home_team.name} Win`, home_win)}
            />
            <ProbBar
                label="Draw" prob={draw} accent="muted"
                canLog={canLog}
                onLog={() => handleLog('draw', 'Draw', draw)}
            />
            <ProbBar
                label={match.away_team.short_name || match.away_team.name}
                prob={away_win} accent="accent"
                canLog={canLog}
                onLog={() => handleLog('away', `${match.away_team.name} Win`, away_win)}
            />

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

function ProbBar({ label, prob, accent, canLog = false, onLog }) {
    // accent: 'ink' (home), 'muted' (draw), 'accent' (away)
    const barColor = {
        ink: 'bg-ink',
        muted: 'bg-ink-muted/40',
        accent: 'bg-accent-soft',
    }[accent];
    return (
        <div className="flex items-center gap-3">
            <span className="text-xs text-ink-soft w-20 sm:w-28 truncate">{label}</span>
            <div className="flex-1 bg-line h-[3px]">
                <div className={`${barColor} h-full transition-all duration-500`} style={{ width: `${prob * 100}%` }} />
            </div>
            <span className="mono text-xs text-ink w-12 text-right font-medium">{formatPercentage(prob)}</span>
            {canLog && (
                <button
                    onClick={onLog}
                    title="Log this pick"
                    className="mono text-[0.65rem] text-ink-muted hover:text-paper hover:bg-ink border border-line hover:border-ink w-6 h-6 leading-none flex items-center justify-center transition-colors cursor-pointer"
                >
                    +
                </button>
            )}
        </div>
    );
}

function MarketRow({ label, data, frontendKey, match, canLog = false, onLog }) {
    if (!data || data.error) return null;

    const probs = data.probabilities || {};
    const entries = Object.entries(probs);
    const best = entries.length > 0 ? entries.reduce((a, b) => b[1] > a[1] ? b : a) : null;
    const backendMarket = backendMarketFor(frontendKey);
    const loggable = canLog && backendMarket;

    return (
        <div className="flex items-center justify-between py-2 px-3 bg-paper border border-line gap-2 flex-wrap">
            <span className="text-xs text-ink-soft min-w-0">{label}</span>
            <div className="flex items-center gap-3 flex-wrap">
                {entries.map(([outcomeLabel, prob]) => {
                    const isBest = best && outcomeLabel === best[0];
                    const outcomeKey = backendOutcomeFor(outcomeLabel);
                    return (
                        <div key={outcomeLabel} className="flex items-center gap-1">
                            <span className={`mono text-[0.6rem] uppercase tracking-[0.1em] ${isBest ? 'text-accent' : 'text-ink-muted'}`}>
                                {outcomeLabel}
                            </span>
                            <span className={`mono text-xs ${isBest ? 'text-ink font-medium' : 'text-ink-muted'}`}>
                                {formatPercentage(prob)}
                            </span>
                            {loggable && (
                                <button
                                    onClick={() => onLog(makePick({
                                        match,
                                        market: backendMarket,
                                        outcomeKey,
                                        outcomeLabel: `${label}: ${outcomeLabel}`,
                                        prob,
                                    }))}
                                    title="Log this pick"
                                    className="mono text-[0.6rem] text-ink-muted hover:text-paper hover:bg-ink border border-line hover:border-ink w-5 h-5 leading-none flex items-center justify-center transition-colors cursor-pointer ml-0.5"
                                >
                                    +
                                </button>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function ComboTable({ combos, match, canLog = false, onLog }) {
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
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            {items.map(combo => (
                                <div key={combo.key}
                                    className="flex items-center justify-between py-2 px-3 bg-paper border border-line gap-2"
                                >
                                    <span className="text-xs text-ink-soft truncate min-w-0">{combo.description}</span>
                                    <div className="flex items-center gap-2 shrink-0">
                                        <span className="mono text-xs text-ink font-medium">{formatPercentage(combo.probability)}</span>
                                        <span className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted">@ {combo.odds || '-'}</span>
                                        {canLog && (
                                            <button
                                                onClick={() => onLog(makePick({
                                                    match,
                                                    market: 'compound',
                                                    // Backend lowercases outcome_key on insert; combo
                                                    // keys come in capitalised ('H_btts_yes').
                                                    outcomeKey: combo.key.toLowerCase(),
                                                    outcomeLabel: combo.description,
                                                    prob: combo.probability,
                                                }))}
                                                title="Log this compound bet"
                                                className="mono text-[0.6rem] text-ink-muted hover:text-paper hover:bg-ink border border-line hover:border-ink w-5 h-5 leading-none flex items-center justify-center transition-colors cursor-pointer"
                                            >
                                                +
                                            </button>
                                        )}
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
