/*
Filter Bar Component

Category tag filters (multi-select): High Confidence, Upset Pick, Banker, Top League.
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
        <div className="flex gap-1.5 flex-wrap mb-6">
            {Object.entries(TAG_CONFIG).map(([key, config]) => {
                const isActive = activeCategories.includes(key);
                return (
                    <button
                        key={key}
                        onClick={() => toggleCategory(key)}
                        className={`text-sm px-5 py-2 rounded-full border font-medium transition-all duration-150
                            ${isActive
                                ? 'bg-blue-500/15 text-blue-300 border-blue-500/30'
                                : 'bg-gray-800/50 text-gray-500 border-gray-700/40 hover:text-gray-400 hover:bg-gray-800'
                            }`}
                    >
                        {config.label}
                    </button>
                );
            })}

            {activeCategories.length > 0 && (
                <button
                    onClick={() => onFilterChange({ ...filters, categories: [] })}
                    className="text-sm px-5 py-2 rounded-full border border-gray-700/40 text-gray-600
                             hover:text-gray-400 transition-all duration-150"
                >
                    Clear
                </button>
            )}
        </div>
    );
}

export default FilterBar;
