/*
BottomNav — mobile-only fixed bottom navigation.

Shows on viewports below the sm breakpoint (640px). The desktop CategoryTabs
component continues to handle nav above that. Carries the four primary
destinations — Today, Upcoming, Best Picks, Performance — chosen because
they cover the daily flow of an operator (check today's slate → confirm via
best picks → log via accumulator → review in performance).

Secondary destinations (Method, Settings, Value) stay accessible via the
meta-links bar in the hero — adding them here would crowd the row.

Skill compliance:
  - No rounded corners (rectangular tap targets)
  - No icons — text labels only, mono uppercase with brand tracking
  - Hairline 1px top border defining the bar
  - Paper-tint background, ink text, accent for active marker
  - Safe-area padding for iPhone home-indicator
*/

const TABS = [
    { id: 'today',       label: 'Today' },
    { id: 'upcoming',    label: 'Upcoming' },
    { id: 'best',        label: 'Best' },
    { id: 'performance', label: 'Perf' },
];

function BottomNav({ activeTab, onTabChange, matchCounts }) {
    return (
        <nav
            className="sm:hidden fixed bottom-0 left-0 right-0 z-40 bg-paper-tint border-t border-line"
            style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
            aria-label="Primary navigation"
        >
            <div className="grid grid-cols-4">
                {TABS.map(tab => {
                    const isActive = activeTab === tab.id;
                    const count = matchCounts?.[tab.id];
                    return (
                        <button
                            key={tab.id}
                            onClick={() => onTabChange(tab.id)}
                            className={
                                'relative flex flex-col items-center justify-center gap-0.5 py-3 mono text-[0.65rem] uppercase tracking-[0.1em] transition-colors cursor-pointer ' +
                                (isActive
                                    ? 'text-ink'
                                    : 'text-ink-muted hover:text-ink-soft')
                            }
                        >
                            <span>{tab.label}</span>
                            {count !== undefined && count > 0 && (
                                <span className="text-[0.55rem] text-ink-muted">{count}</span>
                            )}
                            {isActive && (
                                <span
                                    className="absolute top-0 left-0 right-0 h-[2px] bg-accent"
                                    aria-hidden="true"
                                />
                            )}
                        </button>
                    );
                })}
            </div>
        </nav>
    );
}

export default BottomNav;
