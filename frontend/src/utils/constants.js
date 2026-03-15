/*
Constants and Utility Functions

Shared configuration and helper functions used across all frontend components.

Sections:
    1. Display formatters — percentage, date, time, odds
    2. Confidence helpers — color/label based on probability thresholds
    3. Market & competition config — labels, categories, display names
    4. Tag config — filter button definitions (High Confidence, Upset, Banker)
    5. Bet recommendation engine — collectCandidates, generateBetReason, getRecommendedBets
    6. Accumulator calculator — combined odds and returns
*/

// Maps API outcome keys to display-friendly labels
export const OUTCOME_LABELS = {
    HOME_WIN: 'HOME WIN',
    DRAW: 'DRAW',
    AWAY_WIN: 'AWAY WIN'
};

// Converts a decimal probability to a percentage string
export const formatPercentage = (value) => {
    return `${(value * 100).toFixed(1)}%`;
};

// Converts ISO date string to readable format
export const formatDate = (dateString) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
};

// Format time as "15:00"
export const formatTime = (dateString) => {
    const date = new Date(dateString);
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    });
};

// Format match date as "Today", "Tomorrow", or "Wed, Mar 18"
export const formatMatchDate = (dateString) => {
    const date = new Date(dateString);
    const now = new Date();

    const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const nowOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const diffDays = Math.round((dateOnly - nowOnly) / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Tomorrow';
    if (diffDays === -1) return 'Yesterday';

    return date.toLocaleDateString('en-US', {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
    });
};

export const isToday = (dateString) => {
    const date = new Date(dateString);
    const now = new Date();
    return date.toDateString() === now.toDateString();
};

export const isTomorrow = (dateString) => {
    const date = new Date(dateString);
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    return date.toDateString() === tomorrow.toDateString();
};

// Confidence label and color
export const getConfidenceLabel = (confidence) => {
    if (confidence >= 0.6) return 'High';
    if (confidence >= 0.5) return 'Medium';
    return 'Low';
};

export const getConfidenceColor = (confidence) => {
    if (confidence >= 0.6) return 'bg-green-500';
    if (confidence >= 0.45) return 'bg-amber-500';
    return 'bg-red-500';
};

export const getConfidenceTextColor = (confidence) => {
    if (confidence >= 0.6) return 'text-green-400';
    if (confidence >= 0.45) return 'text-amber-400';
    return 'text-red-400';
};

// Market labels for display
export const MARKET_LABELS = {
    btts: 'Both Teams to Score',
    over_1_5: 'Over/Under 1.5 Goals',
    over_2_5: 'Over/Under 2.5 Goals',
    over_3_5: 'Over/Under 3.5 Goals',
    over_4_5: 'Over/Under 4.5 Goals',
    ht_result: 'Half-Time Result',
    home_wins_at_least_one_half: 'Home Wins At Least One Half',
    away_wins_at_least_one_half: 'Away Wins At Least One Half',
    home_wins_both_halves: 'Home Wins Both Halves',
    away_wins_both_halves: 'Away Wins Both Halves',
    corners_result: 'Corner Kicks Result',
    total_corners_over_8_5: 'Over/Under 8.5 Corners',
    total_corners_over_9_5: 'Over/Under 9.5 Corners',
    total_corners_over_10_5: 'Over/Under 10.5 Corners',
    total_cards_over_3_5: 'Over/Under 3.5 Cards',
    total_cards_over_4_5: 'Over/Under 4.5 Cards',
    total_cards_over_5_5: 'Over/Under 5.5 Cards',
};

// Market categories for grouping in the detail view
export const MARKET_CATEGORIES = {
    goals: {
        label: 'Goals',
        icon: '⚽',
        markets: ['btts', 'over_1_5', 'over_2_5', 'over_3_5', 'over_4_5'],
    },
    halftime: {
        label: 'Half-Time',
        icon: '⏱️',
        markets: ['ht_result', 'home_wins_at_least_one_half', 'away_wins_at_least_one_half', 'home_wins_both_halves', 'away_wins_both_halves'],
    },
    corners: {
        label: 'Corners',
        icon: '🚩',
        markets: ['corners_result', 'total_corners_over_8_5', 'total_corners_over_9_5', 'total_corners_over_10_5'],
    },
    cards: {
        label: 'Cards',
        icon: '🟨',
        markets: ['total_cards_over_3_5', 'total_cards_over_4_5', 'total_cards_over_5_5'],
    },
};

// Competition display names with flags
export const COMPETITION_LABELS = {
    'Premier League': '🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League',
    'La Liga': '🇪🇸 La Liga',
    'Bundesliga': '🇩🇪 Bundesliga',
    'Serie A': '🇮🇹 Serie A',
    'Ligue 1': '🇫🇷 Ligue 1',
    'Primeira Liga': '🇵🇹 Primeira Liga',
    'Championship': '🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship',
    'Eredivisie': '🇳🇱 Eredivisie',
    'UEFA Champions League': '🏆 Champions League',
    'Eliteserien': '🇳🇴 Eliteserien',
};

// Tag display config
export const TAG_CONFIG = {
    high_confidence: { label: 'High Confidence', color: 'bg-green-500/20 text-green-400 border-green-500/30' },
    upset: { label: 'Upset Pick', color: 'bg-purple-500/20 text-purple-400 border-purple-500/30' },
    banker: { label: 'Banker', color: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
};

// Date range options (kept for reference, not used in UI)
export const DATE_RANGE_OPTIONS = [
    { value: 1, label: 'Today' },
    { value: 3, label: '3 Days' },
];

// Format implied odds from probability
export const formatOdds = (probability) => {
    if (!probability || probability <= 0) return '-';
    return (1 / probability).toFixed(2);
};

// ============================================================================
// BET RECOMMENDATION ENGINE
// ============================================================================

// Collect all possible bet candidates from a match's prediction data.
// Gathers: match result (H/D/A), double chance (1X, X2, 12), BTTS, and Over/Under goals.
// Each candidate has { label, prob, market } used for ranking and display.
const collectCandidates = (match) => {
    const candidates = [];
    const p = match.prediction.probabilities;

    // Match result
    candidates.push({ label: 'Home Win', prob: p.home_win, market: 'result' });
    candidates.push({ label: 'Away Win', prob: p.away_win, market: 'result' });
    candidates.push({ label: 'Draw', prob: p.draw, market: 'result' });

    // Double chance
    if (match.double_chance) {
        const dc = match.double_chance;
        if (dc['1X']?.probability) candidates.push({ label: 'Home or Draw', prob: dc['1X'].probability, market: 'double_chance' });
        if (dc['X2']?.probability) candidates.push({ label: 'Draw or Away', prob: dc['X2'].probability, market: 'double_chance' });
        if (dc['12']?.probability) candidates.push({ label: 'Home or Away', prob: dc['12'].probability, market: 'double_chance' });
    }

    // BTTS
    const markets = match.markets || {};
    if (markets.btts?.probabilities) {
        const yp = markets.btts.probabilities['Yes'] || 0;
        const np = markets.btts.probabilities['No'] || 0;
        if (yp > 0) candidates.push({ label: 'BTTS Yes', prob: yp, market: 'btts' });
        if (np > 0) candidates.push({ label: 'BTTS No', prob: np, market: 'btts' });
    }

    // Over/Under goals
    for (const key of ['over_1_5', 'over_2_5', 'over_3_5', 'over_4_5']) {
        const m = markets[key];
        if (m?.probabilities) {
            const line = key.replace('over_', '').replace('_', '.');
            const op = m.probabilities['Over'] || 0;
            const up = m.probabilities['Under'] || 0;
            if (op > 0) candidates.push({ label: `Over ${line} Goals`, prob: op, market: key });
            if (up > 0) candidates.push({ label: `Under ${line} Goals`, prob: up, market: key });
        }
    }

    return candidates;
};

// Generate a detailed bullet-point analysis explaining why a bet was recommended.
// Uses match_stats (Elo, form, win rates, goals, H2H, clean sheets, league position)
// to build contextual descriptions for each bet type. Only shows stats that
// logically support the recommended bet to avoid contradictions.
const generateBetReason = (bet, match) => {
    const stats = match.match_stats || {};
    const homeName = match.home_team?.name || 'Home';
    const awayName = match.away_team?.name || 'Away';
    const pct = (bet.prob * 100).toFixed(0);

    const homeElo = stats.home_elo || 1500;
    const awayElo = stats.away_elo || 1500;
    const eloDiff = Math.abs(homeElo - awayElo);
    const homeStronger = homeElo > awayElo;
    const strongerName = homeStronger ? homeName : awayName;
    const weakerName = homeStronger ? awayName : homeName;

    const homeForm = stats.home_form || 0;
    const awayForm = stats.away_form || 0;
    const homeWinRate = stats.home_win_rate || 0;
    const awayWinRate = stats.away_win_rate || 0;
    const homeGoalsAvg = stats.home_goals_scored_avg || 0;
    const awayGoalsAvg = stats.away_goals_scored_avg || 0;
    const homeConcedeAvg = stats.home_goals_conceded_avg || 0;
    const awayConcedeAvg = stats.away_goals_conceded_avg || 0;
    const homePos = stats.home_league_position || 0;
    const awayPos = stats.away_league_position || 0;
    const h2hHome = stats.h2h_home_wins || 0;
    const h2hAway = stats.h2h_away_wins || 0;
    const h2hDraws = stats.h2h_draws || 0;
    const homeClean = stats.home_clean_sheets || 0;
    const awayClean = stats.away_clean_sheets || 0;
    const hasStats = homeGoalsAvg > 0 || awayGoalsAvg > 0 || homeForm > 0 || awayForm > 0;

    const formDesc = (f) => {
        if (f >= 12) return 'excellent form';
        if (f >= 9) return 'good form';
        if (f >= 6) return 'mixed form';
        if (f >= 3) return 'poor form';
        return 'terrible form';
    };

    const posDesc = (pos) => {
        if (pos <= 4) return 'near the top of the table';
        if (pos <= 8) return 'in the upper half of the table';
        if (pos <= 14) return 'mid-table';
        if (pos <= 17) return 'in the lower half';
        return 'near the bottom of the table';
    };

    const lines = [];

    // --- Match Result bets ---
    if (bet.label === 'Home Win') {
        // Form
        if (homeForm > 0 && awayForm > 0) {
            if (homeForm > awayForm + 3) {
                lines.push(`${homeName} have been in ${formDesc(homeForm)} with ${homeForm} points from their last 5 games, while ${awayName} have struggled with just ${awayForm} points.`);
            } else if (homeForm > awayForm) {
                lines.push(`${homeName} hold a slight form edge with ${homeForm} points from their last 5 games compared to ${awayName}'s ${awayForm}.`);
            } else {
                lines.push(`Form is tight — ${homeName} on ${homeForm} points vs ${awayName} on ${awayForm} from the last 5, but home advantage tips the balance.`);
            }
        }
        // Elo
        if (homeElo > awayElo + 50) {
            lines.push(`${homeName} are the considerably stronger side overall, with an Elo of ${Math.round(homeElo)} compared to ${awayName}'s ${Math.round(awayElo)}.`);
        } else if (homeElo > awayElo) {
            lines.push(`${homeName} have a slight quality edge (Elo ${Math.round(homeElo)} vs ${Math.round(awayElo)}).`);
        }
        // Position
        if (homePos > 0 && awayPos > 0) {
            if (homePos < awayPos) lines.push(`${homeName} sit ${homePos}${ordinal(homePos)} in the league, ${awayPos - homePos} places above ${awayName} who are ${posDesc(awayPos)}.`);
        }
        // Win rates
        if (homeWinRate > 0) lines.push(`${homeName} have won ${(homeWinRate * 100).toFixed(0)}% of their home matches this season.`);
        if (awayWinRate > 0 && awayWinRate < 0.35) lines.push(`${awayName} have only won ${(awayWinRate * 100).toFixed(0)}% of away matches — they struggle on the road.`);
        // H2H
        if (h2hHome > h2hAway) {
            lines.push(`Head-to-head favors ${homeName} with ${h2hHome} wins vs ${h2hAway} for ${awayName} in recent meetings.`);
        } else if (h2hHome === h2hAway && h2hHome > 0) {
            lines.push(`Recent head-to-head is evenly split at ${h2hHome} wins each.`);
        }
        // Goals — show both attack and defense
        if (hasStats) {
            if (homeGoalsAvg > 0) lines.push(`${homeName} average ${homeGoalsAvg.toFixed(1)} goals per home game.`);
            if (awayConcedeAvg > 1) lines.push(`${awayName} concede ${awayConcedeAvg.toFixed(1)} goals per away game — a defense that can be exploited.`);
            if (homeClean > 0.3) lines.push(`${homeName} keep clean sheets in ${(homeClean * 100).toFixed(0)}% of home games.`);
        }
        lines.push(`With home advantage and a ${pct}% predicted probability, ${homeName} are well-placed to claim the win.`);

    } else if (bet.label === 'Away Win') {
        // Elo
        if (awayElo > homeElo + 50) {
            lines.push(`${awayName} are a significantly stronger side with an Elo of ${Math.round(awayElo)} vs ${homeName}'s ${Math.round(homeElo)}, which more than offsets the home advantage.`);
        } else if (awayElo > homeElo) {
            lines.push(`${awayName} edge ${homeName} on overall quality (Elo ${Math.round(awayElo)} vs ${Math.round(homeElo)}).`);
        }
        // Form
        if (awayForm > 0 && homeForm > 0) {
            if (awayForm > homeForm + 3) {
                lines.push(`${awayName} are in ${formDesc(awayForm)} picking up ${awayForm} points from their last 5, while ${homeName} have managed just ${homeForm} points — ${formDesc(homeForm)}.`);
            } else if (awayForm > homeForm) {
                lines.push(`Form slightly favors ${awayName} with ${awayForm} points from their last 5 compared to ${homeName}'s ${homeForm}.`);
            } else {
                lines.push(`${homeName} have form on their side (${homeForm} pts vs ${awayForm}), but ${awayName}'s overall quality is expected to prevail.`);
            }
        }
        // Position
        if (homePos > 0 && awayPos > 0) {
            if (awayPos < homePos) lines.push(`${awayName} are ${awayPos}${ordinal(awayPos)} in the league, ${homePos - awayPos} places above ${homeName} who sit ${posDesc(homePos)}.`);
        }
        // Win rates
        if (awayWinRate > 0) lines.push(`${awayName} have won ${(awayWinRate * 100).toFixed(0)}% of their away matches this season.`);
        if (homeWinRate > 0 && homeWinRate < 0.4) lines.push(`${homeName} only win ${(homeWinRate * 100).toFixed(0)}% at home — their home advantage isn't strong.`);
        // H2H
        if (h2hAway > h2hHome) {
            lines.push(`Head-to-head favors ${awayName} with ${h2hAway} wins vs ${h2hHome} for ${homeName} in recent meetings.`);
        } else if (h2hHome === h2hAway && h2hHome > 0) {
            lines.push(`Recent head-to-head is evenly split at ${h2hHome} wins each.`);
        }
        // Goals — show both attack and defense
        if (hasStats) {
            if (awayGoalsAvg > 0) lines.push(`${awayName} score ${awayGoalsAvg.toFixed(1)} goals per away game on average.`);
            if (homeConcedeAvg > 1) lines.push(`${homeName} concede ${homeConcedeAvg.toFixed(1)} goals at home — a vulnerable defense.`);
            if (awayClean > 0.3) lines.push(`${awayName} keep ${(awayClean * 100).toFixed(0)}% clean sheets away — solid at the back.`);
        }
        lines.push(`The model gives ${awayName} a ${pct}% chance of winning here.`);

    } else if (bet.label === 'Draw') {
        // Elo
        if (eloDiff < 50) lines.push(`These are closely matched sides — ${homeName} (Elo ${Math.round(homeElo)}) and ${awayName} (Elo ${Math.round(awayElo)}) are separated by just ${Math.round(eloDiff)} Elo points.`);
        // Form
        if (homeForm > 0 && awayForm > 0) {
            if (Math.abs(homeForm - awayForm) <= 3) {
                lines.push(`Both teams are in similar form — ${homeName} on ${homeForm} points and ${awayName} on ${awayForm} from their last 5 games.`);
            } else {
                lines.push(`${homeForm > awayForm ? homeName : awayName} have the form edge (${Math.max(homeForm, awayForm)} vs ${Math.min(homeForm, awayForm)} pts), but not enough to pull clear.`);
            }
        }
        // Position
        if (homePos > 0 && awayPos > 0) {
            if (Math.abs(homePos - awayPos) <= 4) lines.push(`They're close in the table — ${homeName} ${homePos}${ordinal(homePos)} and ${awayName} ${awayPos}${ordinal(awayPos)}.`);
        }
        // Win rates — neither dominant
        if (homeWinRate > 0 && homeWinRate < 0.5) lines.push(`${homeName} only win ${(homeWinRate * 100).toFixed(0)}% of home matches — draws are common.`);
        if (awayWinRate > 0 && awayWinRate < 0.4) lines.push(`${awayName} only win ${(awayWinRate * 100).toFixed(0)}% away — they often settle for a point.`);
        // H2H
        if (h2hDraws > 0) lines.push(`${h2hDraws} of their last ${h2hHome + h2hAway + h2hDraws} meetings ended in a draw.`);
        // Goals
        if (hasStats && homeGoalsAvg > 0 && awayGoalsAvg > 0) {
            lines.push(`${homeName} average ${homeGoalsAvg.toFixed(1)} goals at home, ${awayName} average ${awayGoalsAvg.toFixed(1)} away — similar output suggests a tight game.`);
        }
        if (eloDiff < 50 || (homeForm > 0 && awayForm > 0 && Math.abs(homeForm - awayForm) <= 3)) {
            lines.push(`With little to separate these two sides, a draw at ${pct}% is a realistic outcome.`);
        } else {
            lines.push(`The model predicts a ${pct}% chance of a draw — this match could be tighter than the stats suggest.`);
        }

    // --- Double Chance ---
    } else if (bet.label === 'Home or Draw') {
        lines.push(`${homeName} have home advantage and are expected to avoid defeat here.`);
        if (homeElo > awayElo + 30) lines.push(`As the stronger team (Elo ${Math.round(homeElo)} vs ${Math.round(awayElo)}), ${homeName} should be hard to beat at home.`);
        // Form
        if (homeForm > 0 && awayForm > 0) {
            if (homeForm > awayForm) lines.push(`${homeName} are in better form with ${homeForm} points from 5 games compared to ${awayName}'s ${awayForm}.`);
            else lines.push(`${awayName} have slightly better form (${awayForm} vs ${homeForm} pts), but home advantage and the double chance cover that risk.`);
        }
        // Position
        if (homePos > 0 && awayPos > 0 && homePos < awayPos) {
            lines.push(`${homeName} sit ${homePos}${ordinal(homePos)} in the league, ${awayPos - homePos} places above ${awayName}.`);
        }
        // Win rate
        if (homeWinRate > 0.5) {
            lines.push(`${homeName} win ${(homeWinRate * 100).toFixed(0)}% of home matches — they rarely lose on their own ground.`);
        } else if (homeWinRate > 0) {
            lines.push(`${homeName} win ${(homeWinRate * 100).toFixed(0)}% of home matches — their draw rate makes the double chance appealing.`);
        }
        if (awayWinRate > 0 && awayWinRate < 0.35) lines.push(`${awayName} only win ${(awayWinRate * 100).toFixed(0)}% away — they rarely beat the home side.`);
        // H2H
        if (h2hHome >= h2hAway && h2hHome > 0) lines.push(`${homeName} have won ${h2hHome} of the last ${h2hHome + h2hAway + h2hDraws} meetings — ${awayName} struggle in this fixture.`);
        // Goals
        if (hasStats) {
            if (homeGoalsAvg > 0) lines.push(`${homeName} average ${homeGoalsAvg.toFixed(1)} goals per home game — they create enough chances to stay in matches.`);
            if (homeClean > 0.3) lines.push(`${homeName} keep ${(homeClean * 100).toFixed(0)}% clean sheets at home — defensively solid.`);
        }
        lines.push(`Backing ${homeName} to win or draw covers two outcomes at ${pct}% probability — a safer bet with lower odds.`);

    } else if (bet.label === 'Draw or Away') {
        // Elo
        if (awayElo > homeElo + 30) lines.push(`${awayName} are the stronger team (Elo ${Math.round(awayElo)} vs ${Math.round(homeElo)}), making a home win unlikely.`);
        // Form
        if (awayForm > homeForm && awayForm > 0) lines.push(`${awayName} are in better form recently, picking up ${awayForm} points from their last 5.`);
        if (homeForm > 0 && homeForm < 6) lines.push(`${homeName} are in ${formDesc(homeForm)} with just ${homeForm} points from their last 5 games.`);
        // Position
        if (homePos > 0 && awayPos > 0 && awayPos < homePos) {
            lines.push(`${awayName} sit ${awayPos}${ordinal(awayPos)} in the league, ${homePos - awayPos} places above ${homeName} (${homePos}${ordinal(homePos)}).`);
        }
        // Win rates
        if (homeWinRate > 0 && homeWinRate < 0.4) lines.push(`${homeName} only win ${(homeWinRate * 100).toFixed(0)}% of home matches — they're vulnerable at home.`);
        if (awayWinRate > 0) lines.push(`${awayName} have won ${(awayWinRate * 100).toFixed(0)}% of their away matches this season.`);
        // H2H
        if (h2hAway >= h2hHome && h2hAway > 0) lines.push(`${awayName} have won ${h2hAway} of the last ${h2hHome + h2hAway + h2hDraws} meetings — history favors the visitors.`);
        // Goals
        if (hasStats) {
            if (homeGoalsAvg > 0 && homeGoalsAvg < 1.5) lines.push(`${homeName} average ${homeGoalsAvg.toFixed(1)} goals at home — they lack the firepower to dominate.`);
            if (awayGoalsAvg > 0) lines.push(`${awayName} score ${awayGoalsAvg.toFixed(1)} goals per away game.`);
            if (awayClean > 0.2) lines.push(`${awayName} keep ${(awayClean * 100).toFixed(0)}% clean sheets away — they can frustrate the home side.`);
        }
        lines.push(`Covering both a draw and an ${awayName} win gives a ${pct}% probability — a solid hedge against the home side.`);

    } else if (bet.label === 'Home or Away') {
        // Elo
        if (eloDiff > 50) lines.push(`The gap in quality between ${strongerName} (Elo ${Math.round(homeStronger ? homeElo : awayElo)}) and ${weakerName} (Elo ${Math.round(homeStronger ? awayElo : homeElo)}) suggests one team will dominate.`);
        // Form
        if (homeForm > 0 && awayForm > 0) {
            if (Math.abs(homeForm - awayForm) > 3) {
                const betterForm = homeForm > awayForm ? homeName : awayName;
                lines.push(`${betterForm} are in clearly better form — the form gap makes a draw less likely.`);
            } else {
                lines.push(`Both teams are in similar form (${homeForm} vs ${awayForm} pts), but this fixture tends to produce decisive results.`);
            }
        }
        // Win rates
        if (homeWinRate > 0) lines.push(`${homeName} win ${(homeWinRate * 100).toFixed(0)}% at home, ${awayName} win ${awayWinRate > 0 ? (awayWinRate * 100).toFixed(0) : '0'}% away — one of them usually comes out on top.`);
        // H2H
        if (h2hDraws === 0 && (h2hHome + h2hAway) > 0) lines.push(`None of the last ${h2hHome + h2hAway} meetings ended in a draw — this fixture produces winners.`);
        // Goals
        if (hasStats) {
            const totalAvg = homeGoalsAvg + awayGoalsAvg;
            if (totalAvg > 2) lines.push(`With ${totalAvg.toFixed(1)} combined goals on average, these teams tend to produce decisive results.`);
        }
        // Position
        if (homePos > 0 && awayPos > 0 && Math.abs(homePos - awayPos) > 5) {
            lines.push(`A gap of ${Math.abs(homePos - awayPos)} league positions suggests one team is clearly stronger.`);
        }
        lines.push(`There's a ${pct}% chance this match produces a winner rather than ending in a draw.`);

    // --- BTTS ---
    } else if (bet.label === 'BTTS Yes') {
        if (hasStats) {
            // Goals scored — always show so user sees both teams' attacking output
            if (homeGoalsAvg > 0) lines.push(`${homeName} score ${homeGoalsAvg.toFixed(1)} goals per home game on average.`);
            if (awayGoalsAvg > 0) lines.push(`${awayName} score ${awayGoalsAvg.toFixed(1)} goals per away game.`);
            // Goals conceded — shows why both can score
            if (homeConcedeAvg > 0) lines.push(`${homeName} concede ${homeConcedeAvg.toFixed(1)} per game at home${homeConcedeAvg > 0.8 ? ' — their defense leaks goals' : ''}.`);
            if (awayConcedeAvg > 0) lines.push(`${awayName} concede ${awayConcedeAvg.toFixed(1)} per away game${awayConcedeAvg > 0.8 ? ' — giving the opposition chances' : ''}.`);
        }
        // Clean sheet rates
        if (homeClean > 0 || awayClean > 0) {
            if (homeClean < 0.3 && awayClean < 0.3) {
                lines.push(`Neither team keeps many clean sheets — ${homeName}: ${(homeClean * 100).toFixed(0)}%, ${awayName}: ${(awayClean * 100).toFixed(0)}%.`);
            } else {
                lines.push(`Clean sheet rates: ${homeName} ${(homeClean * 100).toFixed(0)}%, ${awayName} ${(awayClean * 100).toFixed(0)}%.`);
            }
        }
        lines.push(`With both teams capable of finding the net, there's a ${pct}% chance both teams score.`);

    } else if (bet.label === 'BTTS No') {
        if (hasStats) {
            // Goals scored — always show both teams
            if (homeGoalsAvg > 0) {
                if (homeGoalsAvg < 1) lines.push(`${homeName} only average ${homeGoalsAvg.toFixed(1)} goals at home — they struggle to break teams down.`);
                else lines.push(`${homeName} average ${homeGoalsAvg.toFixed(1)} goals at home, but a strong defense could shut them out.`);
            }
            if (awayGoalsAvg > 0) {
                if (awayGoalsAvg < 1) lines.push(`${awayName} manage just ${awayGoalsAvg.toFixed(1)} goals per away game — scoring on the road is a problem.`);
                else lines.push(`${awayName} score ${awayGoalsAvg.toFixed(1)} away, but could be kept quiet against a solid backline.`);
            }
            // Goals conceded
            if (homeConcedeAvg > 0 && homeConcedeAvg < 1) lines.push(`${homeName} only concede ${homeConcedeAvg.toFixed(1)} per home game — hard to score against.`);
            if (awayConcedeAvg > 0 && awayConcedeAvg < 1) lines.push(`${awayName} only concede ${awayConcedeAvg.toFixed(1)} per away game.`);
        }
        // Clean sheet rates
        if (homeClean > 0 || awayClean > 0) {
            if (homeClean > 0.3 || awayClean > 0.3) {
                lines.push(`Strong clean sheet records: ${homeName} ${(homeClean * 100).toFixed(0)}%, ${awayName} ${(awayClean * 100).toFixed(0)}%.`);
            } else {
                lines.push(`Clean sheets: ${homeName} ${(homeClean * 100).toFixed(0)}%, ${awayName} ${(awayClean * 100).toFixed(0)}%.`);
            }
        }
        if (eloDiff > 80) lines.push(`The quality gap between ${strongerName} and ${weakerName} often leads to one-sided games where the weaker team fails to score.`);
        lines.push(`The model gives ${pct}% probability that at least one team fails to score.`);

    // --- Over/Under ---
    } else if (bet.label.startsWith('Over')) {
        if (hasStats) {
            const totalAvg = homeGoalsAvg + awayGoalsAvg;
            // Only show combined avg if it supports the Over bet
            if (totalAvg > 2.5) lines.push(`${homeName} and ${awayName} combine for an average of ${totalAvg.toFixed(1)} goals — these matchups tend to be high-scoring.`);
            if (homeGoalsAvg > 1.2) lines.push(`${homeName} are prolific at home, averaging ${homeGoalsAvg.toFixed(1)} goals per game.`);
            if (awayGoalsAvg > 1.2) lines.push(`${awayName} are dangerous on the road with ${awayGoalsAvg.toFixed(1)} goals per away game.`);
            if (homeConcedeAvg > 1) lines.push(`${homeName} concede ${homeConcedeAvg.toFixed(1)} goals per home game — their defense can be exploited.`);
            if (awayConcedeAvg > 1) lines.push(`${awayName} concede ${awayConcedeAvg.toFixed(1)} goals per away game, leaving gaps at the back.`);
            if (homeClean < 0.25 && homeClean > 0) lines.push(`${homeName} only keep clean sheets ${(homeClean * 100).toFixed(0)}% of the time at home.`);
            if (awayClean < 0.25 && awayClean > 0) lines.push(`${awayName} only keep ${(awayClean * 100).toFixed(0)}% clean sheets away — they regularly concede.`);
            // Summary combining both sides
            if (totalAvg > 0) {
                const attackNote = (homeGoalsAvg > 1.2 || awayGoalsAvg > 1.2) ? 'strong attacking output' : 'moderate scoring';
                const defenseNote = (homeConcedeAvg > 1 || awayConcedeAvg > 1) ? 'leaky defenses' : 'defensive vulnerabilities';
                lines.push(`The model weighs both ${attackNote} and ${defenseNote} to predict this outcome.`);
            }
        }
        lines.push(`The model predicts a ${pct}% chance this match goes over the line.`);

    } else if (bet.label.startsWith('Under')) {
        if (hasStats) {
            const totalAvg = homeGoalsAvg + awayGoalsAvg;
            // Combined average
            if (totalAvg > 0 && totalAvg < 2.5) lines.push(`These teams combine for just ${totalAvg.toFixed(1)} goals on average — tight, low-scoring matchups.`);
            // Attacking output — always show so users see both sides
            if (homeGoalsAvg > 0) {
                if (homeGoalsAvg < 1.5) lines.push(`${homeName} average just ${homeGoalsAvg.toFixed(1)} goals at home — not a free-scoring side.`);
                else lines.push(`${homeName} average ${homeGoalsAvg.toFixed(1)} goals at home, but the opposition's defense may limit them.`);
            }
            if (awayGoalsAvg > 0) {
                if (awayGoalsAvg < 1.5) lines.push(`${awayName} manage only ${awayGoalsAvg.toFixed(1)} goals away from home.`);
                else lines.push(`${awayName} average ${awayGoalsAvg.toFixed(1)} goals away, but could be kept quiet by a solid defense.`);
            }
            // Defensive solidity
            if (homeClean > 0.3) lines.push(`${homeName} keep clean sheets in ${(homeClean * 100).toFixed(0)}% of home games — a tough defense to break down.`);
            if (awayClean > 0.3) lines.push(`${awayName} keep ${(awayClean * 100).toFixed(0)}% clean sheets away from home — solid at the back.`);
            if (homeConcedeAvg > 0 && homeConcedeAvg < 1) lines.push(`${homeName} only concede ${homeConcedeAvg.toFixed(1)} goals per home game on average.`);
            if (awayConcedeAvg > 0 && awayConcedeAvg < 1) lines.push(`${awayName} only concede ${awayConcedeAvg.toFixed(1)} goals per away game.`);
            // Summary combining both attack and defense context
            if (totalAvg > 0) {
                const attackNote = (homeGoalsAvg < 1.5 || awayGoalsAvg < 1.5) ? 'limited attacking output' : 'moderate scoring';
                const defenseNote = (homeClean > 0.3 || awayClean > 0.3 || homeConcedeAvg < 1 || awayConcedeAvg < 1) ? 'strong defensive records' : 'tight matchups';
                lines.push(`The model weighs both ${attackNote} and ${defenseNote} to predict this outcome.`);
            }
        }
        lines.push(`There's a ${pct}% chance this match stays under the line.`);

    // --- Fallback ---
    } else {
        lines.push(`Based on Elo ratings, recent form, and historical data, the model predicts a ${pct}% probability for this outcome.`);
        if (hasStats) {
            if (homeForm > 0) lines.push(`${homeName} form: ${homeForm} pts from 5 games.`);
            if (awayForm > 0) lines.push(`${awayName} form: ${awayForm} pts from 5 games.`);
        }
    }

    // If we have no real data, add a disclaimer
    if (!hasStats && lines.length <= 1) {
        lines.unshift(`Limited historical data available for this matchup.`);
    }

    return lines;
};

const ordinal = (n) => {
    const v = n % 100;
    if (v >= 11 && v <= 13) return 'th';
    const r = n % 10;
    if (r === 1) return 'st';
    if (r === 2) return 'nd';
    if (r === 3) return 'rd';
    return 'th';
};

// Get two recommended bets for a match:
//   - Best Bet: picks from the 60-80% probability "sweet spot" for best value
//     (highest odds within a confident range). Falls back to any bet above 60%.
//   - Safest Bet: the single highest probability bet above 60%.
// If both pick the same bet, an alternative is found for the safest slot.
// Each bet includes a generated reason (array of bullet-point strings).
export const getRecommendedBets = (match) => {
    if (!match.prediction?.probabilities) return null;

    const candidates = collectCandidates(match);
    const qualified = candidates.filter(c => c.prob >= 0.60);

    // --- BEST BET: 60-80% range, pick highest odds (lowest prob) for best value ---
    const sweetSpot = candidates.filter(c => c.prob >= 0.60 && c.prob <= 0.80);
    let bestBet;
    if (sweetSpot.length > 0) {
        sweetSpot.sort((a, b) => a.prob - b.prob);
        bestBet = { ...sweetSpot[0], type: 'best' };
    } else if (qualified.length > 0) {
        qualified.sort((a, b) => a.prob - b.prob);
        bestBet = { ...qualified[0], type: 'best' };
    }

    // --- SAFEST BET: highest probability overall (at least 60%) ---
    let safestBet;
    if (qualified.length > 0) {
        const sorted = [...qualified].sort((a, b) => b.prob - a.prob);
        safestBet = { ...sorted[0], type: 'safest' };
    }

    // Deduplicate: if both picked the same bet, find an alternative for safest
    const used = new Set();
    const result = [];

    for (const bet of [bestBet, safestBet]) {
        if (!bet) continue;
        if (used.has(bet.label)) {
            const alt = candidates
                .filter(c => c.prob >= 0.55 && !used.has(c.label))
                .sort((a, b) => b.prob - a.prob)[0];
            if (alt) {
                result.push({ ...alt, type: bet.type, reason: generateBetReason(alt, match) });
                used.add(alt.label);
            }
        } else {
            result.push({ ...bet, reason: generateBetReason(bet, match) });
            used.add(bet.label);
        }
    }

    return result.length > 0 ? result : null;
};

// Legacy single-bet helper (used by accumulator)
export const getRecommendedBet = (match) => {
    const bets = getRecommendedBets(match);
    return bets ? bets[0] : null;
};

// Calculate accumulator returns
export const calculateAccumulator = (selections, stake) => {
    if (!selections.length || !stake) return { totalOdds: 0, potentialReturn: 0, profit: 0 };
    const totalOdds = selections.reduce((acc, s) => acc * (1 / s.prob), 1);
    const potentialReturn = stake * totalOdds;
    return {
        totalOdds: Math.round(totalOdds * 100) / 100,
        potentialReturn: Math.round(potentialReturn * 100) / 100,
        profit: Math.round((potentialReturn - stake) * 100) / 100,
    };
};
