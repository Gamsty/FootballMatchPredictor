/*
Settings — operator preferences page.

Consolidates the per-device config that used to live in URL flags and
ad-hoc localStorage writes into one place. Three sections:

  - Advanced mode   → fmp.advanced.enabled  (logging UI, Value tab, Performance delete)
  - Bet-write token → fmp.bet_token         (X-Bet-Token header on POST/DELETE)
  - Bankroll        → fmp.bankroll.nok      (¼-Kelly stake calculations)

Reads/writes happen synchronously to localStorage so the next component
remount reflects the change. Components that hold the same keys in state
(BestOfWeek + ValueBets bankroll, Dashboard advancedMode) refresh on
tab-switch which unmounts/remounts them, so saves here propagate the
moment the user switches away from Settings.

Lives as a Dashboard tab — not a modal — because config screens deserve
their own surface and you typically want to come back to verify state.
*/

import { useState } from 'react';

const ADVANCED_KEY = 'fmp.advanced.enabled';
const TOKEN_KEY    = 'fmp.bet_token';
const BANKROLL_KEY = 'fmp.bankroll.nok';

function read(key, fallback = '') {
    if (typeof window === 'undefined') return fallback;
    return localStorage.getItem(key) ?? fallback;
}

function Settings({ advancedMode, onAdvancedChange }) {
    const [tokenValue, setTokenValue] = useState(read(TOKEN_KEY));
    const [bankrollValue, setBankrollValue] = useState(read(BANKROLL_KEY, '0'));
    const [tokenSaved, setTokenSaved] = useState(false);
    const [bankrollSaved, setBankrollSaved] = useState(false);
    const [showToken, setShowToken] = useState(false);

    const tokenIsSet = !!read(TOKEN_KEY);

    const handleSaveToken = () => {
        if (tokenValue.trim()) {
            localStorage.setItem(TOKEN_KEY, tokenValue.trim());
        } else {
            localStorage.removeItem(TOKEN_KEY);
        }
        setTokenSaved(true);
        setTimeout(() => setTokenSaved(false), 2000);
    };

    const handleClearToken = () => {
        localStorage.removeItem(TOKEN_KEY);
        setTokenValue('');
        setTokenSaved(true);
        setTimeout(() => setTokenSaved(false), 2000);
    };

    const handleSaveBankroll = () => {
        const n = Number(bankrollValue);
        if (Number.isFinite(n) && n >= 0) {
            localStorage.setItem(BANKROLL_KEY, String(Math.floor(n)));
        }
        setBankrollSaved(true);
        setTimeout(() => setBankrollSaved(false), 2000);
    };

    const handleResetAll = () => {
        if (!confirm('Clear advanced mode, bet token, and bankroll? Logged bets are NOT affected — only per-device preferences.')) return;
        localStorage.removeItem(ADVANCED_KEY);
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(BANKROLL_KEY);
        setTokenValue('');
        setBankrollValue('0');
        onAdvancedChange?.(false);
    };

    return (
        <div className="space-y-8 sm:space-y-10">
            <header className="pb-4 border-b border-line">
                <div className="eyebrow mb-2">Per-device</div>
                <h2 className="display text-3xl sm:text-4xl text-ink leading-[1] font-light">
                    Settings<span className="text-accent">.</span>
                </h2>
                <p className="text-ink-soft text-sm mt-2 max-w-2xl font-light">
                    Configuration is stored in this browser's localStorage —
                    it doesn't sync between devices, and clearing cookies
                    wipes it. Bets and Performance history live on the server
                    and are not affected by anything here.
                </p>
            </header>

            {/* Advanced mode */}
            <Section
                eyebrow="Mode"
                title="Advanced mode"
                description="Unlocks the Value tab (every +EV pick across all leagues), Log buttons on match cards / details / accumulator, and delete actions inside Performance. Public visitors browse read-only without it."
            >
                <div className="flex items-center gap-4">
                    <button
                        onClick={() => onAdvancedChange?.(!advancedMode)}
                        className={
                            'mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 border transition-colors cursor-pointer inline-flex items-center gap-2 ' +
                            (advancedMode
                                ? 'bg-ink text-paper border-ink hover:bg-accent hover:border-accent'
                                : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                        }
                    >
                        <span>{advancedMode ? 'ON' : 'OFF'}</span>
                        <span aria-hidden="true">{advancedMode ? '●' : '○'}</span>
                    </button>
                    <span className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                        {advancedMode ? 'logging + delete enabled' : 'read-only'}
                    </span>
                </div>
            </Section>

            {/* Bet-write token */}
            <Section
                eyebrow="Auth"
                title="Bet-write token"
                description={
                    <>
                        The backend rejects POST/DELETE on <code className="mono text-[0.85em] bg-paper-tint border border-line px-1">/api/bets</code> without a matching <code className="mono text-[0.85em] bg-paper-tint border border-line px-1">X-Bet-Token</code> header. Find the value in <code className="mono text-[0.85em] bg-paper-tint border border-line px-1">backend/.env</code> (<code className="mono text-[0.85em] bg-paper-tint border border-line px-1">BET_WRITE_TOKEN=…</code>) and paste it here.
                    </>
                }
            >
                <div className="space-y-3">
                    <div className="flex items-center gap-2">
                        <span className={
                            'mono text-[0.65rem] uppercase tracking-[0.12em] px-2 py-1 border ' +
                            (tokenIsSet ? 'text-positive border-positive' : 'text-ink-muted border-line')
                        }>
                            {tokenIsSet ? 'TOKEN SET ●' : 'NO TOKEN ○'}
                        </span>
                    </div>
                    <div className="flex flex-col sm:flex-row gap-2">
                        <input
                            type={showToken ? 'text' : 'password'}
                            value={tokenValue}
                            onChange={(e) => setTokenValue(e.target.value)}
                            placeholder="Paste BET_WRITE_TOKEN value"
                            className="flex-1 bg-paper border border-line px-3 py-2 mono text-sm text-ink focus:outline-none focus:border-accent transition-colors"
                            autoComplete="off"
                        />
                        <button
                            onClick={() => setShowToken(!showToken)}
                            className="mono text-[0.65rem] uppercase tracking-[0.12em] px-2 py-2 border border-line text-ink-soft hover:text-ink hover:border-ink-muted transition-colors cursor-pointer"
                        >
                            {showToken ? 'Hide' : 'Show'}
                        </button>
                    </div>
                    <div className="flex items-center gap-3 flex-wrap">
                        <button
                            onClick={handleSaveToken}
                            className="mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 bg-ink text-paper border border-ink hover:bg-accent hover:border-accent transition-colors cursor-pointer"
                        >
                            Save token
                        </button>
                        {tokenIsSet && (
                            <button
                                onClick={handleClearToken}
                                className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted hover:text-danger transition-colors cursor-pointer"
                            >
                                Clear
                            </button>
                        )}
                        {tokenSaved && (
                            <span className="mono text-[0.65rem] uppercase tracking-[0.12em] text-positive">
                                Saved ✓
                            </span>
                        )}
                    </div>
                </div>
            </Section>

            {/* Bankroll */}
            <Section
                eyebrow="Stake"
                title="Bankroll"
                description={
                    <>
                        Total NOK you're willing to expose to the model. ¼-Kelly stake suggestions on Value and Best Picks tabs scale against this number. Set to <code className="mono text-[0.85em] bg-paper-tint border border-line px-1">0</code> to hide stake recommendations entirely.
                    </>
                }
            >
                <div className="flex items-center gap-3 flex-wrap">
                    <input
                        type="number"
                        inputMode="numeric"
                        min="0"
                        step="100"
                        value={bankrollValue}
                        onChange={(e) => setBankrollValue(e.target.value)}
                        className="w-32 bg-paper border border-line px-3 py-2 mono text-sm text-ink focus:outline-none focus:border-accent transition-colors"
                    />
                    <span className="mono text-xs text-ink-muted">NOK</span>
                    <button
                        onClick={handleSaveBankroll}
                        className="mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 bg-ink text-paper border border-ink hover:bg-accent hover:border-accent transition-colors cursor-pointer"
                    >
                        Save bankroll
                    </button>
                    {bankrollSaved && (
                        <span className="mono text-[0.65rem] uppercase tracking-[0.12em] text-positive">
                            Saved ✓
                        </span>
                    )}
                </div>
                <p className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted mt-3">
                    Switch tabs and back to refresh Kelly displays elsewhere.
                </p>
            </Section>

            {/* Reset */}
            <Section eyebrow="Danger" title="Reset device preferences">
                <p className="text-ink-soft text-sm mb-3 font-light">
                    Wipes advanced mode, bet token, and bankroll from this
                    browser. Doesn't touch logged bets or anything on the
                    server — those persist regardless.
                </p>
                <button
                    onClick={handleResetAll}
                    className="mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 border border-danger text-danger hover:bg-danger hover:text-paper transition-colors cursor-pointer"
                >
                    Reset all preferences
                </button>
            </Section>
        </div>
    );
}

function Section({ eyebrow, title, description, children }) {
    return (
        <section>
            <div className="eyebrow mb-2">{eyebrow}</div>
            <h3 className="display text-xl text-ink mb-2">{title}</h3>
            {description && (
                <p className="text-ink-soft text-sm mb-4 max-w-2xl font-light leading-relaxed">
                    {description}
                </p>
            )}
            {children}
        </section>
    );
}

export default Settings;
