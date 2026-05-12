/*
Error Boundary — paper-aesthetic fallback

Catches JavaScript errors in its child component tree and renders a calm,
editorial fallback rather than letting the app blank-screen. Uses the same
paper palette as the rest of the dashboard so errors don't look like a
different application appeared.
*/

import React from 'react';

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }

    componentDidCatch(error, errorInfo) {
        // Log to the browser console for local debugging; in production this
        // is also surfaced via Application Insights' browser SDK if wired up.
        console.error('Error caught by boundary:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex items-center justify-center min-h-screen bg-paper px-6">
                    <div className="max-w-md w-full text-center">
                        <div className="mono text-[0.7rem] uppercase tracking-[0.15em] text-accent mb-4">
                            Unexpected error
                        </div>
                        <h1 className="display text-4xl text-ink mb-4">
                            Something broke<span className="text-accent">.</span>
                        </h1>
                        <p className="text-ink-soft mb-8 leading-relaxed">
                            The dashboard hit an error it didn't know how to handle. A
                            refresh usually clears it.
                        </p>
                        <button
                            onClick={() => window.location.reload()}
                            className="mono text-[0.72rem] uppercase tracking-[0.12em] px-5 py-3
                                       bg-ink text-paper hover:bg-accent transition-colors cursor-pointer"
                        >
                            Refresh page →
                        </button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
