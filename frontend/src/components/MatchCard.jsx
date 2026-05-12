/*
Match Card — Editorial pitch report

Each card reads like a one-paragraph match preview: kickoff time top-left, confidence
chip top-right, teams listed with probabilities, a thin H-D-A bar underneath, and the
two recommended bets (best edge + safest pick) called out below. Borrows from
newspaper sports columns rather than betting-site card components — flat, rule-based,
high information density, no rounded corners.

Accumulator UX: each bet has an inline +/✓ button on the right. Clicking + adds it to
the slip; clicking ✓ removes it. Switching between Best and Safest on the same match
swaps which one's in the slip (one bet per match, by design).
*/

import { useState } from 'react';
import {
    formatTime, formatMatchDate, formatPercentage, getConfidenceColor, getConfidenceLabel,
    getRecommendedBets, formatOdds, isToday
} from '../utils/constants';

const TYPE_LABELS = { best: 'Best bet', safest: 'Safest bet' };

// Confidence chip — traffic-light hierarchy tuned to the portfolio palette:
//   HIGH    → positive (forest green)
//   MEDIUM  → warning  (saffron amber, distinct from accent burnt-orange)
//   LOW     → danger   (saturated terracotta red)
// Using warning instead of accent for MEDIUM keeps the chip clearly differentiated
// from the orange used elsewhere on the page (How it works, accumulator border).
// Fallback to ink-soft for any unknown bucket if constants.js adds tiers later.
// Map whatever getConfidenceColor() returns to our paper-palette equivalents.
// constants.js currently returns bg-green-500 / bg-amber-500 / bg-red-500, but
// we cover a few alternative spellings (yellow/orange/emerald) in case the
// thresholds ever get retuned to use Tailwind's other warm-tone scales.
const CHIP_COLORS = {
    'bg-green-500':   'bg-positive text-paper',
    'bg-emerald-500': 'bg-positive text-paper',
    'bg-amber-500':   'bg-warning text-paper',
    'bg-yellow-500':  'bg-warning text-paper',
    'bg-orange-500':  'bg-warning text-paper',
    'bg-red-500':     'bg-danger text-paper',
    'bg-rose-500':    'bg-danger text-paper',
};

function chipClass(originalColor) {
    return CHIP_COLORS[originalColor] || 'bg-ink-soft text-paper';
}

function BetToggle({ active, onClick, label }) {
    return (
        <button
            onClick={(e) => { e.stopPropagation(); onClick(); }}
            title={active ? `Remove "${label}" from slip` : `Add "${label}" to slip`}
            aria-label={active ? `Remove ${label} from slip` : `Add ${label} to slip`}
            className={
                // 30px hit target — above WCAG minimum, balanced against the mono
                // numerals next to it. Subtle scale on hover for tactile feedback.
                "shrink-0 w-[30px] h-[30px] flex items-center justify-center border transition-all duration-150 cursor-pointer mono text-sm leading-none " +
                (active
                    ? "bg-accent text-paper border-accent hover:bg-accent-soft"
                    : "bg-transparent text-ink-muted border-line hover:text-accent hover:border-accent hover:bg-paper-tint")
            }
        >
            {active ? '✓' : '+'}
        </button>
    );
}

function MatchCard({ match, onClick, selectedBet, onToggleAccumulator }) {
    const [expandedReason, setExpandedReason] = useState(null);
    const prediction = match.prediction;
    const hasPrediction = prediction && prediction.probabilities;
    const recommendedBets = getRecommendedBets(match);

    // Draw label position: align horizontally to the midpoint of the draw segment,
    // but clamp to the middle 70% of the bar so it never collides with Home/Away labels.
    let drawLeftPct = 50;
    if (hasPrediction) {
        const homePct = prediction.probabilities.home_win * 100;
        const drawPct = prediction.probabilities.draw * 100;
        drawLeftPct = Math.min(82, Math.max(18, homePct + drawPct / 2));
    }

    return (
        <article
            onClick={() => onClick(match)}
            className="group bg-paper border border-line p-5 cursor-pointer
                       transition-all duration-200
                       hover:border-ink/40 hover:shadow-[0_6px_24px_-12px_rgba(21,17,13,0.18)]"
        >
            {/* Header: kickoff + confidence chip */}
            <div className="flex justify-between items-baseline mb-4">
                <span className="mono text-[0.7rem] uppercase tracking-[0.1em] text-ink-muted">
                    {isToday(match.date) ? formatTime(match.date) : `${formatMatchDate(match.date)} · ${formatTime(match.date)}`}
                </span>
                {hasPrediction && (
                    <span className={`${chipClass(getConfidenceColor(prediction.confidence))} mono text-[0.6rem] uppercase tracking-[0.12em] px-2 py-0.5`}>
                        {getConfidenceLabel(prediction.confidence)}
                    </span>
                )}
            </div>

            {/* Teams + Probabilities */}
            <div className="mb-3 space-y-1.5">
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-2.5 truncate max-w-[70%]">
                        {match.home_team.crest && (
                            <img src={match.home_team.crest} alt="" className="w-5 h-5 object-contain shrink-0" />
                        )}
                        <span className="display text-base text-ink truncate">
                            {match.home_team.short_name || match.home_team.name}
                        </span>
                    </div>
                    {hasPrediction && (
                        <span className="mono text-sm text-ink font-medium">
                            {formatPercentage(prediction.probabilities.home_win)}
                        </span>
                    )}
                </div>
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-2.5 truncate max-w-[70%]">
                        {match.away_team.crest && (
                            <img src={match.away_team.crest} alt="" className="w-5 h-5 object-contain shrink-0" />
                        )}
                        <span className="display text-base text-ink-soft truncate">
                            {match.away_team.short_name || match.away_team.name}
                        </span>
                    </div>
                    {hasPrediction && (
                        <span className="mono text-sm text-ink-soft font-medium">
                            {formatPercentage(prediction.probabilities.away_win)}
                        </span>
                    )}
                </div>
            </div>

            {/* Probability bar — three thin bands with the Draw label aligned to
                the actual draw segment, not statically centered. */}
            {hasPrediction && (
                <div className="mb-4">
                    <div className="flex h-[3px] bg-line">
                        <div className="bg-ink transition-all duration-500"
                            style={{ width: `${prediction.probabilities.home_win * 100}%` }} />
                        <div className="bg-ink-muted/40 transition-all duration-500"
                            style={{ width: `${prediction.probabilities.draw * 100}%` }} />
                        <div className="bg-accent-soft transition-all duration-500"
                            style={{ width: `${prediction.probabilities.away_win * 100}%` }} />
                    </div>
                    <div className="relative mt-1.5 h-3 mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted">
                        <span className="absolute left-0">Home</span>
                        <span
                            className="absolute -translate-x-1/2 whitespace-nowrap"
                            style={{ left: `${drawLeftPct}%` }}
                        >
                            Draw {formatPercentage(prediction.probabilities.draw)}
                        </span>
                        <span className="absolute right-0">Away</span>
                    </div>
                </div>
            )}

            {/* Recommended bets — each row has an inline + button to add to the slip.
                Removes the extra row of buttons at the bottom of the card. */}
            {recommendedBets && recommendedBets.length > 0 && (
                <div className="space-y-3 border-t border-line pt-3">
                    {recommendedBets.map((bet) => {
                        const isExpanded = expandedReason === bet.type;
                        const isSelected = selectedBet === bet.label;
                        return (
                            <div key={bet.type}>
                                <div className="flex items-baseline justify-between gap-3 mb-1">
                                    <span className="mono text-[0.6rem] uppercase tracking-[0.12em] text-accent">
                                        {TYPE_LABELS[bet.type]}
                                    </span>
                                    <span className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted">
                                        @ {formatOdds(bet.prob)}
                                    </span>
                                </div>
                                <div className="flex items-center justify-between gap-3">
                                    <span className="display text-base text-ink truncate">{bet.label}</span>
                                    <div className="flex items-center gap-2 shrink-0">
                                        <span className="mono text-sm text-ink font-medium">
                                            {formatPercentage(bet.prob)}
                                        </span>
                                        {onToggleAccumulator && (
                                            <BetToggle
                                                active={isSelected}
                                                onClick={() => onToggleAccumulator(match, bet)}
                                                label={bet.label}
                                            />
                                        )}
                                    </div>
                                </div>
                                {bet.reason && (
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            setExpandedReason(isExpanded ? null : bet.type);
                                        }}
                                        className="mono text-[0.6rem] uppercase tracking-[0.12em] text-ink-muted hover:text-accent mt-1.5 transition-colors cursor-pointer"
                                    >
                                        {isExpanded ? '— hide reasoning' : '+ why this bet'}
                                    </button>
                                )}
                                {isExpanded && bet.reason && (
                                    <ul className="mt-2 space-y-1">
                                        {bet.reason.map((line, i) => (
                                            <li key={i} className="flex gap-2 text-xs text-ink-soft leading-snug">
                                                <span className="text-accent mt-0.5 shrink-0">·</span>
                                                <span>{line}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </article>
    );
}

export default MatchCard;
