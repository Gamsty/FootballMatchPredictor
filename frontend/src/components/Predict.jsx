/*
Predict Page

Main prediction page where the user selects a home and away team,
submits a prediction request, and views the result with probabilities
and ML features used.
*/

import { useState } from "react";
import TeamSelector from "./TeamSelector";
import PredictionResult from "./PredictionResult";
import FeatureDisplay from "./FeatureDisplay";
import footballAPI from "../services/api";
import { useToast } from "../contexts/ToastContext";

function Predict() {
    const [homeTeamId, setHomeTeamId] = useState('');
    const [awayTeamId, setAwayTeamId] = useState('');
    const [prediction, setPrediction] = useState(null);
    const [loading, setLoading] = useState(false);
    const { addToast } = useToast();

    // Validate selections and call the prediction API
    const handlePredict = async () => {
        if (!homeTeamId || !awayTeamId) {
            addToast('Please select both teams', 'error');
            return;
        }

        if (homeTeamId === awayTeamId) {
            addToast('Please select different teams', 'error');
            return;
        }

        setLoading(true);
        setPrediction(null);

        try {
            const data = await footballAPI.predictMatch(homeTeamId, awayTeamId);
            setPrediction(data);
        } catch (err) {
            addToast('Failed to get prediction. Please try again', 'error');
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    // Clear all selections and prediction data
    const handleReset = () => {
        setHomeTeamId('');
        setAwayTeamId('');
        setPrediction(null);
    };

    return (
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
            {/* Page Header */}
            <div className="text-center mb-10">
                <h1 className="text-4xl sm:text-5xl font-bold mb-4 gradient-text">
                    ⚽ Football Match Predictor
                </h1>
                <p className="text-lg text-gray-400 max-w-2xl mx-auto">
                    Select two teams to predict the match outcome using machine learning
                </p>
            </div>

            {/* Predict Card */}
            <div className="bg-gray-800 rounded-2xl shadow-xl sm:p-8 mb-8">
                <TeamSelector
                    label="Home Team"
                    value={homeTeamId}
                    onChange={setHomeTeamId}
                    excludeId={awayTeamId}
                    icon="🏠"
                />

                <div className="flex items-center justify-center py-3">
                    <div className="text-xl font-bold text-primary-400 px-6 py-3
                                    bg-gradient-to-r from-primary-900 to-secondary-900 rounded-lg">
                        VS
                    </div>
                </div>

                <TeamSelector
                    label="Away Team"
                    value={awayTeamId}
                    onChange={setAwayTeamId}
                    excludeId={homeTeamId}
                    icon="✈️"
                />

                {/* Actions Buttons */}
                <div className="flex flex-col sm:flex-row gap-4">
                    <button
                        onClick={handlePredict}
                        disabled={loading || !homeTeamId || !awayTeamId}
                        className="flex-1 px-6 py-3 text-base font-semibold text-white bg-gradient-to-r
                                    from-primary-500 to-secondary-500 rounded-lg shadow-md hover:shadow-lg
                                    disabled:opacity-50 disabled:cursor-not-allowed transition-all
                                    duration-300 transform hover:scale-105 disabled:hover:scale-100
                                    flex items-center justify-center gap-2"
                    >
                        {loading ? (
                            <>
                                <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                <span>Predicting...</span>
                            </>
                        ) : (
                            <>
                            <span>🔮</span>
                            <span>Predict Match</span>
                            </>
                        )}
                    </button>

                    {prediction && (
                        <button 
                            onClick={handleReset}
                            className="px-6 py-3 text-base font-semibold text-gray-200 bg-gray-700
                                        rounded-lg hover:bg-gray-600 transition-all duration-300"
                        >
                            ↻ Reset
                        </button>
                    )}
                </div>
            </div>

            {/* Prediction Result */}
            {prediction && (
                <>
                <PredictionResult
                    prediction={prediction.prediction}
                    homeTeam={prediction.home_team}
                    awayTeam={prediction.away_team}
                />
                <FeatureDisplay
                    prediction={prediction}
                />
                </>
            )}
        </div>
    );
}

export default Predict;