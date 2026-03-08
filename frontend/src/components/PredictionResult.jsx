/*
Prediction Result Component

Displays the ML model's match prediction including:
    - Predicted outcome (Home Win / Draw / Away Win) with icon
    - Confidence badge (High / Medium / Low) with color coding
    - Probability bar charts for each outcome
    - Disclaimer notice
*/

import { OUTCOME_LABELS, formatPercentage, getConfidenceColor, getConfidenceLabel } from "../utils/constants";

function PredictionResult({ prediction, homeTeam, awayTeam }) {
    if (!prediction) return null;

    const { outcome, probabilities, confidence } = prediction;
    const { home_win, draw, away_win } = probabilities;

    // Returns an emoji icon matching the predicted outcome
    const getOutcomeIcon = (outcomeType) => {
        switch (outcomeType) {
            case 'HOME_WIN':
                return '🏠';
            case 'DRAW':
                return '🤝';
            case 'AWAY_WIN':
                return '✈️';
            default:
                return '⚽';
        }
    };

    return (
        <div className="bg-gray-800 rounded-2xl p-8 shadow-xl animate-slide-in">
            {/* Header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
                <h2 className="text-2xl font-bold text-gray-100">
                    Prediction Result
                </h2>
                <div className={`${getConfidenceColor(confidence)} text-white px-4 py-2 rounded-full text-sm font-semibold`}>
                    {getConfidenceLabel(confidence)} Confidence ({formatPercentage(confidence)})
                </div>
            </div>

            {/* Match Info */}
            <div className="flex flex-col sm:flex-row justify-between items-center gap-4 mb-8 p-6
                            bg-gradient-to-r from-primary-500 to-secondary-500 rounded-xl text-white">
                <div className="text-lg font-semibold flex-1 text-center sm:text-right">
                    {homeTeam?.name}
                </div>
                <div className="text-sm font-bold px-4 py-2 bg-white/20 rounded-lg">
                    VS
                </div>
                <div className="text-lg font-semibold flex-1 text-center sm:text-left">
                    {awayTeam?.name}
                </div>
            </div>

            {/* Predicted Outcome */}
            <div className="text-center mb-8 p-6 bg-gray-700 rounded-xl">
                <div className="text-6xl mb-3">
                    {getOutcomeIcon(outcome)}
                </div>
                <div className="text-3xl font-bold text-gray-100">
                    {OUTCOME_LABELS[outcome]}
                </div>
            </div>

            {/* Probability bar charts — one per outcome */}
            <div className="space-y-5">
                {/* Home Win */}
                <div className="space-y-2">
                    <div className="flex justify-between items-center">
                        <span className="text-sm font-semibold text-gray-400 flex items-center gap-2">
                            <span>🏠</span>
                            <span>Home Win</span>
                        </span>
                        <span className="text-base font-bold text-gray-100">
                            {formatPercentage(home_win)}
                        </span>
                    </div>
                    <div className="h-8 bg-gray-600 rounded-full overflow-hidden">
                        <div className="h-full bg-gradient-to-r from-green-400 to-green-600 
                                        transition-all duration-700 ease-out flex items-center
                                        justify-end pr-3 text-white text-xs font-semibold"
                            style={{ width: formatPercentage(home_win) }}
                        />
                    </div>
                </div>

                {/* Draw */}
                <div className="space-y-2">
                    <div className="flex justify-between items-center">
                        <span className="text-sm font-semibold text-gray-400 flex items-center gap-2">
                            <span>🤝</span>
                            <span>Draw</span>
                        </span>
                        <span className="text-base font-bold text-gray-100">
                            {formatPercentage(draw)}
                        </span>
                    </div>
                    <div className="h-8 bg-gray-600 rounded-full overflow-hidden">
                        <div className="h-full bg-gradient-to-r from-amber-400 to-amber-600 
                                        transition-all duration-700 ease-out flex items-center
                                        justify-end pr-3 text-white text-xs font-semibold"
                            style={{ width: formatPercentage(draw) }}
                        />
                    </div>
                </div>

                {/* Away Win */}
                <div className="space-y-2">
                    <div className="flex justify-between items-center">
                        <span className="text-sm font-semibold text-gray-400 flex items-center gap-2">
                            <span>✈️</span>
                            <span>Away Win</span>
                        </span>
                        <span className="text-base font-bold text-gray-100">
                            {formatPercentage(away_win)}
                        </span>
                    </div>
                    <div className="h-8 bg-gray-600 rounded-full overflow-hidden">
                        <div className="h-full bg-gradient-to-r from-red-400 to-red-600 
                                        transition-all duration-700 ease-out flex items-center
                                        justify-end pr-3 text-white text-xs font-semibold"
                            style={{ width: formatPercentage(away_win) }}
                        />
                    </div>
                </div>
            </div>

            {/* Disclaimer */}
            <div className="mt-6 p-4 bg-amber-900/20 border-l-4 border-amber-500 rounded">
                <p className="text-sm text-amber-300 leading-relaxed">
                    ⚠️ This prediction is for information purposes only. Football matches are unpredictable.
                </p>
            </div>
        </div>
    );
}

export default PredictionResult;