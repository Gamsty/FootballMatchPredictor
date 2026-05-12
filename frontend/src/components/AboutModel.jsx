/*
About Model Modal — Explains the prediction pipeline to portfolio visitors.

Covers: architecture, training data, retraining cadence, data source.
Built so a non-technical visitor can grasp it in 30 seconds.
*/

import { useEffect, useRef } from 'react';

function AboutModel({ onClose }) {
    const panelRef = useRef(null);

    useEffect(() => {
        const handleEsc = (e) => { if (e.key === 'Escape') onClose(); };
        const handleClickOutside = (e) => {
            if (panelRef.current && !panelRef.current.contains(e.target)) onClose();
        };
        document.addEventListener('keydown', handleEsc);
        document.addEventListener('mousedown', handleClickOutside);
        return () => {
            document.removeEventListener('keydown', handleEsc);
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [onClose]);

    return (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div
                ref={panelRef}
                className="bg-gray-900 border border-gray-800 rounded-2xl max-w-lg w-full p-6 shadow-2xl"
            >
                <div className="flex items-start justify-between mb-4">
                    <h2 className="text-lg font-bold text-gray-100">About the model</h2>
                    <button
                        onClick={onClose}
                        className="text-gray-500 hover:text-gray-200 transition-colors text-xl leading-none"
                        aria-label="Close"
                    >
                        &times;
                    </button>
                </div>

                <div className="space-y-4 text-sm text-gray-400 leading-relaxed">
                    <div>
                        <div className="text-[10px] uppercase tracking-wider text-emerald-400 mb-1">
                            Architecture
                        </div>
                        <p>
                            Stacked ensemble — <span className="text-gray-200">XGBoost + RandomForest</span> base
                            learners feeding a logistic-regression meta-learner. Out-of-fold predictions
                            generated via TimeSeriesSplit so the meta-learner never sees future data.
                        </p>
                    </div>

                    <div>
                        <div className="text-[10px] uppercase tracking-wider text-emerald-400 mb-1">
                            Features
                        </div>
                        <p>
                            ~50 features per match: Elo ratings (computed chronologically, no leakage),
                            form over last 5 matches, head-to-head record, league position, rest days,
                            shots/corners/cards averages, season progress.
                        </p>
                    </div>

                    <div>
                        <div className="text-[10px] uppercase tracking-wider text-emerald-400 mb-1">
                            Training data
                        </div>
                        <p>
                            <span className="text-gray-200">40,000+ historical matches</span> across 9
                            European leagues (Premier League, Championship, La Liga, Bundesliga, Serie A,
                            Ligue 1, Eredivisie, Primeira Liga, Champions League). Time-based holdout split
                            so validation reflects future performance.
                        </p>
                    </div>

                    <div>
                        <div className="text-[10px] uppercase tracking-wider text-emerald-400 mb-1">
                            Retraining
                        </div>
                        <p>
                            Azure Container Apps Job runs nightly at 03:00 UTC. New model is promoted only
                            if its AUC beats production (within tolerance) on the holdout window. Failed
                            candidates are archived in Blob Storage for inspection.
                        </p>
                    </div>

                    <div>
                        <div className="text-[10px] uppercase tracking-wider text-emerald-400 mb-1">
                            Data source
                        </div>
                        <p>
                            Fixtures and results from{' '}
                            <a
                                href="https://www.football-data.org/"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-400 hover:text-blue-300 underline"
                            >
                                football-data.org
                            </a>
                            . Synced nightly into PostgreSQL on Azure.
                        </p>
                    </div>
                </div>

                <div className="mt-5 pt-4 border-t border-gray-800 text-xs text-gray-500 text-center">
                    Predictions are informational only. Match outcomes are inherently uncertain.
                </div>
            </div>
        </div>
    );
}

export default AboutModel;
