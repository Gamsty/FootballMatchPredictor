/*
MethodNote — full-page method documentation, replaces the About modal.

Editorial-grade explanation of how the model works, what data feeds it,
what trust signals exist, and where the known limitations are. Sits as a
Dashboard tab so it's deep-linkable + lets the calibration plot render
inline (instead of being trapped in a modal-in-modal layout).

Sections:
  1. Hero + lead — what this is, in one paragraph
  2. Architecture — stacked ensemble, feature engineering, training
  3. Calibration — inline CalibrationView so the reader sees real data
  4. Retraining — nightly Azure job, AUC gate, blob archive
  5. Data sources — football-data.org, NT odds entry, what's NOT included
  6. Known limitations — HT/cards/corners gaps, model bias spots
  7. Disclosure — the standard "informational only" close

Voice: technically precise, calm, direct. First-person plural avoided.
Headings end with the signature accent period.
*/

import { useState, useEffect } from 'react';
import { footballAPI } from '../services/api';

function MethodNote() {
    return (
        <article className="space-y-12 sm:space-y-16 max-w-3xl">
            {/* Hero */}
            <header className="pb-4 border-b border-line">
                <div className="eyebrow mb-2">Method note</div>
                <h2 className="display text-3xl sm:text-5xl text-ink leading-[0.95] font-light">
                    How the model works<span className="text-accent">.</span>
                </h2>
                <p className="text-ink-soft text-base sm:text-lg mt-4 max-w-2xl font-light leading-relaxed">
                    A stacked-ensemble classifier trained on{' '}
                    <span className="text-ink font-medium">40,000+ historical matches</span>{' '}
                    across nine European leagues. Retrained nightly on Azure with
                    AUC validation against the live production model — every
                    candidate must beat the incumbent on a time-based holdout
                    before promotion.
                </p>
            </header>

            {/* Architecture */}
            <Section eyebrow="Architecture" title="Stacked ensemble">
                <p>
                    Two base learners — <span className="text-ink font-medium">XGBoost</span>{' '}
                    and <span className="text-ink font-medium">RandomForest</span> — feed
                    out-of-fold predictions into a{' '}
                    <span className="text-ink font-medium">logistic-regression meta-learner</span>.
                    The meta-learner picks up where each base learner is reliable,
                    weighting them by how their out-of-fold scores correlated with
                    actual outcomes inside each training fold.
                </p>
                <p>
                    The base learners are tuned independently on the same training
                    matrix. The meta-learner sees only their cross-validated
                    predictions, never the raw features — that prevents leakage
                    from the holdout window into the stacked combination.
                </p>
                <Aside>
                    Stacking with TimeSeriesSplit broke in sklearn 1.5+ (the
                    partition-check became stricter). The current pipeline uses
                    StratifiedKFold for the meta-learner CV and keeps the
                    holdout window as a separate post-fit validation step.
                </Aside>
            </Section>

            {/* Features */}
            <Section eyebrow="Features" title="What the model sees">
                <p>
                    ~50 features per match. Computed chronologically so that
                    nothing past the kickoff date leaks back into a row used
                    to predict that match.
                </p>
                <Bulleted items={[
                    <><span className="text-ink font-medium">Elo ratings</span> — both teams' Elo at kickoff time, computed by walking matches in date order with a single-pass update rule</>,
                    <><span className="text-ink font-medium">Form</span> — points and goal difference over the last 5 matches per team</>,
                    <><span className="text-ink font-medium">Head-to-head</span> — wins / draws / losses in recent meetings between the same two clubs</>,
                    <><span className="text-ink font-medium">League position</span> + season progress (matches played as a fraction of season length)</>,
                    <><span className="text-ink font-medium">Rest days</span> — days since each team's last fixture, capped at 14</>,
                    <><span className="text-ink font-medium">Goal averages</span> — scored and conceded per game, home and away splits</>,
                    <><span className="text-ink font-medium">Defensive solidity</span> — clean-sheet rate, average goals conceded</>,
                ]} />
            </Section>

            {/* Calibration — inline */}
            <Section eyebrow="Calibration" title="Probabilities you can trust">
                <p>
                    AUC tells you the model can{' '}
                    <span className="text-ink font-medium">rank</span> matches
                    correctly — it says nothing about whether a 70%-confidence
                    pick actually wins 70% of the time. Overconfidence inflates
                    edges on the Value tab and pushes the operator into fake
                    +EV territory.
                </p>
                <p>
                    The chart below buckets all evaluated predictions by their
                    predicted probability and compares to the actual outcome
                    rate per bucket. Perfect calibration = points on the
                    dashed diagonal. The sample strip underneath shows how
                    much data sits in each bucket — a tight ECE built on
                    n=3 buckets is just noise.
                </p>
                <InlineCalibration />
            </Section>

            {/* Retraining */}
            <Section eyebrow="Retraining" title="Nightly on Azure">
                <p>
                    An Azure Container Apps Job runs at 03:00 UTC every day.
                    Pulls latest match data, recomputes features, trains a
                    candidate stack, evaluates it on the most-recent 30-day
                    holdout, and compares AUC to the production model.
                </p>
                <p>
                    <span className="text-ink font-medium">Promotion gate:</span>{' '}
                    the candidate is only deployed if its holdout AUC beats
                    production within tolerance. Otherwise it's archived in
                    Blob Storage with full metrics + feature importances for
                    later inspection. The previous champion stays serving.
                </p>
                <p>
                    A separate cron snapshots bookmaker closing odds 15
                    minutes before kickoff and applies them to any logged
                    bet on that match — that's how CLV gets computed in the
                    Performance tab.
                </p>
            </Section>

            {/* Data sources */}
            <Section eyebrow="Data" title="What feeds it">
                <Bulleted items={[
                    <><span className="text-ink font-medium">Fixtures + results</span> — football-data.org free tier, synced nightly into PostgreSQL on Azure</>,
                    <><span className="text-ink font-medium">Historical match data</span> — football-data.co.uk CSVs for the training set, including HT scores, cards, and corners (the live API doesn't ship those)</>,
                    <><span className="text-ink font-medium">Bookmaker odds</span> — The Odds API for the consensus + Pinnacle line used in the Value tab</>,
                    <><span className="text-ink font-medium">NT odds</span> — entered manually by the operator on Best Picks and at log time; the only price that actually matters since that's where bets get placed</>,
                ]} />
                <Aside>
                    <span className="text-ink font-medium">Not included:</span>{' '}
                    xG, lineup data, injury reports, weather. Free-tier
                    sources don't expose these. Adding them likely lifts AUC
                    but requires a paid Opta or StatsPerform feed (~$200/mo).
                </Aside>
            </Section>

            {/* Limitations */}
            <Section eyebrow="Limits" title="What it doesn't do">
                <Bulleted items={[
                    <><span className="text-ink font-medium">Halftime / cards / corners markets</span> — the model serves probabilities for these from training data, but the live result feed doesn't ship the underlying stats. Any logged bet on these markets auto-voids if no historical CSV backfills the row first</>,
                    <><span className="text-ink font-medium">Player markets</span> — goal-scorers, cards-per-player, assists. Not modelled, not loggable</>,
                    <><span className="text-ink font-medium">Live in-play prices</span> — predictions reflect pre-match state only. The model freezes at kickoff</>,
                    <><span className="text-ink font-medium">Calibration on rare bins</span> — extreme probabilities (very-near-0 or very-near-1) get few training examples; ECE looks tight in aggregate but specific buckets can drift</>,
                ]} />
            </Section>

            {/* Disclosure */}
            <footer className="pt-8 border-t border-line">
                <p className="mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted leading-relaxed">
                    Predictions are informational. Match outcomes are inherently
                    uncertain. Bet only what you can afford to lose, or simply
                    don't bet at all.
                </p>
            </footer>
        </article>
    );
}

function Section({ eyebrow, title, children }) {
    return (
        <section>
            <div className="eyebrow mb-2">{eyebrow}</div>
            <h3 className="display text-2xl sm:text-3xl text-ink mb-4 leading-tight">
                {title}<span className="text-accent">.</span>
            </h3>
            <div className="space-y-4 text-ink-soft leading-relaxed font-light">
                {children}
            </div>
        </section>
    );
}

function Bulleted({ items }) {
    return (
        <ul className="space-y-2 mt-2">
            {items.map((item, i) => (
                <li key={i} className="flex gap-3 text-sm sm:text-base">
                    <span className="text-accent mt-1.5 shrink-0">·</span>
                    <span>{item}</span>
                </li>
            ))}
        </ul>
    );
}

function Aside({ children }) {
    return (
        <div className="bg-paper-tint border-l-2 border-accent px-4 py-3 text-sm">
            <div className="eyebrow mb-1">Note</div>
            <p className="text-ink-soft leading-relaxed">{children}</p>
        </div>
    );
}

// Inline calibration plot using the same /api/predictions/calibration data
// the modal version uses. Self-contained — no props needed. Renders a
// quieter, smaller variant suited to inline reading flow.
function InlineCalibration() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        const controller = new AbortController();
        footballAPI.getCalibration({ outcome: 'predicted', bins: 10 }, { signal: controller.signal })
            .then(setData)
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(err.message || 'Failed to load calibration');
            })
            .finally(() => setLoading(false));
        return () => controller.abort();
    }, []);

    if (loading) {
        return (
            <div className="bg-paper-tint border border-line p-6 mt-4">
                <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                    Loading calibration data…
                </p>
            </div>
        );
    }
    if (error || !data || data.summary?.total === 0) {
        return (
            <div className="bg-paper-tint border-l-2 border-ink-muted/30 px-4 py-3 mt-4">
                <p className="text-ink-soft text-sm">
                    Calibration data isn't available yet — no predictions have
                    been evaluated against final scores. Comes online
                    automatically after the first weekend of finished matches.
                </p>
            </div>
        );
    }

    const PLOT_SIZE = 320;
    const PLOT_PADDING = 32;
    const x = (v) => PLOT_PADDING + v * (PLOT_SIZE - 2 * PLOT_PADDING);
    const y = (v) => PLOT_SIZE - PLOT_PADDING - v * (PLOT_SIZE - 2 * PLOT_PADDING);
    const points = data.buckets
        .filter(b => b.mean_predicted != null && b.actual_rate != null)
        .map(b => ({
            cx: x(b.mean_predicted),
            cy: y(b.actual_rate),
            r: 3 + Math.sqrt(b.count) * 0.4,
            count: b.count,
            pred: b.mean_predicted,
            actual: b.actual_rate,
        }));
    return (
        <div className="mt-4 space-y-3">
            <div className="border border-line bg-paper-tint/40">
                <svg
                    viewBox={`0 0 ${PLOT_SIZE} ${PLOT_SIZE}`}
                    className="block w-full h-auto"
                    preserveAspectRatio="xMidYMid meet"
                    style={{ aspectRatio: '1 / 1' }}
                >
                    {[0.25, 0.5, 0.75].map(v => (
                        <g key={v}>
                            <line x1={x(v)} y1={y(0)} x2={x(v)} y2={y(1)} stroke="#15110D" strokeOpacity="0.08" strokeDasharray="2 3" />
                            <line x1={x(0)} y1={y(v)} x2={x(1)} y2={y(v)} stroke="#15110D" strokeOpacity="0.08" strokeDasharray="2 3" />
                        </g>
                    ))}
                    <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(0)} stroke="#15110D" strokeOpacity="0.3" />
                    <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} stroke="#15110D" strokeOpacity="0.3" />
                    <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#C0410B" strokeOpacity="0.5" strokeDasharray="4 4" />
                    {points.map((p, i) => (
                        <circle key={i} cx={p.cx} cy={p.cy} r={p.r} fill="#15110D">
                            <title>Predicted {(p.pred * 100).toFixed(0)}% · Actual {(p.actual * 100).toFixed(0)}% · n={p.count}</title>
                        </circle>
                    ))}
                </svg>
            </div>
            <div className="grid grid-cols-3 gap-3 text-sm">
                <Metric label="Sample" value={data.summary.total.toLocaleString()} />
                <Metric label="ECE" value={data.summary.ece?.toFixed(3) ?? '—'} accent={data.summary.ece > 0.05 ? 'warning' : null} />
                <Metric label="Brier" value={data.summary.brier_score?.toFixed(3) ?? '—'} />
            </div>
        </div>
    );
}

function Metric({ label, value, accent }) {
    const cls = accent === 'warning' ? 'text-warning' : 'text-ink';
    return (
        <div className="bg-paper-tint border border-line px-3 py-2">
            <div className="eyebrow">{label}</div>
            <div className={'display text-lg mt-0.5 ' + cls}>{value}</div>
        </div>
    );
}

export default MethodNote;
