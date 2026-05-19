/*
BetLog — slide-in bet list with multi-filter + sort.

Opened from PerformanceHub's "Full bet log →" link. Focused on browsing —
no perf summary, just filters (status, market, league) and column sort.

Click a row → opens BetDetail (passed up via onSelectBet so the parent owns
the modal state and refresh cascade).
*/

import { useState, useEffect, useMemo } from 'react';
import { footballAPI } from '../services/api';
import BetRow from './BetRow';
import { COMPETITION_LABELS } from '../utils/constants';
import { MARKET_BADGE } from '../utils/marketBadges';

const STATUS_OPTIONS = [
    { id: '',        label: 'All' },
    { id: 'pending', label: 'Pending' },
    { id: 'won',     label: 'Won' },
    { id: 'lost',    label: 'Lost' },
    { id: 'void',    label: 'Void' },
];

const SORTS = [
    { id: 'placed_desc', label: 'Newest', cmp: (a, b) => new Date(b.placed_at) - new Date(a.placed_at) },
    { id: 'placed_asc',  label: 'Oldest', cmp: (a, b) => new Date(a.placed_at) - new Date(b.placed_at) },
    { id: 'pl_desc',     label: 'Highest P/L', cmp: (a, b) => (b.profit_loss ?? -Infinity) - (a.profit_loss ?? -Infinity) },
    { id: 'pl_asc',      label: 'Lowest P/L',  cmp: (a, b) => (a.profit_loss ?? Infinity)  - (b.profit_loss ?? Infinity) },
    { id: 'stake_desc',  label: 'Biggest stake', cmp: (a, b) => (b.stake || 0) - (a.stake || 0) },
    { id: 'odds_desc',   label: 'Longest odds',  cmp: (a, b) => (b.odds_at_bet || 0) - (a.odds_at_bet || 0) },
];

function BetLog({ onClose, canEdit = false, onSelectBet }) {
    const [bets, setBets] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [status, setStatus] = useState('');
    const [market, setMarket] = useState('');
    const [league, setLeague] = useState('');
    const [sortId, setSortId] = useState('placed_desc');

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError(null);
        const params = { limit: 500 };
        if (status) params.status = status;
        footballAPI.listBets(params, { signal: controller.signal })
            .then(setBets)
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(err.message || 'Failed to load bets');
            })
            .finally(() => setLoading(false));
        return () => controller.abort();
    }, [status]);

    // Unique market + league sets — derived from current bet list so filters
    // only show what's actually present (no dead options).
    const allMarkets = useMemo(() => {
        if (!bets?.bets) return [];
        const set = new Set(bets.bets.map(b => b.market));
        return [...set].sort();
    }, [bets]);

    const allLeagues = useMemo(() => {
        if (!bets?.bets) return [];
        const set = new Set(
            bets.bets
                .filter(b => b.match?.competition)
                .map(b => b.match.competition)
        );
        return [...set].sort();
    }, [bets]);

    const filtered = useMemo(() => {
        if (!bets?.bets) return [];
        let rows = bets.bets;
        if (market) rows = rows.filter(b => b.market === market);
        if (league) rows = rows.filter(b => b.match?.competition === league);
        const sort = SORTS.find(s => s.id === sortId) || SORTS[0];
        return [...rows].sort(sort.cmp);
    }, [bets, market, league, sortId]);

    const handleDelete = async (betId) => {
        if (!confirm('Delete this bet?')) return;
        await footballAPI.deleteBet(betId);
        // Re-fetch with current status filter.
        const params = { limit: 500 };
        if (status) params.status = status;
        const fresh = await footballAPI.listBets(params);
        setBets(fresh);
    };

    return (
        <div
            className="fixed inset-0 z-50 bg-ink/40 flex justify-end animate-fade-in"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-paper w-full max-w-4xl border-l border-line overflow-y-auto animate-slide-in">
                {/* Header */}
                <div className="sticky top-0 bg-paper/95 backdrop-blur-md border-b border-line z-10">
                    <div className="px-6 py-4 flex items-start justify-between">
                        <div>
                            <div className="eyebrow mb-1">Tracking</div>
                            <h2 className="display text-3xl text-ink">
                                Bet log<span className="text-accent">.</span>
                            </h2>
                        </div>
                        <button
                            onClick={onClose}
                            className="text-ink-muted hover:text-ink text-2xl leading-none cursor-pointer"
                            aria-label="Close"
                        >×</button>
                    </div>
                </div>

                <div className="px-6 py-6 space-y-5">
                    {error && (
                        <div className="bg-paper-tint border-l-2 border-danger p-4">
                            <p className="text-ink-soft text-sm">{error}</p>
                        </div>
                    )}

                    {/* Filters row */}
                    <div className="flex flex-wrap items-end gap-x-6 gap-y-3 pb-4 border-b border-line">
                        <div>
                            <div className="eyebrow mb-1.5">Status</div>
                            <div className="flex gap-1.5">
                                {STATUS_OPTIONS.map(opt => (
                                    <button
                                        key={opt.id}
                                        onClick={() => setStatus(opt.id)}
                                        className={
                                            'mono text-[0.7rem] uppercase tracking-[0.1em] px-2 py-1 border transition-colors cursor-pointer ' +
                                            (status === opt.id
                                                ? 'bg-ink text-paper border-ink'
                                                : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                                        }
                                    >
                                        {opt.label}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <SelectField
                            label="Market"
                            value={market}
                            onChange={setMarket}
                            options={[
                                { value: '', label: 'All markets' },
                                ...allMarkets.map(m => ({
                                    value: m,
                                    label: m === 'combo' ? 'Combo' : (MARKET_BADGE[m] || m),
                                })),
                            ]}
                        />

                        <SelectField
                            label="League"
                            value={league}
                            onChange={setLeague}
                            options={[
                                { value: '', label: 'All leagues' },
                                ...allLeagues.map(l => ({
                                    value: l,
                                    label: COMPETITION_LABELS[l] || l,
                                })),
                            ]}
                        />

                        <SelectField
                            label="Sort"
                            value={sortId}
                            onChange={setSortId}
                            options={SORTS.map(s => ({ value: s.id, label: s.label }))}
                        />

                        {(market || league || status || sortId !== 'placed_desc') && (
                            <button
                                onClick={() => {
                                    setMarket(''); setLeague(''); setStatus(''); setSortId('placed_desc');
                                }}
                                className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted hover:text-accent transition-colors cursor-pointer"
                            >
                                Reset
                            </button>
                        )}
                    </div>

                    {/* Result count */}
                    <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                        {loading ? 'Loading…' : `${filtered.length} ${filtered.length === 1 ? 'bet' : 'bets'}`}
                    </div>

                    {/* List */}
                    {!loading && filtered.length === 0 && (
                        <div className="text-center py-12 max-w-md mx-auto">
                            <div className="eyebrow mb-3">No matches</div>
                            <h3 className="display text-2xl text-ink mb-3">
                                Nothing in this slice<span className="text-accent">.</span>
                            </h3>
                            <p className="text-ink-soft text-sm">
                                Adjust the filters above, or reset to see the full log.
                            </p>
                        </div>
                    )}

                    {!loading && filtered.length > 0 && (
                        <div className="space-y-1.5">
                            {filtered.map(b => (
                                <BetRow
                                    key={b.id}
                                    bet={b}
                                    onSelect={onSelectBet}
                                    onDelete={canEdit ? handleDelete : null}
                                />
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function SelectField({ label, value, onChange, options }) {
    return (
        <div>
            <div className="eyebrow mb-1.5">{label}</div>
            <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className="bg-paper border border-line px-2 py-1 mono text-[0.75rem] text-ink focus:outline-none focus:border-accent transition-colors cursor-pointer"
            >
                {options.map(opt => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
            </select>
        </div>
    );
}

export default BetLog;
