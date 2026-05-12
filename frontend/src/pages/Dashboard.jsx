/*
Dashboard Page — Main Landing Page

Shows upcoming match predictions grouped by league with:
    - Category tabs: "Today" and "Upcoming" (tomorrow + day after)
    - Filter buttons: High Confidence, Upset Pick, Banker (multi-select)
    - Match cards: compact prediction cards in a responsive grid
    - Accumulator builder: select bets across matches, calculate combined odds/returns
    - Match detail modal: click a card to see all betting markets

Data flow:
    1. Fetches predictions from /api/predictions/upcoming (3 days ahead)
    2. Client-side filters by tab (today vs upcoming) and category tags
    3. Groups matches by league, sorted by priority (PL first, then alphabetical)
    4. Auto-refreshes every 5 minutes
*/

import { useState, useEffect, useCallback } from 'react';
import { footballAPI } from '../services/api';
import MatchCard from '../components/MatchCard';
import FilterBar from '../components/FilterBar';
import CategoryTabs from '../components/CategoryTabs';
import MatchDetail from '../components/MatchDetail';
import {
    isToday, COMPETITION_LABELS, formatOdds,
    calculateAccumulator, isTomorrow
} from '../utils/constants';

function Dashboard() {
    // Data state
    const [matches, setMatches] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // UI state
    const [activeTab, setActiveTab] = useState('today');
    const [selectedMatch, setSelectedMatch] = useState(null);
    const [filters, setFilters] = useState({
        categories: [],
    });

    // Accumulator state
    const [accumulator, setAccumulator] = useState([]);
    const [stake, setStake] = useState(100);

    // Fetch all matches for the next 3 days on mount and tab change
    const fetchPredictions = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const params = { days: 3, sort_by: 'date' };

            const data = await footballAPI.getUpcomingPredictions(params);
            setMatches(data.matches || []);
        } catch (err) {
            setError('Failed to load predictions. Make sure the backend is running.');
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [activeTab]);

    useEffect(() => { fetchPredictions(); }, [fetchPredictions]);

    // Auto-refresh every 5 minutes
    useEffect(() => {
        const interval = setInterval(fetchPredictions, 5 * 60 * 1000);
        return () => clearInterval(interval);
    }, [fetchPredictions]);

    // Fixture refresh is handled exclusively by the nightly Azure Container Apps Job
    // (backend/jobs/retrain.py). The /api/fixtures/refresh endpoint requires an admin
    // token so it can't be triggered from this public UI — exposing it to all visitors
    // would burn our football-data.org free-tier quota (10 req/min).

    // Client-side filtering: tabs + categories
    const activeCategories = filters.categories || [];
    let filteredMatches = [...matches];

    // Tab date filtering
    if (activeTab === 'today') {
        filteredMatches = filteredMatches.filter(m => isToday(m.date));
    } else if (activeTab === 'upcoming') {
        filteredMatches = filteredMatches.filter(m => !isToday(m.date));
    }

    // Category tag filtering
    if (activeCategories.length > 0) {
        filteredMatches = filteredMatches.filter(m =>
            activeCategories.some(cat => (m.tags || []).includes(cat))
        );
    }

    // Match counts (from all matches, not filtered — so tab badges are always accurate)
    const matchCounts = {
        today: matches.filter(m => isToday(m.date)).length,
        upcoming: matches.filter(m => !isToday(m.date)).length,
    };

    // Group by league, ordered by priority
    const LEAGUE_ORDER = [
        'Premier League',
        'Championship',
        'La Liga',
        'Bundesliga',
        'Serie A',
        'Ligue 1',
        'Eredivisie',
        'Primeira Liga',
        'UEFA Champions League',
    ];

    const groupedMatches = {};
    filteredMatches.forEach(match => {
        const league = match.competition || 'Other';
        if (!groupedMatches[league]) groupedMatches[league] = [];
        groupedMatches[league].push(match);
    });
    Object.values(groupedMatches).forEach(leagueMatches => {
        leagueMatches.sort((a, b) => new Date(a.date) - new Date(b.date));
    });

    // Sort leagues: priority order first, then alphabetical for any others
    const sortedLeagues = Object.keys(groupedMatches).sort((a, b) => {
        const idxA = LEAGUE_ORDER.indexOf(a);
        const idxB = LEAGUE_ORDER.indexOf(b);
        if (idxA !== -1 && idxB !== -1) return idxA - idxB;
        if (idxA !== -1) return -1;
        if (idxB !== -1) return 1;
        return a.localeCompare(b);
    });

    // Accumulator helpers
    const accMatchIds = new Set(accumulator.map(s => s.matchId));

    const toggleAccumulator = (match, bet) => {
        if (accMatchIds.has(match.id)) {
            setAccumulator(prev => prev.filter(s => s.matchId !== match.id));
        } else {
            setAccumulator(prev => [...prev, {
                matchId: match.id,
                homeTeam: match.home_team.short_name || match.home_team.name,
                awayTeam: match.away_team.short_name || match.away_team.name,
                label: bet.label,
                prob: bet.prob,
            }]);
        }
    };

    const accResult = calculateAccumulator(accumulator, stake);

    return (
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            {/* Header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-5">
                <div>
                    <h1 className="text-2xl font-bold text-gray-100">Match Predictions</h1>
                    <p className="text-gray-500 text-sm mt-0.5">
                        {filteredMatches.length} matches
                    </p>
                </div>
            </div>

            {/* Tabs */}
            <CategoryTabs activeTab={activeTab} onTabChange={setActiveTab} matchCounts={matchCounts} />

            {/* Filters */}
            <FilterBar filters={filters} onFilterChange={setFilters} />

            {/* Accumulator Bar */}
            {accumulator.length > 0 && (
                <div className="bg-gray-800/60 border border-gray-700/40 rounded-xl p-4 mb-6">
                    <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-semibold text-gray-200">
                            Accumulator ({accumulator.length} selections)
                        </h3>
                        <button
                            onClick={() => setAccumulator([])}
                            className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
                        >
                            Clear All
                        </button>
                    </div>

                    {/* Selections */}
                    <div className="space-y-1.5 mb-3">
                        {accumulator.map(sel => (
                            <div key={sel.matchId} className="flex items-center justify-between py-1.5 px-3 rounded-lg bg-gray-900/50">
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => setAccumulator(prev => prev.filter(s => s.matchId !== sel.matchId))}
                                        className="text-gray-600 hover:text-red-400 text-xs transition-colors"
                                    >&times;</button>
                                    <span className="text-xs text-gray-400">{sel.homeTeam} vs {sel.awayTeam}</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <span className="text-xs text-blue-300 font-medium">{sel.label}</span>
                                    <span className="text-[10px] text-gray-600">@ {formatOdds(sel.prob)}</span>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Stake + Returns */}
                    <div className="flex items-center gap-3 pt-3 border-t border-gray-700/30">
                        <div className="flex items-center gap-1.5">
                            <span className="text-xs text-gray-500">Stake:</span>
                            <input
                                type="number"
                                value={stake}
                                onChange={(e) => setStake(Math.max(0, Number(e.target.value)))}
                                className="w-20 bg-gray-900 border border-gray-700/50 rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500/50"
                            />
                            <span className="text-xs text-gray-600">NOK</span>
                        </div>
                        <div className="flex-1" />
                        <div className="text-right">
                            <div className="text-[10px] text-gray-500">
                                Combined odds: <span className="text-gray-400 font-mono">{accResult.totalOdds}</span>
                            </div>
                            <div className="text-sm font-bold text-emerald-400">
                                Return: {accResult.potentialReturn.toLocaleString()} NOK
                                <span className="text-xs text-emerald-500/60 ml-1">(+{accResult.profit.toLocaleString()})</span>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Content */}
            {loading && (
                <div className="text-center py-16">
                    <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500 mx-auto mb-4" />
                    <p className="text-gray-500 text-sm">Loading predictions...</p>
                </div>
            )}

            {error && (
                <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-center">
                    <p className="text-red-400 text-sm">{error}</p>
                    <button onClick={fetchPredictions} className="mt-3 text-xs text-red-300 hover:text-red-200 underline">
                        Try again
                    </button>
                </div>
            )}

            {!loading && !error && filteredMatches.length === 0 && (
                <div className="text-center py-16">
                    <div className="text-4xl mb-3 opacity-50">⚽</div>
                    <h3 className="text-lg font-semibold text-gray-400 mb-2">No matches found</h3>
                    <p className="text-gray-500 text-sm mb-4">
                        Try adjusting your filters. New fixtures sync automatically every night.
                    </p>
                </div>
            )}

            {!loading && !error && filteredMatches.length > 0 && (
                <div className="space-y-7">
                    {sortedLeagues.map(league => ({ league, leagueMatches: groupedMatches[league] })).map(({ league, leagueMatches }) => (
                        <div key={league}>
                            <div className="flex items-center gap-3 mb-3">
                                <h2 className="text-sm font-semibold text-gray-300">
                                    {COMPETITION_LABELS[league] || league}
                                </h2>
                                <span className="text-xs text-gray-600">{leagueMatches.length}</span>
                                <div className="flex-1 border-t border-gray-800" />
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                                {leagueMatches.map(match => (
                                    <MatchCard
                                        key={match.id}
                                        match={match}
                                        onClick={setSelectedMatch}
                                        isSelected={accMatchIds.has(match.id)}
                                        onToggleAccumulator={toggleAccumulator}
                                    />
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Match Detail Modal */}
            {selectedMatch && (
                <MatchDetail
                    match={selectedMatch}
                    onClose={() => setSelectedMatch(null)}
                />
            )}
        </div>
    );
}

export default Dashboard;
