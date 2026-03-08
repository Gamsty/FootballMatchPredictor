/*
Toast Container

Renders active toast notifications as a fixed overlay in the bottom-right corner.
Reads toasts from ToastContext and displays them stacked vertically.
Each toast auto-dismisses after 4 seconds or can be closed manually.
*/

import { useToast } from '../contexts/ToastContext';

// Tailwind classes for each toast type
const TOAST_STYLES = {
    success: 'bg-green-600 text-white',
    error: 'bg-red-600 text-white',
    info: 'bg-blue-600 text-white',
};

// Icon per toast type (simple unicode)
const TOAST_ICONS = {
    success: '\u2713',
    error: '\u2717',
    info: '\u24D8',
};

export default function ToastContainer() {
    const { toasts, removeToast } = useToast();

    if (toasts.length === 0) return null;

    return (
        // Fixed overlay — bottom-right, above all other content
        <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-3">
            {toasts.map((toast) => (
                <div
                    key={toast.id}
                    className={`
                        ${TOAST_STYLES[toast.type] || TOAST_STYLES.info}
                        animate-slide-in
                        flex items-center gap-3
                        px-4 py-3 rounded-lg shadow-lg
                        min-w-[300px] max-w-[420px]
                    `}
                >
                    {/* Icon */}
                    <span className="text-lg font-bold">
                        {TOAST_ICONS[toast.type] || TOAST_ICONS.info}
                    </span>

                    {/* Message */}
                    <span className="flex-1 text-sm">{toast.message}</span>

                    {/* Close button */}
                    <button
                        onClick={() => removeToast(toast.id)}
                        className="ml-2 text-white/70 hover:text-white text-lg leading-none cursor-pointer"
                    >
                        &times;
                    </button>
                </div>
            ))}
        </div>
    );
}
