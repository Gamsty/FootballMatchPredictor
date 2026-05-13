"""
Odds-coverage diagnostic — answers "do my upcoming fixtures actually resolve
to The Odds API events?"

For every upcoming match in the DB across the next N days, attempt to resolve
bookmaker odds and print a coverage report. Unmatched fixtures get a "miss"
row showing the team names we sent in, so you can extend TEAM_ALIASES in
odds_api.py for any that consistently fail.

Usage (from backend/):
    # Requires DATABASE_URL + ODDS_API_KEY in env (or .env)
    python jobs/check_odds_coverage.py
    python jobs/check_odds_coverage.py --days 7 --verbose

Read-only — does NOT call the prediction model, does NOT write anywhere.
Costs ~1 request per league with upcoming fixtures (cached, so re-runs are free).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make src/ importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database import DatabaseManager  # noqa: E402
from odds_api import OddsAPIClient, SPORT_KEY_MAP  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=7,
                        help='How many days ahead to check (default 7)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print every fixture, not just misses')
    args = parser.parse_args()

    client = OddsAPIClient()
    if not client.enabled:
        print('ERROR: ODDS_API_KEY not set. Add it to your .env or environment.')
        return 1

    db = DatabaseManager()
    matches = db.get_upcoming_matches(args.days)
    if not matches:
        print(f'No upcoming matches in DB for the next {args.days} days.')
        return 0

    # Bucket by competition so we can show per-league coverage
    by_comp: dict[str, list] = {}
    for m in matches:
        by_comp.setdefault(m.competition, []).append(m)

    total = 0
    matched = 0
    misses: list[tuple[str, str, str, str]] = []  # (competition, home, away, reason)
    unsupported_comps: set[str] = set()

    print(f'\nChecking {len(matches)} fixtures across {len(by_comp)} competitions over {args.days} days\n')
    print(f'{"League":<24} {"Matched":>9} {"Total":>7} {"Rate":>7}')
    print('-' * 50)

    for comp in sorted(by_comp):
        fixtures = by_comp[comp]
        if comp not in SPORT_KEY_MAP:
            unsupported_comps.add(comp)
            print(f'{comp:<24} {"-":>9} {len(fixtures):>7} {"n/a":>7}  (no sport_key)')
            continue

        comp_matched = 0
        for m in fixtures:
            total += 1
            best = client.best_odds_for_match(comp, m.home_team.name, m.away_team.name)
            if best:
                comp_matched += 1
                matched += 1
                if args.verbose:
                    print(f'  ✓ {m.home_team.name} vs {m.away_team.name}'
                          f'  H:{_fmt(best["home"])}  D:{_fmt(best["draw"])}  A:{_fmt(best["away"])}')
            else:
                misses.append((comp, m.home_team.name, m.away_team.name, 'no event matched'))

        rate = comp_matched / len(fixtures) if fixtures else 0
        print(f'{comp:<24} {comp_matched:>9} {len(fixtures):>7}  {rate:>6.0%}')

    print('-' * 50)
    overall = (matched / total * 100) if total else 0
    print(f'\nOverall: {matched}/{total} fixtures matched ({overall:.0f}%)')

    if unsupported_comps:
        print(f'\nUnsupported competitions ({len(unsupported_comps)}): '
              + ', '.join(sorted(unsupported_comps)))

    if misses:
        print(f'\n--- {len(misses)} unmatched fixtures ---')
        print('If a team appears in this list repeatedly, add it to TEAM_ALIASES in src/odds_api.py.\n')
        for comp, home, away, reason in misses[:30]:
            print(f'  [{comp}] {home}  vs  {away}  ({reason})')
        if len(misses) > 30:
            print(f'  ... and {len(misses) - 30} more (use --verbose to see all)')

    return 0


def _fmt(o: dict | None) -> str:
    if not o:
        return '   - '
    return f'{o["price"]:.2f}'


if __name__ == '__main__':
    raise SystemExit(main())
