/*
Category Tabs Component

Top-level navigation tabs for the dashboard:
- Today's Matches (default)
- Upcoming
- Top Picks (high confidence)
*/

function CategoryTabs({ activeTab, onTabChange, matchCounts }) {
    const tabs = [
        { id: 'today', label: "Today", icon: '📅', count: matchCounts.today },
        { id: 'upcoming', label: 'Upcoming', icon: '🗓️', count: matchCounts.upcoming },
    ];

    return (
        <div className="flex gap-1.5 mb-4 overflow-x-auto pb-1">
            {tabs.map(tab => (
                <button
                    key={tab.id}
                    onClick={() => onTabChange(tab.id)}
                    className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium
                              transition-all duration-150 whitespace-nowrap
                              ${activeTab === tab.id
                                  ? 'bg-blue-600 text-white'
                                  : 'bg-gray-800/50 text-gray-500 hover:bg-gray-800 hover:text-gray-300'
                              }`}
                >
                    <span>{tab.icon}</span>
                    <span>{tab.label}</span>
                    {tab.count > 0 && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold
                            ${activeTab === tab.id
                                ? 'bg-white/20 text-white'
                                : 'bg-gray-700/50 text-gray-500'
                            }`}
                        >
                            {tab.count}
                        </span>
                    )}
                </button>
            ))}
        </div>
    );
}

export default CategoryTabs;
