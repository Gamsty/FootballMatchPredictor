/*
Constants and utility functions used across frontend components
*/

// Maps API outcome keys (HOME_WIN, DRAW, AWAY_WIN) to display-friendly labels
export const OUTCOME_LABELS = {
    HOME_WIN: 'HOME WIN',
    DRAW: 'DRAW',
    AWAY_WIN: 'AWAY_WIN'
};

// Converts a decimal probability (0.575) to a percentage string ("57.5%")
export const formatPercentage = (value) => {
    return `${(value * 100).toFixed(1)}%`;
};

// Converts ISO date string ("2026-03-05T21:00:00") to readable format ("Mar 5, 2026")
export const formatDate = (dateString) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
};

// Returns a Tailwind label class based on prediction confidence level
export const getConfidenceLabel = (confidence) => {
  if (confidence >= 0.7) return 'High';
  if (confidence >= 0.5) return 'Medium';
  return 'Low';
};

// Returns a Tailwind background color class based on prediction confidence level
// Green = high confidence (60%+), Amber = medium (45-60%), Red = low (<45%)
export const getConfidenceColor = (confidence) => {
    if (confidence >= 0.6) return 'bg-green-500';
    if (confidence >= 0.45) return 'bg-amber-500';
    return 'bg-red-500';
};