/*
Match Card Component

Compact card showing a single match prediction for the dashboard grid.

Layout (top to bottom):
    1. Date/time + confidence badge (High/Medium/Low)
    2. Team names with club crests + H/A win probabilities
    3. Probability bar (green=home, gray=draw, blue=away)
    4. Recommended bets (Best Bet + Safest Bet) with expandable "Why this bet?" reasons
    5. Accumulator buttons — add a specific bet to the accumulator

Props:
    - match: match object from API with prediction, markets, match_stats
    - onClick: opens MatchDetail modal
    - isSelected: whether this match is in the accumulator
    - onToggleAccumulator: callback to add/remove from accumulator
*/

import { useState } from 'react';
import {
    formatTime, formatMatchDate, formatPercentage, getConfidenceColor, getConfidenceLabel,
    getRecommendedBets, getRecommendedBet, formatOdds, isToday
} from '../utils/constants';

const TYPE_LABELS = { best: 'Best Bet', safest: 'Safest Bet' };
const TYPE_COLORS = {
    best:    { bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'text-blue-300/70', text: 'text-blue-200', prob: 'text-blue-300' },
    safest:  { bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', label: 'text-emerald-300/70', text: 'text-emerald-200', prob: 'text-emerald-300' },
};

function MatchCard({ match, onClick, isSelected, onToggleAccumulator }) {
    const [expandedReason, setExpandedReason] = useState(null);
    const prediction = match.prediction;
    const hasPrediction = prediction && prediction.probabilities;
    const recommendedBets = getRecommendedBets(match);
    const recommended = recommendedBets ? recommendedBets[0] : null;

    return (
        <div
            onClick={() => onClick(match)}
            className="bg-gray-800/80 rounded-xl p-4 shadow-md hover:shadow-lg
                       cursor-pointer transition-all duration-200 hover:scale-[1.01]
                       border border-gray-700/40 hover:border-blue-500/40"
        >
            {/* Header: Date/Time */}
            <div className="flex justify-between items-center mb-2.5">
                <span className="text-xs text-gray-500">
                    {isToday(match.date) ? formatTime(match.date) : `${formatMatchDate(match.date)} ${formatTime(match.date)}`}
                </span>
                {hasPrediction && (
                    <span className={`${getConfidenceColor(prediction.confidence)} text-white text-[10px] font-semibold px-2 py-0.5 rounded-full`}>
                        {getConfidenceLabel(prediction.confidence)}
                    </span>
                )}
            </div>

            {/* Teams + Probabilities */}
            <div className="mb-2.5">
                <div className="flex justify-between items-center mb-1.5">
                    <div className="flex items-center gap-2 truncate max-w-[70%]">
                        {match.home_team.crest && (
                            <img src={match.home_team.crest} alt="" className="w-5 h-5 object-contain shrink-0" />
                        )}
                        <span className="text-sm font-semibold text-gray-100 truncate">
                            {match.home_team.short_name || match.home_team.name}
                        </span>
                    </div>
                    {hasPrediction && (
                        <span className="text-xs font-bold text-emerald-400 font-mono">
                            {formatPercentage(prediction.probabilities.home_win)}
                        </span>
                    )}
                </div>
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-2 truncate max-w-[70%]">
                        {match.away_team.crest && (
                            <img src={match.away_team.crest} alt="" className="w-5 h-5 object-contain shrink-0" />
                        )}
                        <span className="text-sm font-semibold text-gray-100 truncate">
                            {match.away_team.short_name || match.away_team.name}
                        </span>
                    </div>
                    {hasPrediction && (
                        <span className="text-xs font-bold text-sky-400 font-mono">
                            {formatPercentage(prediction.probabilities.away_win)}
                        </span>
                    )}
                </div>
            </div>

            {/* Probability Bar */}
            {hasPrediction && (
                <div className="mb-3">
                    <div className="flex h-1.5 rounded-full overflow-hidden bg-gray-700/60">
                        <div className="bg-emerald-500 transition-all duration-500"
                            style={{ width: `${prediction.probabilities.home_win * 100}%` }} />
                        <div className="bg-gray-400 transition-all duration-500"
                            style={{ width: `${prediction.probabilities.draw * 100}%` }} />
                        <div className="bg-sky-500 transition-all duration-500"
                            style={{ width: `${prediction.probabilities.away_win * 100}%` }} />
                    </div>
                    <div className="flex justify-between mt-0.5">
                        <span className="text-[9px] text-emerald-400/70">H</span>
                        <span className="text-[9px] text-gray-500">
                            D {formatPercentage(prediction.probabilities.draw)}
                        </span>
                        <span className="text-[9px] text-sky-400/70">A</span>
                    </div>
                </div>
            )}

            {/* Recommended Bets (up to 3) */}
            {recommendedBets && recommendedBets.length > 0 && (
                <div className="space-y-1 mb-2.5">
                    {recommendedBets.map((bet) => {
                        const c = TYPE_COLORS[bet.type] || TYPE_COLORS.best;
                        const isExpanded = expandedReason === bet.type;
                        return (
                            <div key={bet.type}>
                                <div className={`${c.bg} border ${c.border} rounded-lg px-3 py-1.5`}>
                                    <div className="flex items-center justify-between">
                                        <span className={`text-[10px] ${c.label} uppercase font-medium`}>{TYPE_LABELS[bet.type]}</span>
                                        <span className="text-[10px] text-gray-500">@ {formatOdds(bet.prob)}</span>
                                    </div>
                                    <div className="flex items-center justify-between mt-0.5">
                                        <span className={`text-xs font-semibold ${c.text}`}>{bet.label}</span>
                                        <span className={`text-xs font-bold ${c.prob} font-mono`}>{formatPercentage(bet.prob)}</span>
                                    </div>
                                    {bet.reason && (
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setExpandedReason(isExpanded ? null : bet.type);
                                            }}
                                            className={`text-[10px] mt-1 font-medium transition-colors ${c.label} hover:underline`}
                                        >
                                            {isExpanded ? 'Hide reason' : 'Why this bet?'}
                                        </button>
                                    )}
                                </div>
                                {isExpanded && bet.reason && (
                                    <div className={`${c.bg} border ${c.border} border-t-0 rounded-b-lg px-3 py-2.5 -mt-0.5`}>
                                        <ul className="space-y-1.5">
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

            {/* Accumulator — pick which bet to add */}
            {recommendedBets && onToggleAccumulator && (
                isSelected ? (
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onToggleAccumulator(match, recommended);
                        }}
                        className="w-full text-[10px] font-medium py-1.5 rounded transition-all duration-200
                            bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                    >
                        ✓ In Accumulator — Click to Remove
                    </button>
                ) : (
                    <div className="grid grid-cols-2 gap-1" onClick={(e) => e.stopPropagation()}>
                        {recommendedBets.map((bet) => {
                            const c = TYPE_COLORS[bet.type] || TYPE_COLORS.best;
                            return (
                                <button
                                    key={bet.type}
                                    onClick={() => onToggleAccumulator(match, bet)}
                                    className={`text-[9px] font-medium py-1 px-1 rounded border transition-all duration-200
                                        bg-gray-700/30 text-gray-500 border-gray-700/30 hover:${c.text} hover:${c.border} hover:bg-gray-700/50`}
                                    title={`Add "${bet.label}" to accumulator`}
                                >
                                    + {TYPE_LABELS[bet.type]?.replace(' Bet', '')}
                                </button>
                            );
                        })}
                    </div>
                )
            )}
        </div>
    );
}

export default MatchCard;
