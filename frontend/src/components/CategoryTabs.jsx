/*
Category tabs — underlined nav, not pill buttons

Behaves like a magazine section nav (Today / Upcoming) rather than a UI-kit segmented
control. Active tab gets the ink-underline; inactive is just muted text. Counts are
mono numerals sitting next to each label.
*/

function CategoryTabs({ activeTab, onTabChange, matchCounts }) {
    const tabs = [
        { id: 'today',    label: 'Today',    count: matchCounts.today },
        { id: 'upcoming', label: 'Upcoming', count: matchCounts.upcoming },
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
                        <span className="mono text-xs text-ink-muted">{tab.count}</span>
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
