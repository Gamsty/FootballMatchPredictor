/*
Category tabs — underlined nav, not pill buttons

Behaves like a magazine section nav (Today / Upcoming) rather than a UI-kit segmented
control. Active tab gets the ink-underline; inactive is just muted text. Counts are
mono numerals sitting next to each label.
*/

function CategoryTabs({ activeTab, onTabChange, matchCounts, showValueTab = false }) {
    const tabs = [
        { id: 'today',    label: 'Today',    count: matchCounts.today },
        { id: 'upcoming', label: 'Upcoming', count: matchCounts.upcoming },
        // Value tab — only shown in advanced mode. Count is undefined-safe.
        ...(showValueTab ? [
            { id: 'value', label: 'Value', count: matchCounts.value, accent: true },
        ] : []),
    ];

    return (
        <div className="flex gap-6 mb-6 border-b border-line overflow-x-auto">
            {tabs.map(tab => {
                const isActive = activeTab === tab.id;
                return (
                    <button
                        key={tab.id}
                        onClick={() => onTabChange(tab.id)}
                        className={
                            "relative flex items-baseline gap-2 pb-3 transition-colors duration-200 cursor-pointer " +
                            (isActive
                                ? "text-ink"
                                : "text-ink-muted hover:text-ink-soft")
                        }
                    >
                        <span className="display text-lg">{tab.label}</span>
                        {tab.count !== undefined && (
                            <span className="mono text-xs text-ink-muted">{tab.count}</span>
                        )}
                        {tab.accent && !isActive && (
                            <span className="w-1.5 h-1.5 bg-accent rounded-full ml-0.5" aria-hidden="true" />
                        )}
                        {isActive && (
                            <span
                                className="absolute left-0 right-0 bottom-[-1px] h-[2px] bg-accent"
                                aria-hidden="true"
                            />
                        )}
                    </button>
                );
            })}
        </div>
    );
}

export default CategoryTabs;
