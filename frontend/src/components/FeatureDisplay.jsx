/*
FeatureDisplay Component

Shows the ML features used to make a prediction, grouped into three categories:
    - Home Team stats (form, goals, win rate)
    - Away Team stats (form, goals, win rate)
    - Head-to-Head record (wins, draws)
Also displays model type and prediction timestamp.
*/

function FeatureDisplay({ prediction }) {
    if (!prediction || !prediction.features_used) return null;

    const features = prediction.features_used;

    return (
        <div className="bg-gray-800 rounded-2xl p-8 shadow-xl mt-8">
            <h3 className="text-2xl font-bold text-gray-100 mb-2 flex items-center gap-2">
                <span>📊</span>
                <span>Features Used in Prediction</span>
            </h3>
            <p className="text-gray-400 mb-6">
                These statistics were analyzed by the ML model to make the prediction
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {/* Home Team Features */}
                <div>
                    <h4 className="text-lg font-bold text-primary-600 mb-4 flex items-center gap-2">
                        <span>🏠</span>
                        <span>Home Team</span>
                    </h4>
                    <div className="space-y-3">
                        <FeatureItem
                            label="Recent Form (5 games)"
                            value={`${features.home_form_5.toFixed(1)} pts`}
                        />
                        <FeatureItem 
                            label="Avg Goals Scored"
                            value={features.home_goals_scored_avg.toFixed(2)}
                        />
                        <FeatureItem 
                            label="Avg Goals Conceded"
                            value={features.home_goals_conceded_avg.toFixed(2)}
                        />
                        <FeatureItem 
                            label="Win Rate"
                            value={`${(features.home_win_rate * 100).toFixed(1)}%`}
                        />
                    </div>
                </div>

                {/* Away Team Features */}
                <div>
                    <h4 className="text-lg font-bold text-secondary-600 mb-4 flex items-center gap-2">
                        <span>✈️</span>
                        <span>Away Team</span>
                    </h4>
                    <div className="space-y-3">
                        <FeatureItem
                            label="Recent Form (5 games)"
                            value={`${features.away_form_5.toFixed(1)} pts`}
                        />
                        <FeatureItem 
                            label="Avg Goals Scored"
                            value={features.away_goals_scored_avg.toFixed(2)}
                        />
                        <FeatureItem 
                            label="Avg Goals Conceded"
                            value={features.away_goals_conceded_avg.toFixed(2)}
                        />
                        <FeatureItem 
                            label="Win Rate"
                            value={`${(features.away_win_rate * 100).toFixed(1)}%`}
                        />
                    </div>
                </div>

                {/* Head-to-head Features */}
                <div>
                    <h4 className="text-lg font-bold text-green-600 mb-4 flex items-center gap-2">
                        <span>🤝</span>
                        <span>Head-to-Head</span>
                    </h4>
                    <div className="space-y-3">
                        <FeatureItem
                            label="Home Wins"
                            value={features.h2h_home_wins}
                        />
                        <FeatureItem 
                            label="Draws"
                            value={features.h2h_draws}
                        />
                        <FeatureItem 
                            label="Away Wins"
                            value={features.h2h_away_wins}
                        />
                    </div>
                </div>
            </div>

            {/* Model Info */}
            <div className="mt-6 pt-6 border-t border-gray-600 flex flex-col sm:flex-row gap-4 text-sm text-gray-400">
                <p>
                    <strong className="text-gray-200">
                        Model:
                    </strong>
                    {prediction.model_type.toUpperCase()}
                </p>
                <p>
                    <strong className="text-gray-200">
                        Prediction Time:
                    </strong>
                    {' '}{new Date(prediction.timestamp).toLocaleString()}
                </p>
            </div>
        </div>
    );
}

// Reusable row displaying a single feature label and its value
function FeatureItem({ label, value }) {
    return (
        <div className="flex justify-between items-center p-3 bg-gray-700 rounded-lg">
            <span className="text-sm text-gray-400">{label}</span>
            <span className="text-base font-bold text-gray-100">{value}</span>
        </div>
    );
}

export default FeatureDisplay;