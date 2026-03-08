/*
Statistics Page

Displays overall match statistics, goals data, model performance metrics,
and key insights. Fetches data from the statistics API on mount.
*/

import { useEffect, useState } from "react";
import { formatPercentage } from "../utils/constants";
import { useToast } from "../contexts/ToastContext";
import footballAPI from "../services/api";

function Statistics() {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const { addToast } = useToast();

    useEffect(() => {
        loadStatistics();
    }, []);

    // Fetch statistics overview from the API
    const loadStatistics = async () => {
        try {
            setLoading(true);
            const data = await footballAPI.getStatisticsOverview();
            setStats(data);
        } catch (err) {
            addToast('Failed to load statistics', 'error');
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10">
                <div className="text-center text-lg text-gray-400">
                    Loading statistics...
                </div>
            </div>
        );
    }

    return (
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10">
            {/* Page Header */}
            <div className="text-center mb-10">
                <h1 className="text-4xl sm:text-5xl font-bold mb-4 gradient-text">
                    📊 Statistics & Performance
                </h1>
                <p className="text-lg text-gray-400">
                    Insights from {stats.matches.total.toLocaleString()} matches and model performance metrics
                </p>
            </div>

            {/* Key Metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                <MetricCard
                    icon="🏆"
                    value={stats.matches.total.toLocaleString()}
                    label="Total Matches"
                    gradient="from-blue-500 to-blue-600"
                />
                <MetricCard
                    icon="🏠"
                    value={formatPercentage(stats.matches.home_win_rate)}
                    label="Home Win Rate"
                    gradient="from-green-500 to-green-600"
                />
                <MetricCard
                    icon="🤝"
                    value={formatPercentage(stats.matches.draw_rate)}
                    label="Draw Rate"
                    gradient="from-amber-500 to-amber-600"
                />
                <MetricCard
                    icon="✈️"
                    value={formatPercentage(stats.matches.away_win_rate)}
                    label="Away Win Rate"
                    gradient="from-red-500 to-red-600"
                />
            </div>

            {/* Match Outcome */}
            <div className="bg-gray-800 rounded-2xl shadow-xl p-8 mb-8">
                <h2 className="text-2xl font-bold text-gray-100 mb-8">
                    Match Outcome Distribution
                </h2>
                <div className="space-y-6">
                    <OutcomeBar
                        label="🏠 Home Wins"
                        count={stats.matches.home_wins}
                        percentage={stats.matches.home_win_rate}
                        color="bg-green-500"
                    />
                    <OutcomeBar
                        label="🤝 Draws"
                        count={stats.matches.draws}
                        percentage={stats.matches.draw_rate}
                        color="bg-amber-500"
                    />
                    <OutcomeBar
                        label="✈️ Away Wins"
                        count={stats.matches.away_wins}
                        percentage={stats.matches.away_win_rate}
                        color="bg-red-500"
                    />
                </div>
            </div>

            {/* Goals Statistics */}
            <div className="bg-gray-800 rounded-2xl shadow-xl p-8 mb-8">
                <h2 className="text-2xl font-bold text-gray-100 mb-6">
                    ⚽ Goals Statistics
                </h2>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-6">
                    <GoalStat
                        value={stats.goals.avg_home_goals.toFixed(2)}
                        label="Avg Home Goals"
                    />
                    <GoalStat
                        value={stats.goals.avg_away_goals.toFixed(2)}
                        label="Avg Away Goals"
                    />
                    <GoalStat
                        value={stats.goals.avg_total_goals.toFixed(2)}
                        label="Avg Total Goals"
                    />
                    <GoalStat
                        value={stats.goals.total_home_goals.toLocaleString()}
                        label="Total Home Goals"
                    />
                    <GoalStat
                        value={stats.goals.total_away_goals.toLocaleString()}
                        label="Total Away Goals"
                    />
                </div>
            </div>

            {/* Model Performance */}
            <div className="bg-gradient-to-br from-primary-900/50 to-secondary-900/50 rounded-2xl shadow-xl p-8 mb-8">
                <h2 className="text-2xl font-bold text-gray-100 mb-6">
                    🤖 ML Model Performance
                </h2>

                <div className="flex flex-col lg:flex-row gap-8 items-center mb-8">
                    {/* Accuracy Circle */}
                    <div className="flex-shrink-0 w-48 h-48 rounded-full flex items-center justify-center"
                        style={{
                            background: `conic-gradient(#10b981 ${stats.model.accuracy * 360}deg, #374151 0deg)`
                        }}
                    >
                        <div className="w-36 h-36 bg-gray-800 rounded-full flex flex-col items-center justify-center">
                            <div className="text-4xl font-bold text-gray-100">
                                {formatPercentage(stats.model.accuracy)}
                            </div>
                            <div className="text-sm text-gray-400">Accuracy</div>
                        </div>
                    </div>

                    {/* Model Stats */}
                    <div className="flex-1 space-y-4 w-full">
                        <ModelStatItem
                            label="Model Type"
                            value={stats.model.model_type?.toUpperCase() || 'XGBOOST'}
                        />
                        <ModelStatItem
                            label="Total Predictions"
                            value={stats.model.total_predictions.toLocaleString()}
                        />
                        <ModelStatItem
                            label="Correct Predictions"
                            value={stats.model.correct_predictions.toLocaleString()}
                        />
                    </div>
                </div>

                {/* Model Insights */}
                <div className="bg-gray-700 rounded-xl p-6 border-l-4 border-primary-500">
                    <h3 className="text-lg font-bold text-primary-400 mb-4">
                        📈 Model Insights
                    </h3>
                    <ul className="space-y-3 text-sm text-gray-300 leading-relaxed">
                        <li className="flex gap-2">
                            <span className="text-primary-500 flex-shrink-0">•</span>
                            <span>
                                <strong>Baseline Comparison: </strong>
                                Random guessing would achieve ~33% accuracy (3 outcomes).
                                Our model achieves {formatPercentage(stats.model.accuracy)}, which is{' '}
                                {((stats.model.accuracy - 0.33) / 0.33 * 100).toFixed(0)}% better.
                            </span>
                        </li>
                        <li className="flex gap-2">
                            <span className="text-primary-500 flex-shrink-0">•</span>
                            <span>
                                <strong>Home Advantage: </strong>
                                Always predicting home wins would achieve{' '}
                                {formatPercentage(stats.matches.home_win_rate)} accuracy. Our model is{' '}
                                {stats.model.accuracy > stats.matches.home_win_rate ? 'better' : 'competitive'}.
                            </span>
                        </li>
                        <li className="flex gap-2">
                            <span className="text-primary-500 flex-shrink-0">•</span>
                            <span>
                                <strong>Professional Benchmark: </strong>
                                Professional betting models typically achieve 55-65% accuracy.
                                Our model is in the competitive range.
                            </span>
                        </li>
                    </ul>
                </div>
            </div>

            {/* Key Insights */}
            <div className="bg-gray-800 rounded-2xl shadow-xl p-8">
                <h2 className="text-2xl font-bold text-gray-100 mb-6">
                    💡 Key Insights
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <InsightCard
                        icon="🏠"
                        title="Home Advantage"
                        description={`Home teams win ${formatPercentage(stats.matches.home_win_rate)}
                                        of matches, demonstrating a clear home advantage in football.`}
                    />
                    <InsightCard
                        icon="⚽"
                        title="Scoring Patterns"
                        description={`Home teams average ${stats.goals.avg_home_goals.toFixed(2)}
                                        goals per match, while away teams average
                                        ${stats.goals.avg_away_goals.toFixed(2)} goals.`}
                    />
                    <InsightCard
                        icon="🎯"
                        title="Prediction Accuracy"
                        description={`The ML model correctly predicts 
                                        ${formatPercentage(stats.model.accuracy)} 
                                        of matches, significantly better than random chance.`}

                    />
                    <InsightCard
                        icon="📊"
                        title="Match Variety"
                        description={`Draws occur in ${formatPercentage(stats.matches.draw_rate)} 
                                    of matches, showing the competitive nature of football.`}
                    />
                </div>
            </div>
        </div>
    );
}

// Horizontal bar showing outcome count and percentage
function OutcomeBar({ label, count, percentage, color }) {
    return (
        <div className="space-y-2">
            <div className="flex justify-between items-center">
                <span className="text-base font-semibold text-gray-200">{label}</span>
                <span className="text-sm text-gray-400">
                    {count.toLocaleString()} ({formatPercentage(percentage)})
                </span>
            </div>
            <div className="h-10 bg-gray-600 rounded-full overflow-hidden">
                <div
                    className={`h-full ${color} transition-all duration-1000 ease-out`}
                    style={{ width: formatPercentage(percentage) }}
                />
            </div>
        </div>
    );
}

// Card displaying a single key metric with icon and gradient text
function MetricCard({ icon, value, label, gradient }) {
    return (
        <div className="bg-gray-800 rounded-xl p-6 shadow-md hover:shadow-lg transition-shadow duration-300">
            <div className="text-5xl mb-3">{icon}</div>
            <div className={`text-3xl font-bold bg-gradient-to-r ${gradient} bg-clip-text text-transparent mb-2`}>
                {value}
            </div>
            <div className="text-sm text-gray-400">{label}</div>
        </div>
    );
}

// Centered stat box for goals data
function GoalStat({ value, label }) {
    return (
        <div className="text-center p-4 bg-gray-700 rounded-lg">
            <div className="text-3xl font-bold text-primary-400 mb-2">{value}</div>
            <div className="text-sm text-gray-400">{label}</div>
        </div>
    );
}

// Row displaying a model stat label and value
function ModelStatItem({ label, value }) {
    return (
        <div className="flex justify-between items-center p-4 bg-gray-700 rounded-lg">
            <span className="text-sm text-gray-400">{label}</span>
            <span className="text-xl font-bold text-gray-100">{value}</span>
        </div>
    );
}

// Card for key insight with icon, title, and description
function InsightCard({ icon, title, description }) {
    return (
        <div className="p-6 bg-gray-700 rounded-xl text-center">
            <div className="text-5xl mb-3">{icon}</div>
            <h3 className="text-lg font-bold text-gray-100 mb-2">{title}</h3>
            <p className="text-sm text-gray-400 leading-relaxed">{description}</p>
        </div>
    );
}

export default Statistics;