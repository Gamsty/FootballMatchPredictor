/*
useModalDismiss — Escape-to-close for overlay dialogs.

Every overlay in this app already closes on backdrop click, but only MatchDetail
listened for Escape. Escape is the one dismissal gesture a keyboard user expects
to work everywhere, and on desktop it is faster than aiming at a 24px ×.

Nested dialogs are the reason this is a shared hook rather than a copy-pasted
useEffect. MatchDetail can have LogBetModal open on top of it (they render as
siblings, but they stack visually); if both attached a window listener, one
Escape would close both and dump the reader back to the match list. A module
level stack fixes the order: only the most recently mounted dialog reacts, so
Escape peels one layer at a time.

Usage:
    useModalDismiss(onClose);
    ...
    <div className="fixed inset-0 ..." role="dialog" aria-modal="true">
*/

import { useEffect } from 'react';

// Mounted dialogs, oldest first. The last entry owns Escape.
const stack = [];

export function useModalDismiss(onClose, { enabled = true } = {}) {
    useEffect(() => {
        if (!enabled || typeof onClose !== 'function') return undefined;

        // An object identity rather than the callback itself: two dialogs could
        // legitimately be handed the same close function.
        const token = {};
        stack.push(token);

        const handleKey = (event) => {
            if (event.key !== 'Escape') return;
            if (stack[stack.length - 1] !== token) return;   // not the top dialog
            event.stopPropagation();
            onClose();
        };

        window.addEventListener('keydown', handleKey);
        return () => {
            window.removeEventListener('keydown', handleKey);
            const at = stack.indexOf(token);
            if (at !== -1) stack.splice(at, 1);
        };
    }, [onClose, enabled]);
}

export default useModalDismiss;
