/*
ComboPresets — three pre-built combo recommendations derived from the current
top picks.

Why these three:
  - "Safest double"   — top 2 picks weighted by probability. Highest hit rate;
                        lowest variance; suitable when you actually need the
                        combo to land (e.g. building bankroll back from a dip).
  - "Best edge double" — top 2 picks by raw edge. Lower hit rate but higher EV
                        per stake when it does land.
  - "3-leg balanced"  — top 3 picks by Kelly score. Bigger payoff, higher
                        variance; warning shown.

Hard-coded constraints:
  - One leg per match (correlated outcomes would invalidate the independence
    assumption in the multiplicative combo math).
  - Never offer 4+ legs as a preset — bookmaker margin compounds faster than
    realistic model edges can support.

This component reads the same scored picks the parent BestOfWeek already has,
so no extra API calls.
*/

import { useState } from 'react';
import { COMPETITION_LABELS } from '../utils/constants';

const pct = (v) => `${(v * 100).toFixed(1)}%`;
const FRACTIONAL_KELLY = 0.25;
// Mirror of BestOfWeek's SUSPICIOUS_EDGE — anything above 20% against best is
// almost always palp error / model overconfidence on low-prob outcomes. We
// leave these in the singles list (flagged) so users can see them, but they
// don't belong in auto-generated combo recommendations.
const SUSPICIOUS_EDGE = 0.20;

function ComboPresets({ scoredPicks, bankroll, onUseCombo }) {
    const [expanded, setExpanded] = useState(false);

    const presets = buildPresets(scoredPicks);
    if (presets.every(p => p == null)) return null;

    return (
        <section className="mb-8 bg-paper-tint border-l-2 border-accent">
            <button
                onClick={() => setExpanded(e => !e)}
                className="w-full flex items-baseline justify-between px-5 py-3 cursor-pointer text-left hover:bg-paper/40 transition-colors"
                aria-expanded={expanded}
            >
                <div className="flex items-baseline gap-3">
                    <span className="eyebrow">Quick combos</span>
                    <span className="text-ink-soft text-sm">
                        {presets.filter(Boolean).length} auto-generated from this week's top picks
                    </span>
                </div>
                <span className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                    {expanded ? '−' : '+'}
                </span>
            </button>

            {expanded && (
                <div className="px-5 pb-5 grid grid-cols-1 lg:grid-cols-3 gap-3">
                    {presets.map((preset, i) => (
                        preset && (
                            <PresetCard
                                key={i}
                                preset={preset}
                                bankroll={bankroll}
                                onUseCombo={onUseCombo}
                            />
                        )
                    ))}
                </div>
            )}
        </section>
    );
}

function PresetCard({ preset, bankroll, onUseCombo }) {
    const { title, subtitle, legs, comboProb, comboOdds, comboEdge, comboMargin, warning } = preset;
    // ¼-Kelly stake on combined
    const b = comboOdds - 1;
    const kelly = (b > 0 && comboProb > 0)
        ? Math.max(0, (comboProb * b - (1 - comboProb)) / b)
        : 0;
    const stake = bankroll > 0 && kelly > 0
        ? Math.max(1, Math.floor(bankroll * kelly * FRACTIONAL_KELLY))
        : 0;
    const returnNok = stake * comboOdds;

    return (
        <div className="bg-paper border border-line p-4 flex flex-col">
            <div className="mb-3">
                <div className="eyebrow mb-1">{title}</div>
                <div className="text-ink-soft text-xs">{subtitle}</div>
            </div>

            {/* Legs */}
            <div className="space-y-1 mb-3 flex-1">
                {legs.map(leg => (
                    <div key={`${leg.match_id}-${leg.market}-${leg.outcome_key}`} className="text-xs">
                        <div className="text-ink-muted mono text-[0.6rem] uppercase tracking-[0.08em]">
                            {COMPETITION_LABELS[leg.competition] || leg.competition}
                        </div>
                        <div className="text-ink">
                            {leg.home_team?.short_name || leg.home_team?.name}
                            <span className="text-ink-muted mx-1">vs</span>
                            {leg.away_team?.short_name || leg.away_team?.name}
                        </div>
                        <div className="flex items-center justify-between text-ink-soft">
                            <span>{leg.outcome}</span>
                            <span className="mono">{(leg.odds_median ?? leg.odds).toFixed(2)}</span>
                        </div>
                    </div>
                ))}
            </div>

            {/* Combo math */}
            <div className="border-t border-line pt-3 space-y-1.5">
                <Row label="Combined odds" value={comboOdds.toFixed(2)} />
                <Row label="Hit prob" value={pct(comboProb)} />
                <Row
                    label="Edge"
                    value={`${comboEdge >= 0 ? '+' : ''}${pct(comboEdge)}`}
                    valueClass={comboEdge >= 0 ? 'text-positive' : 'text-warning'}
                />
                <Row label="Margin drag" value={`−${pct(comboMargin)}`} valueClass="text-ink-soft" />
                {bankroll > 0 && stake > 0 && (
                    <>
                        <div className="border-t border-line pt-2 mt-2"></div>
                        <Row
                            label="¼-Kelly stake"
                            value={`${stake.toLocaleString()} NOK`}
                            valueClass="text-ink"
                        />
                        <Row
                            label="Potential return"
                            value={`${Math.round(returnNok).toLocaleString()} NOK`}
                            valueClass="text-accent"
                        />
                    </>
                )}
            </div>

            {warning && (
                <div className="mt-3 text-[0.65rem] text-warning leading-relaxed">
                    {warning}
                </div>
            )}

            {onUseCombo && (
                <button
                    onClick={() => onUseCombo(legs)}
                    className="mt-3 mono text-[0.65rem] uppercase tracking-[0.12em] px-3 py-2 border border-line text-ink-soft hover:text-ink hover:border-accent transition-colors cursor-pointer"
                >
                    Load into combo builder →
                </button>
            )}
        </div>
    );
}

function Row({ label, value, valueClass = 'text-ink' }) {
    return (
        <div className="flex items-baseline justify-between">
            <span className="mono text-[0.6rem] uppercase tracking-[0.1em] text-ink-muted">{label}</span>
            <span className={'mono text-xs font-semibold ' + valueClass}>{value}</span>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Preset builders — each takes the scored picks list and returns either a
// combo object or null when there aren't enough qualifying picks.
//
// Constraint: dedupe by match_id (one leg per match — correlated outcomes
// within a fixture violate the independence assumption baked into the
// multiplicative combo math).
// ---------------------------------------------------------------------------

function dedupeByMatch(picks) {
    const seen = new Set();
    const out = [];
    for (const p of picks) {
        if (seen.has(p.match_id)) continue;
        seen.add(p.match_id);
        out.push(p);
    }
    return out;
}

function comboFromLegs(legs) {
    const prob = legs.reduce((a, p) => a * (p.prob ?? 0), 1);
    const odds = legs.reduce((a, p) => a * (p.odds_median ?? p.odds ?? 1), 1);
    const ev = prob * odds - 1;
    const marginFactor = legs.reduce(
        (a, p) => a * (p.overround_median ?? p.overround_best ?? 1), 1
    );
    const margin = marginFactor - 1;
    return { comboProb: prob, comboOdds: odds, comboEdge: ev, comboMargin: margin };
}

// Edge cases handled (verified by reading + manual UI test, not a unit test —
// frontend has no Vitest/Jest setup and adding it for these branches is
// overkill):
//   - scoredPicks empty/undefined          → [null, null, null] (caller hides)
//   - <2 picks after edge<20% filter       → [null, null, null]
//   - 2 picks: safest + bestEdge populated, triple = null
//   - 3+ picks after dedupe by match       → all three slots populated
function buildPresets(scoredPicks) {
    if (!scoredPicks || scoredPicks.length < 2) return [null, null, null];

    // Drop suspicious-edge picks before building any preset — those are the
    // exact picks that would headline a "Best Edge Double" but are almost
    // certainly fake edges. We'd rather show no preset than recommend a palp.
    const safe = scoredPicks.filter(
        p => (p.edge_best ?? p.edge ?? 0) < SUSPICIOUS_EDGE
    );

    // Pool: dedupe by match first, then rerank per preset.
    const pool = dedupeByMatch([...safe].sort((a, b) => b._score - a._score));

    // Safest double — top 2 by probability (we still require they cleared the
    // 2% edge gate to even be in the pool).
    const safestPool = [...pool].sort((a, b) => (b.prob ?? 0) - (a.prob ?? 0));
    const safest = safestPool.length >= 2 ? {
        title: 'Safest double',
        subtitle: 'Top 2 by hit probability',
        legs: safestPool.slice(0, 2),
        ...comboFromLegs(safestPool.slice(0, 2)),
    } : null;

    // Best edge double — top 2 by raw edge_median.
    const edgePool = [...pool].sort(
        (a, b) => (b.edge_median ?? b.edge ?? 0) - (a.edge_median ?? a.edge ?? 0)
    );
    const bestEdge = edgePool.length >= 2 ? {
        title: 'Best edge double',
        subtitle: 'Top 2 by sharp edge',
        legs: edgePool.slice(0, 2),
        ...comboFromLegs(edgePool.slice(0, 2)),
    } : null;

    // 3-leg balanced — top 3 by composite ¼-Kelly × agreement score.
    const tripleLegs = pool.slice(0, 3);
    const triple = tripleLegs.length >= 3 ? {
        title: '3-leg balanced',
        subtitle: 'Top 3 by Kelly × agreement',
        legs: tripleLegs,
        warning: 'Three-leg combo: hit probability drops below 30% even on strong favorites. Higher payoff, much higher variance.',
        ...comboFromLegs(tripleLegs),
    } : null;

    return [safest, bestEdge, triple];
}

export default ComboPresets;
