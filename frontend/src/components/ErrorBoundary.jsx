/**
 * Error Boundary Component
 *
 * Class component that catches JavaScript errors in its child component tree
 * and displays a fallback UI instead of crashing the entire app.
 * Uses React's getDerivedStateFromError and componentDidCatch lifecycle methods.
 */

import React from 'react';

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    // Update state so the next render shows the fallback UI
    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }

    // Log error details for debugging
    componentDidCatch(error, errorInfo) {
        console.error('Error caught by boundary:', error, errorInfo);
    }

    render() {
        // Show fallback UI when an error is caught
        if (this.state.hasError) {
            return (
                <div className="flex items-center justify-center min-h-screen bg-gray-900">
                    <div className="text-center p-8 bg-gray-800 rounded-2xl shadow-lg max-w-md mx-4">
                        <h1 className="text-2xl font-bold text-gray-100 mb-3">
                            Oops! Something went wrong
                        </h1>
                        <p className="text-gray-400 mb-6">
                            We're sorry for the inconvenience. Please try refreshing the page.
                        </p>
                        <button
                            onClick={() => window.location.reload()}
                            className="px-6 py-2.5 bg-primary-600 text-white font-semibold rounded-lg
                                       hover:bg-primary-700 transition-colors cursor-pointer"
                        >
                            Refresh Page
                        </button>
                    </div>
                </div>
            );
        }

        // No error — render children normally
        return this.props.children;
    }
}

export default ErrorBoundary;
