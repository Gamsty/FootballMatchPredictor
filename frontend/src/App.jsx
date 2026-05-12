/*
Main App Component

Root component that sets up:
    - ToastProvider for global toast notifications
    - React Router for client-side navigation
    - Sticky navbar with active link highlighting
    - Page route (Dashboard)
    - Footer with disclaimer
*/

// Context and notifications
import { ToastProvider } from './contexts/ToastContext';
import ToastContainer from './components/ToastContainer';

// Routing
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';

// Pages
import Dashboard from './pages/Dashboard';
import ErrorBoundary from './components/ErrorBoundary';

function App() {
    return (
        <ErrorBoundary>
        <ToastProvider>
            <Router>
                <div className="min-h-screen flex flex-col bg-gradient-to-br from-gray-900 to-gray-950">
                    {/* Header */}
                    <header className="bg-gray-950 border-b border-gray-800 sticky top-0 z-50 shadow-lg shadow-black/30">
                        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                            <div className="flex items-center justify-between h-14">
                                <div className="flex items-center">
                                    <span className="text-2xl mr-2.5">⚽</span>
                                    <span className="text-lg font-bold text-gray-100">Football Predictor</span>
                                    <span className="hidden sm:inline ml-3 text-xs text-gray-500">
                                        ML predictions across 9 leagues
                                    </span>
                                </div>
                                <div className="hidden sm:flex items-center gap-2 text-xs text-gray-500">
                                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/80 animate-pulse" />
                                    <span>Live</span>
                                </div>
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
                    <footer className="bg-gray-900/60 border-t border-gray-800 mt-12">
                        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 grid gap-3 sm:grid-cols-2 sm:items-center">
                            <div className="text-center sm:text-left">
                                <p className="text-gray-400 text-sm">
                                    &copy; {new Date().getFullYear()} Football Match Predictor
                                </p>
                                <p className="text-xs text-gray-500 mt-1">
                                    Predictions are informational. Match outcomes are inherently uncertain.
                                </p>
                            </div>
                            <div className="flex items-center justify-center sm:justify-end gap-4 text-xs text-gray-500">
                                <span>Stacked ensemble · 40k+ matches</span>
                                <span className="text-gray-700">·</span>
                                <a
                                    href="https://github.com/Gamsty/FootballMatchPredictor"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="hover:text-gray-300 transition-colors flex items-center gap-1"
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

            {/* Toast notifications — renders in bottom-right corner */}
            <ToastContainer />
        </ToastProvider>
        </ErrorBoundary>
    );
}

export default App;
