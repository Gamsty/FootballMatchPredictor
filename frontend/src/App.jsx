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
                            <div className="flex items-center h-14">
                                <span className="text-2xl mr-2.5">⚽</span>
                                <span className="text-lg font-bold text-gray-100">Football Predictor</span>
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
                    <footer className="bg-gray-800 border-t border-gray-700 mt-12">
                        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 text-center">
                            <p className="text-gray-400 mb-2">
                                &copy; 2025 Football Match Predictor | Built with Machine Learning
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
        </ErrorBoundary>
    );
}

export default App;
