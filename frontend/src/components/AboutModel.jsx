/*
About Model — paper-aesthetic modal

Five-section explainer for portfolio visitors who want to know how the predictions
work. Editorial framing rather than tooltip-style help text.
*/

import { useEffect, useRef } from 'react';

function Section({ label, children }) {
    return (
        <div>
            <div className="mono text-[0.62rem] uppercase tracking-[0.15em] text-accent mb-2">
                {label}
            </div>
            <p className="text-ink-soft leading-relaxed">{children}</p>
        </div>
    );
}

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
        <div className="fixed inset-0 bg-ink/40 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
            <div
                ref={panelRef}
                className="bg-paper border border-line max-w-lg w-full shadow-[0_24px_60px_-20px_rgba(21,17,13,0.35)]
                           animate-slide-in"
            >
                <div className="flex items-baseline justify-between p-6 border-b border-line">
                    <div>
                        <div className="mono text-[0.62rem] uppercase tracking-[0.15em] text-ink-muted">
                            Method note
                        </div>
                        <h2 className="display text-2xl text-ink mt-1">
                            How it works<span className="text-accent">.</span>
                        </h2>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-ink-muted hover:text-accent transition-colors text-2xl leading-none cursor-pointer"
                        aria-label="Close"
                    >
                        ×
                    </button>
                </div>

                <div className="p-6 space-y-5 text-sm">
                    <Section label="Architecture">
                        Stacked ensemble — <span className="text-ink font-medium">XGBoost + RandomForest</span> base
                        learners feeding a logistic-regression meta-learner. Out-of-fold predictions
                        generated via TimeSeriesSplit so the meta-learner never sees future data.
                    </Section>

                    <Section label="Features">
                        ~50 features per match: Elo ratings (computed chronologically, no leakage),
                        form over last 5 matches, head-to-head record, league position, rest days,
                        shots/corners/cards averages, season progress.
                    </Section>

                    <Section label="Training data">
                        <span className="text-ink font-medium">40,000+ historical matches</span> across 9
                        European leagues. Time-based holdout split so validation reflects future
                        performance rather than random sampling.
                    </Section>

                    <Section label="Retraining">
                        Azure Container Apps Job runs nightly at 03:00 UTC. New model is promoted only
                        if its AUC beats production (within tolerance) on the holdout window. Failed
                        candidates are archived in Blob Storage for inspection.
                    </Section>

                    <Section label="Data source">
                        Fixtures and results from{' '}
                        <a
                            href="https://www.football-data.org/"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-accent hover:text-accent-soft border-b border-accent/40 transition-colors"
                        >
                            football-data.org
                        </a>
                        . Synced nightly into PostgreSQL on Azure.
                    </Section>
                </div>

                <div className="px-6 py-4 border-t border-line bg-paper-tint">
                    <p className="mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-muted text-center">
                        Predictions are informational. Match outcomes are inherently uncertain.
                    </p>
                </div>
            </div>
        </div>
    );
}

export default AboutModel;
