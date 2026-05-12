/*
Filter bar — small caps mono chips

Tight uppercase tags rather than candy pill buttons. Active state inverts to ink-on-paper
with a thin border. Mirrors how a sports section flags article tags ("Premier League",
"Match of the day") inline.
*/

import { TAG_CONFIG } from '../utils/constants';

function FilterBar({ filters, onFilterChange }) {
    const activeCategories = filters.categories || [];
    const toggleCategory = (key) => {
        const next = activeCategories.includes(key)
            ? activeCategories.filter(c => c !== key)
            : [...activeCategories, key];
        onFilterChange({ ...filters, categories: next });
    };

    return (
        <div className="flex gap-2 flex-wrap mb-8">
            {Object.entries(TAG_CONFIG).map(([key, config]) => {
                const isActive = activeCategories.includes(key);
                return (
                    <button
                        key={key}
                        onClick={() => toggleCategory(key)}
                        className={
                            "mono text-[0.65rem] uppercase tracking-[0.12em] px-3 py-1.5 border transition-all duration-150 cursor-pointer " +
                            (isActive
                                ? "bg-ink text-paper border-ink"
                                : "bg-transparent text-ink-muted border-line hover:border-ink hover:text-ink-soft")
                        }
                    >
                        {config.label}
                    </button>
                );
            })}

            {activeCategories.length > 0 && (
                <button
                    onClick={() => onFilterChange({ ...filters, categories: [] })}
                    className="mono text-[0.65rem] uppercase tracking-[0.12em] px-3 py-1.5 text-accent
                               hover:text-accent-soft border-b border-accent/30 hover:border-accent-soft
                               transition-colors cursor-pointer"
                >
                    Clear ×
                </button>
            )}
        </div>
    );
}

export default FilterBar;
