/*
Main App Component

Root component that sets up:
    - ToastProvider for global toast notifications
    - React Router for client-side navigation
    - Sticky navbar with active link highlighting
    - Page routes (Predict and Statistics)
    - Footer with disclaimer
*/

// Context and notifications
import { ToastProvider } from './contexts/ToastContext';
import ToastContainer from './components/ToastContainer';

// Routing
import { BrowserRouter as Router, Routes, Route, Link, NavLink } from 'react-router-dom';

// Pages
import Predict from './components/Predict';
import Statistics from './pages/Statistics';

function App() {
    return (
        <ToastProvider>
            <Router>
                <div className="min-h-screen flex flex-col bg-gradient-to-br from-gray-900 to-gray-950">
                    {/* Navigation */}
                    <nav className="bg-gradient-to-r from-primary-500 to-secondary-500 shadow-lg sticky top-0 z-50">
                        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                            <div className="flex justify-between items-center h-16">
                                <Link to="/" className="flex items-center gap-3 text-white hover:scale-105 transition-transform duration-300">
                                    <span className="text-3xl">⚽</span>
                                    <span className="text-xl font-bold">Football Predictor</span>
                                </Link>

                                {/* Nav links — highlights active page */}
                                <div className="flex gap-2">
                                    <NavLink
                                        to="/"
                                        className={({ isActive }) =>
                                            `px-4 py-2 rounded-lg font-medium transition-all duration-300 ${
                                                isActive
                                                    ? 'bg-white/30 text-white'
                                                    : 'text-white/90 hover:bg-white/20'
                                            }`
                                        }
                                    >
                                        🔮 Predict
                                    </NavLink>
                                    <NavLink
                                        to="/statistics"
                                        className={({ isActive }) =>
                                            `px-4 py-2 rounded-lg font-medium transition-all duration-300 ${
                                                isActive
                                                    ? 'bg-white/30 text-white'
                                                    : 'text-white/90 hover:bg-white/20'
                                            }`
                                        }
                                    >
                                        📊 Statistics
                                    </NavLink>
                                </div>
                            </div>
                        </div>
                    </nav>

                    {/* Main Content */}
                    <main className="flex-1">
                        <Routes>
                            <Route path="/" element={<Predict />} />
                            <Route path="/statistics" element={<Statistics />} />
                        </Routes>
                    </main>

                    {/* Footer */}
                    <footer className="bg-gray-800 border-t border-gray-700 mt-12">
                        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 text-center">
                            <p className="text-gray-400 mb-2">
                                © 2024 Football Match Predictor | Built with Machine Learning
                            </p>
                            <p className="text-sm text-gray-500">
                                Predictions are for informational purposes only. Match outcomes are inherently unpredictable.
                            </p>
                        </div>
                    </footer>
                </div>
            </Router>

            {/* Toast notifications — renders in bottom-right corner */}
            <ToastContainer />
        </ToastProvider>
    );
}

export default App;
