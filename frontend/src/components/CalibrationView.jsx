/*
CalibrationView — model calibration plot.

Bucket all evaluated predictions by their predicted probability and compare
to actual outcome rates. Perfect calibration = points on the y=x diagonal.

Why this matters: AUC tells you the model can rank matches correctly, but it
says nothing about whether 70%-confidence picks actually win 70% of the time.
Overconfidence inflates the "edges" computed in /api/value-bets — a model that
says 70% but only wins 55% will systematically chase fake value. ECE
(Expected Calibration Error) is the single number that captures this.
*/

import { useState, useEffect } from 'react';
import { footballAPI, describeApiError } from '../services/api';
import { useModalDismiss } from '../hooks/useModalDismiss';

const OUTCOMES = [
    { id: 'predicted', label: 'Predicted side',
      hint: 'Calibration on the side the model picked (its confidence outcome).' },
    { id: 'home_win',  label: 'Home win',
      hint: 'Calibration of home_win_prob across all matches.' },
    { id: 'draw',      label: 'Draw',
      hint: 'Calibration of draw_prob — usually the worst-calibrated, draws are hard.' },
    { id: 'away_win',  label: 'Away win',
      hint: 'Calibration of away_win_prob across all matches.' },
];

const PLOT_SIZE = 320;
const PLOT_PADDING = 32;

function CalibrationView({ onClose }) {
    // Escape closes the topmost dialog only — see the hook for why that matters
    // when a log-bet modal is stacked over a detail view.
    useModalDismiss(onClose);
    const [outcome, setOutcome] = useState('predicted');
    // '' = all model versions (default). Otherwise restricts to a specific version
    // so we don't average over v1 + v2 predictions after a retrain.
    const [modelVersion, setModelVersion] = useState('');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // Reset for a new request DURING render, not inside the effect. Setting
    // state in an effect body schedules a second render pass for something React
    // can apply immediately, and react-hooks/set-state-in-effect flags it. The
    // key-comparison form below is React's documented way to adjust state when
    // inputs change.
    const calibrationKey = `${outcome}|${modelVersion}`;
    const [requestKey, setRequestKey] = useState(calibrationKey);
    if (requestKey !== calibrationKey) {
        setRequestKey(calibrationKey);
        setLoading(true);
        setError(null);
    }

    useEffect(() => {
        const controller = new AbortController();
        const params = { outcome, bins: 10 };
        if (modelVersion) params.model_version = modelVersion;
        footballAPI.getCalibration(params, { signal: controller.signal })
            .then(res => setData(res))
            .catch(err => {
                if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return;
                setError(describeApiError(err, 'Failed to load calibration'));
            })
            .finally(() => setLoading(false));
        return () => controller.abort();
    }, [outcome, modelVersion]);

    const versionsAvailable = data?.summary?.model_versions_available || [];

    return (
        <div
            role="dialog"
            aria-modal="true"
            aria-label="Model calibration"
            className="fixed inset-0 z-50 bg-ink/60 flex items-stretch sm:items-center justify-center sm:p-4 overflow-y-auto"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-paper w-full max-w-2xl sm:my-8 border-y sm:border border-line">
                <div className="flex items-start justify-between px-4 sm:px-6 py-4 border-b border-line">
                    <div>
                        <div className="eyebrow mb-1">Diagnostic</div>
                        <h2 className="display text-2xl sm:text-3xl text-ink">
                            Model calibration<span className="text-accent">.</span>
                        </h2>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-ink-muted hover:text-ink text-2xl leading-none cursor-pointer p-2 -m-2"
                        aria-label="Close"
                    >×</button>
                </div>

                <div className="px-4 sm:px-6 py-5 sm:py-6 space-y-5">
                    {/* Outcome picker — px-3 py-2 on mobile for thumb targets */}
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="eyebrow">Outcome</span>
                        {OUTCOMES.map(o => (
                            <button
                                key={o.id}
                                onClick={() => setOutcome(o.id)}
                                title={o.hint}
                                className={
                                    'mono text-[0.7rem] uppercase tracking-[0.1em] px-3 py-2 sm:px-2 sm:py-1 border transition-colors cursor-pointer ' +
                                    (outcome === o.id
                                        ? 'bg-ink text-paper border-ink'
                                        : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                                }
                            >
                                {o.label}
                            </button>
                        ))}
                    </div>

                    {/* Model-version filter — shown only when there's more than one
                        version with evaluated predictions. Mixing versions makes the
                        calibration plot less meaningful, so this is the recommended
                        way to look at a specific deployed model in isolation. */}
                    {versionsAvailable.length > 1 && (
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="eyebrow">Model version</span>
                            <button
                                onClick={() => setModelVersion('')}
                                className={
                                    'mono text-[0.7rem] uppercase tracking-[0.1em] px-2 py-1 border transition-colors cursor-pointer ' +
                                    (modelVersion === ''
                                        ? 'bg-ink text-paper border-ink'
                                        : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                                }
                            >
                                All ({data.summary.total})
                            </button>
                            {versionsAvailable.map(v => (
                                <button
                                    key={v}
                                    onClick={() => setModelVersion(v)}
                                    className={
                                        'mono text-[0.7rem] tracking-[0.05em] px-2 py-1 border transition-colors cursor-pointer ' +
                                        (modelVersion === v
                                            ? 'bg-ink text-paper border-ink'
                                            : 'border-line text-ink-soft hover:text-ink hover:border-ink-muted')
                                    }
                                >
                                    {v}
                                </button>
                            ))}
                        </div>
                    )}

                    {loading && (
                        <p className="mono text-[0.7rem] uppercase tracking-[0.15em] text-ink-muted">
                            Computing calibration…
                        </p>
                    )}

                    {error && (
                        <div className="bg-paper-tint border-l-2 border-danger p-4">
                            <p className="text-ink-soft text-sm">{error}</p>
                        </div>
                    )}

                    {data && !loading && (
                        <>
                            {data.summary?.total === 0 ? (
                                <div className="bg-paper-tint border-l-2 border-accent p-5">
                                    <div className="eyebrow mb-2">No data yet</div>
                                    <p className="text-ink-soft text-sm">
                                        Predictions are persisted with timestamps now, but no matches have
                                        been evaluated yet. Calibration unlocks once historical predictions
                                        have results filled in (happens automatically after kickoff +
                                        outcome sync).
                                    </p>
                                </div>
                            ) : (
                                <>
                                    <CalibrationPlot buckets={data.buckets} />
                                    <SampleStrip buckets={data.buckets} />
                                    <Summary summary={data.summary} />
                                    <CalibratorBanner calibrator={data.summary?.calibrator} />
                                </>
                            )}
                        </>
                    )}

                    {/* Legend */}
                    <div className="mono text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted leading-relaxed border-t border-line pt-4">
                        <p>
                            ECE (Expected Calibration Error): bucket-weighted |actual − predicted|.
                            Lower is better; perfectly calibrated = 0.
                        </p>
                        <p className="mt-1">
                            Brier score: mean squared error vs 0/1 outcome. Combines calibration and
                            sharpness — used for absolute model comparison.
                        </p>
                    </div>
                </div>
            </div>
        </div>
    );
}

function CalibrationPlot({ buckets }) {
    // Plot coords: x = mean_predicted, y = actual_rate. Diagonal y=x is "perfect".
    const x = (v) => PLOT_PADDING + v * (PLOT_SIZE - 2 * PLOT_PADDING);
    const y = (v) => PLOT_SIZE - PLOT_PADDING - v * (PLOT_SIZE - 2 * PLOT_PADDING);

    // Walk buckets in order; nulls become "lift the pen" — the connector path
    // shouldn't bridge across empty buckets (would imply false interpolation).
    const points = buckets
        .filter(b => b.mean_predicted != null && b.actual_rate != null)
        .map(b => ({
            cx: x(b.mean_predicted),
            cy: y(b.actual_rate),
            r: 3 + Math.sqrt(b.count) * 0.4,
            count: b.count,
            pred: b.mean_predicted,
            actual: b.actual_rate,
        }));

    // Build path segments — start a new "M" each time we resume after a gap.
    // We detect gaps by walking the original buckets list and noting consecutive
    // populated entries.
    const pathSegments = [];
    let segment = [];
    for (const b of buckets) {
        if (b.mean_predicted != null && b.actual_rate != null) {
            segment.push({ cx: x(b.mean_predicted), cy: y(b.actual_rate) });
        } else if (segment.length) {
            pathSegments.push(segment);
            segment = [];
        }
    }
    if (segment.length) pathSegments.push(segment);
    const path = pathSegments
        .map(seg => seg.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.cx} ${p.cy}`).join(' '))
        .join(' ');

    return (
        // viewBox + 100% width = scales to container; height stays square via
        // aspect-ratio so the diagonal stays a true 45° on any screen width.
        <div className="border border-line bg-paper-tint/40">
            <svg
                viewBox={`0 0 ${PLOT_SIZE} ${PLOT_SIZE}`}
                className="block w-full h-auto"
                preserveAspectRatio="xMidYMid meet"
                style={{ aspectRatio: '1 / 1' }}
            >
                {/* Grid */}
                {[0.25, 0.5, 0.75].map(v => (
                    <g key={v}>
                        <line x1={x(v)} y1={y(0)} x2={x(v)} y2={y(1)} stroke="#15110D" strokeOpacity="0.08" strokeDasharray="2 3" />
                        <line x1={x(0)} y1={y(v)} x2={x(1)} y2={y(v)} stroke="#15110D" strokeOpacity="0.08" strokeDasharray="2 3" />
                    </g>
                ))}
                {/* Axes */}
                <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(0)} stroke="#15110D" strokeOpacity="0.3" />
                <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} stroke="#15110D" strokeOpacity="0.3" />
                {/* Perfect-calibration diagonal */}
                <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)}
                      stroke="#C0410B" strokeOpacity="0.5" strokeDasharray="4 4" />
                {/* Connector */}
                <path d={path} stroke="#15110D" strokeWidth="1.5" fill="none" />
                {/* Bucket points — area proportional to sample size */}
                {points.map((p, i) => (
                    <circle key={i} cx={p.cx} cy={p.cy} r={p.r} fill="#15110D">
                        <title>
                            Predicted {(p.pred * 100).toFixed(0)}% · Actual {(p.actual * 100).toFixed(0)}% · n={p.count}
                        </title>
                    </circle>
                ))}
                {/* Axis labels */}
                <text x={x(0.5)} y={PLOT_SIZE - 6} textAnchor="middle"
                      fontFamily="JetBrains Mono, monospace" fontSize="10" fill="#15110D" fillOpacity="0.5">
                    PREDICTED PROBABILITY
                </text>
                <text x={10} y={y(0.5)} textAnchor="middle"
                      transform={`rotate(-90 10 ${y(0.5)})`}
                      fontFamily="JetBrains Mono, monospace" fontSize="10" fill="#15110D" fillOpacity="0.5">
                    ACTUAL RATE
                </text>
            </svg>
        </div>
    );
}

// Horizontal strip of hairline bars showing relative sample size per bucket.
// Sits under the calibration plot so the reader can immediately see which
// probability ranges have enough data to trust — a tight ECE built on n=3
// samples is just noise.
function SampleStrip({ buckets }) {
    const maxCount = Math.max(1, ...buckets.map(b => b.count || 0));
    return (
        <div>
            <div className="flex gap-px h-6 bg-paper-tint border border-line">
                {buckets.map((b, i) => {
                    const h = b.count ? (b.count / maxCount) * 100 : 0;
                    return (
                        <div
                            key={i}
                            className="flex-1 flex items-end"
                            title={`Bucket ${(i / buckets.length * 100).toFixed(0)}–${((i + 1) / buckets.length * 100).toFixed(0)}%: n=${b.count || 0}`}
                        >
                            <div
                                className="w-full bg-ink"
                                style={{ height: `${h}%` }}
                            />
                        </div>
                    );
                })}
            </div>
            <div className="mono text-[0.55rem] uppercase tracking-[0.12em] text-ink-muted mt-1.5 flex justify-between">
                <span>0%</span>
                <span>samples per bucket — bigger = more data</span>
                <span>100%</span>
            </div>
        </div>
    );
}

function Summary({ summary }) {
    return (
        <div className="grid grid-cols-3 gap-3">
            <div className="bg-paper-tint border border-line px-3 py-2.5">
                <div className="eyebrow">Sample</div>
                <div className="display text-xl text-ink mt-0.5">{summary.total.toLocaleString()}</div>
            </div>
            <div className="bg-paper-tint border border-line px-3 py-2.5">
                <div className="eyebrow">ECE</div>
                <div className={'display text-xl mt-0.5 ' + (summary.ece > 0.05 ? 'text-warning' : 'text-ink')}>
                    {summary.ece?.toFixed(3) ?? '—'}
                </div>
            </div>
            <div className="bg-paper-tint border border-line px-3 py-2.5">
                <div className="eyebrow">Brier</div>
                <div className="display text-xl text-ink mt-0.5">
                    {summary.brier_score?.toFixed(3) ?? '—'}
                </div>
            </div>
        </div>
    );
}

function CalibratorBanner({ calibrator }) {
    if (!calibrator) {
        return (
            <div className="bg-paper-tint border-l-2 border-ink-muted/30 px-4 py-3">
                <div className="eyebrow mb-1">Calibrator</div>
                <p className="text-ink-soft text-sm">
                    No temperature calibrator is loaded. Predictions reflect the model's raw output —
                    expect ECE to drift up at the extremes. Run{' '}
                    <span className="mono text-xs bg-paper px-1.5 py-0.5 border border-line">jobs/fit_calibration.py</span>{' '}
                    or POST to{' '}
                    <span className="mono text-xs bg-paper px-1.5 py-0.5 border border-line">/api/admin/refit-calibration</span>{' '}
                    to learn T from current data.
                </p>
            </div>
        );
    }
    const t = calibrator.temperature;
    const direction = t < 0.95 ? 'sharpening' : t > 1.05 ? 'softening' : 'identity (no-op)';
    return (
        <div className="bg-paper-tint border-l-2 border-accent px-4 py-3">
            <div className="eyebrow mb-1">Calibrator applied</div>
            <p className="text-ink-soft text-sm">
                Temperature{' '}
                <span className="mono text-ink">T = {t.toFixed(3)}</span>{' '}
                ({direction}), fitted on{' '}
                <span className="mono text-ink">{calibrator.fit_samples?.toLocaleString() ?? '?'}</span>{' '}
                samples. Probabilities shown above are post-calibration. Re-fit after retraining the model.
            </p>
        </div>
    );
}

export default CalibrationView;
