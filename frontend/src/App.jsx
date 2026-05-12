/*
App Shell

Root layout: paper background, editorial header with monogram + live indicator,
sticky on scroll. Footer mirrors the portfolio (adrianklogamst.no) so both reads
as the same designer's work.
*/

import { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import ErrorBoundary from './components/ErrorBoundary';

function App() {
    const [scrolled, setScrolled] = useState(false);

    useEffect(() => {
        const onScroll = () => setScrolled(window.scrollY > 12);
        window.addEventListener('scroll', onScroll, { passive: true });
        return () => window.removeEventListener('scroll', onScroll);
    }, []);

    return (
        <ErrorBoundary>
            <Router>
                <div className="min-h-screen flex flex-col text-ink">
                    {/* Header */}
                    <header
                        className={
                            "sticky top-0 z-50 transition-all duration-300 " +
                            (scrolled
                                ? "backdrop-blur-md bg-paper/80 border-b border-line"
                                : "bg-transparent")
                        }
                    >
                        <div className="max-w-6xl mx-auto px-6 lg:px-10 py-5 flex items-center justify-between">
                            <a href="/" className="display text-xl font-semibold text-ink no-underline tracking-tight">
                                A.K.G.
                                <span className="mono text-[0.65rem] text-ink-muted uppercase tracking-[0.15em] ml-3 align-middle">
                                    / Predictor
                                </span>
                            </a>
                            <div className="hidden sm:flex items-center gap-2 mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                                <span className="w-1.5 h-1.5 rounded-full bg-positive animate-pulse-soft" />
                                <span>Live</span>
                            </div>
                        </div>
                    </header>

                    {/* Main Content */}
                    <main className="flex-1">
                        <Routes>
                            <Route path="/" element={<Dashboard />} />
                        </Routes>
                    </main>

                    {/* Footer */}
                    <footer className="border-t border-line mt-20">
                        <div className="max-w-6xl mx-auto px-6 lg:px-10 py-8 grid gap-3 sm:grid-cols-2 sm:items-end">
                            <div>
                                <div className="display text-sm text-ink">
                                    Football Match Predictor.
                                </div>
                                <div className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted mt-1">
                                    © {new Date().getFullYear()} · Built by Adrian Klo Gamst
                                </div>
                                <p className="text-xs text-ink-muted mt-3 max-w-md leading-relaxed">
                                    Predictions are informational. Football outcomes are inherently uncertain — bet only what you can afford to lose, or simply don't bet at all.
                                </p>
                            </div>
                            <div className="flex sm:justify-end items-center gap-5 mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                                <span>Stacked ensemble · 40k+ matches</span>
                                <a
                                    href="https://github.com/Gamsty/FootballMatchPredictor"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="hover:text-accent transition-colors flex items-center gap-1.5"
                                >
                                    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.44 9.8 8.21 11.39.6.11.82-.26.82-.58v-2.17c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.74.08-.72.08-.72 1.21.08 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.31-.54-1.52.11-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 016 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.25 2.87.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.63-5.49 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.69.83.58A12 12 0 0024 12c0-6.63-5.37-12-12-12z" />
                                    </svg>
                                    Source
                                </a>
                            </div>
                        </div>
                    </footer>
                </div>
            </Router>
        </ErrorBoundary>
    );
}

export default App;
