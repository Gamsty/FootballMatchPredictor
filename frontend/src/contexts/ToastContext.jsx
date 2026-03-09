/*
Toast Notification Context

Provides a global toast system via React Context.
Any component can show temporary messages by calling addToast().

Usage:
  const { addToast } = useToast();
  addToast('Prediction saved!', 'success');
  addToast('Failed to load teams', 'error');
  addToast('Loading data...', 'info');
*/

import { createContext, useContext, useState, useCallback } from 'react';

const ToastContext = createContext();

// Auto-dismiss delay in milliseconds
const TOAST_DURATION = 4000;

// Counter for unique toast IDs (persists across renders)
let toastId = 0;

export function ToastProvider({ children }) {
    const [toasts, setToasts] = useState([]);

    // Add a new toast and schedule its removal
    const addToast = useCallback((message, type = 'info') => {
        const id = ++toastId;

        setToasts((prev) => [...prev, { id, message, type }]);

        // Auto-remove after TOAST_DURATION
        setTimeout(() => {
            setToasts((prev) => prev.filter((t) => t.id !== id));
        }, TOAST_DURATION);
    }, []);

    // Manually dismiss a toast (close button)
    const removeToast = useCallback((id) => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
    }, []);

    return (
        <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
            {children}
        </ToastContext.Provider>
    );
}

// Hook for components to access the toast system
export function useToast() {
    const context = useContext(ToastContext);
    if (!context) {
        throw new Error('useToast must be used within a ToastProvider');
    }
    return context;
}
