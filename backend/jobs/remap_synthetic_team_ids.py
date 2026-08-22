"""
One-time repair for fragmented club identities.

WHY THIS EXISTS
---------------
Two bugs split a single club across several `teams` rows:

  1. `load_external_csv.generate_team_id` derived a club's synthetic api_id from
     Python's builtin `hash()`, which randomises string hashing per process. The
     ID differed on every run while `add_team` matched on api_id, so each re-load
     inserted a fresh row.
  2. Clubs present in BOTH the football-data.co.uk CSVs and the football-data.org
     API got one row per source — a synthetic ID and a real one — because nothing
     resolved them by name.

Features are computed per `team_id`, so a club with four identities had its form,
win rate, head-to-head and rest-day features built from a quarter of its history.

Both causes are fixed in the loaders (crc32 IDs, and `match_by_name=True`). This
script reconciles a database populated before that:

  1. Group every team row by club name.
  2. Pick one survivor per club — preferring the row carrying a REAL
     football-data.org ID, since that is what fixture refresh writes against.
  3. Repoint matches and standings onto the survivor and delete the rest.
  4. Give survivors that have no real ID their deterministic synthetic ID.
  5. Report (and optionally remove) duplicate fixtures the merge exposes.

Dry-run by default. Nothing is written without --apply.

    python jobs/remap_synthetic_team_ids.py                       # report only
    python jobs/remap_synthetic_team_ids.py --apply
    python jobs/remap_synthetic_team_ids.py --apply --merge-matches

--merge-matches deletes duplicate Match rows (same competition/season/home/away),
keeping the row with the most data. A duplicate is only removed when nothing
depends on it — predictions, bets, odds snapshots and prediction snapshots all
block. Its `match_features` row is derived data and is deleted alongside it.

Back the database up first: this rewrites identifiers across four tables.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from database import (  # noqa: E402
    Bet, DatabaseManager, Match, MatchFeatures, OddsSnapshot, Prediction,
    PredictionSnapshot, Standing, Team,
)
from load_external_csv import LEAGUE_ID_OFFSETS, generate_team_id  # noqa: E402
from odds_api import _ALIAS_INDEX, _normalize, _matches as names_match  # noqa: E402

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('remap_team_ids')

# Synthetic IDs are laid out as offset + (slot % 49000) with offsets 50000 apart,
# so the band an ID falls into identifies its league even when the slot itself was
# random garbage. Anything outside every band is a real football-data.org ID.
BAND_WIDTH = 49000


def league_for_api_id(api_id: int) -> str | None:
    for code, (team_offset, _match_offset) in LEAGUE_ID_OFFSETS.items():
        if team_offset <= api_id < team_offset + BAND_WIDTH:
            return code
    return None


def match_counts(session) -> dict:
    """matches played per team_id, in two grouped queries rather than one per row."""
    from sqlalchemy import func
    counts: dict[int, int] = defaultdict(int)
    for col in (Match.home_team_id, Match.away_team_id):
        for team_id, n in session.query(col, func.count()).group_by(col).all():
            if team_id is not None:
                counts[team_id] += n
    return counts


def plan_groups(session):
    """Group team rows by club name and choose a survivor for each."""
    counts = match_counts(session)
    by_name: dict[str, list[Team]] = defaultdict(list)
    for team in session.query(Team).order_by(Team.id).all():
        by_name[team.name].append(team)

    plans = []
    for name, rows in sorted(by_name.items()):
        if len(rows) == 1:
            continue
        real = [t for t in rows if league_for_api_id(t.api_id or 0) is None]
        # Prefer a real football-data.org identity; among candidates take the one
        # with the most history, then the lowest id for stability.
        pool = real or rows
        survivor = sorted(pool, key=lambda t: (-counts.get(t.id, 0), t.id))[0]
        losers = [t for t in rows if t.id != survivor.id]

        target = None
        if not real:
            # No real identity exists — settle on the deterministic synthetic ID,
            # using the league band of whichever row carried the most history.
            busiest = sorted(rows, key=lambda t: (-counts.get(t.id, 0), t.id))[0]
            code = league_for_api_id(busiest.api_id or 0)
            if code:
                target = generate_team_id(name, code)

        plans.append({
            'name': name, 'survivor': survivor, 'losers': losers,
            'counts': counts, 'target': target, 'kept_real': bool(real),
        })
    return plans


def match_strength(name_a: str, name_b: str) -> int:
    """How confidently do these two names denote the same club? 0 = not a match.

    Ranking matters more than the boolean did. 'Milan FC' (the CSV's AC Milan)
    matched 'FC Internazionale Milano' on the substring rule, because 'milan' is
    a substring of 'milano' — and taking the first match found would have merged
    AC Milan into Inter. Exact and alias matches now outrank substring/fuzzy ones,
    and a tie at the top is treated as ambiguous rather than guessed.
    """
    a, b = _normalize(name_a), _normalize(name_b)
    if not a or not b:
        return 0
    if a == b:
        return 3
    alias_set = _ALIAS_INDEX.get(a) or _ALIAS_INDEX.get(b)
    if alias_set and a in alias_set and b in alias_set:
        return 2
    return 1 if names_match(name_a, name_b) else 0


def plan_fuzzy_pairs(session):
    """Pair a synthetic row with the real row for the same club under a different spelling.

    Exact-name grouping can't see these: football-data.co.uk writes 'PSV Eindhoven'
    where football-data.org writes 'PSV', 'Zwolle' where the API says 'PEC Zwolle'.
    Both rows survive the first pass and the club stays split in two.

    Three constraints keep this conservative, because merging two genuinely
    different clubs is unrecoverable:
      * only a SYNTHETIC row may merge into a REAL one — two rows from the same
        source inside one league are different clubs by construction;
      * the two rows must share a competition;
      * they must never have appeared in the same match. A club cannot play
        itself, so this alone rules out almost every false positive.
    """
    from sqlalchemy import or_

    rows = session.query(Team).order_by(Team.id).all()
    synthetic, real = [], []
    for t in rows:
        (synthetic if league_for_api_id(t.api_id or 0) is not None else real).append(t)

    comps: dict[int, set] = defaultdict(set)
    for team_id, comp in session.query(Match.home_team_id, Match.competition).distinct():
        comps[team_id].add(comp)
    for team_id, comp in session.query(Match.away_team_id, Match.competition).distinct():
        comps[team_id].add(comp)

    pairs = []
    for syn in synthetic:
        syn_comps = comps.get(syn.id, set())
        if not syn_comps:
            continue

        scored = []
        for r in real:
            if not (syn_comps & comps.get(r.id, set())):
                continue
            score = match_strength(syn.name, r.name)
            if score:
                scored.append((score, r))
        if not scored:
            continue

        best = max(s for s, _r in scored)
        winners = [r for s, r in scored if s == best]
        if len(winners) > 1:
            logger.info("  ambiguous: %r could be %s — skipped",
                        syn.name, ' or '.join(repr(w.name) for w in winners))
            continue
        target = winners[0]

        faced = session.query(Match.id).filter(or_(
            (Match.home_team_id == syn.id) & (Match.away_team_id == target.id),
            (Match.home_team_id == target.id) & (Match.away_team_id == syn.id),
        )).first()
        if faced:
            logger.info("  skipping %r / %r — they have played each other",
                        syn.name, target.name)
            continue
        pairs.append((target, syn, best))  # real survives
    return pairs


def repoint(session, survivor: Team, duplicate: Team) -> dict:
    """Move every reference from `duplicate` to `survivor`, using bulk statements.

    Reassigning a loaded ORM object's FK and then session.delete()-ing its parent
    makes SQLAlchemy apply the default nullify cascade to the still-loaded
    collection — it wrote team_id=NULL over the row just relocated and tripped the
    NOT NULL constraint. Core-level statements skip that machinery entirely.
    """
    moved = {
        'home': session.query(Match).filter(Match.home_team_id == duplicate.id)
        .update({Match.home_team_id: survivor.id}, synchronize_session=False),
        'away': session.query(Match).filter(Match.away_team_id == duplicate.id)
        .update({Match.away_team_id: survivor.id}, synchronize_session=False),
    }

    # standings carries a unique index on (team_id, season, competition), so a
    # blind repoint can violate it. Keep the survivor's row where both exist.
    survivor_keys = {
        (row.season, row.competition)
        for row in session.query(Standing).filter(Standing.team_id == survivor.id)
    }
    conflicting = [
        row.id
        for row in session.query(Standing).filter(Standing.team_id == duplicate.id)
        if (row.season, row.competition) in survivor_keys
    ]
    if conflicting:
        (session.query(Standing).filter(Standing.id.in_(conflicting))
         .delete(synchronize_session=False))
    moved['standings'] = (
        session.query(Standing).filter(Standing.team_id == duplicate.id)
        .update({Standing.team_id: survivor.id}, synchronize_session=False)
    )
    moved['standings_dropped'] = len(conflicting)
    return moved


def apply_api_ids(session, plans):
    """Two-phase api_id rewrite.

    api_id is UNIQUE and NOT NULL, so assigning targets one at a time can collide
    with an ID some *other* row still holds — a transient clash, not a real one.
    The first dry run reported exactly this: Leganés' target was Sevilla's stale
    ID, and Sevilla was itself about to move. Parking every survivor on a negative
    placeholder first removes the ordering problem; only target-vs-target
    collisions are genuine, and those are reported instead of applied.
    """
    pending = [p for p in plans if p['target'] and p['survivor'].api_id != p['target']]
    if not pending:
        return 0, []

    targets: dict[int, str] = {}
    genuine = []
    for p in pending:
        other = targets.get(p['target'])
        if other is not None:
            genuine.append(f"{p['name']!r} and {other!r} both map to api_id {p['target']}")
        targets[p['target']] = p['name']
    if genuine:
        return 0, genuine

    for p in pending:  # phase 1 — park out of the way
        (session.query(Team).filter(Team.id == p['survivor'].id)
         .update({Team.api_id: -p['survivor'].id}, synchronize_session=False))
    for p in pending:  # phase 2 — land on the deterministic ID
        (session.query(Team).filter(Team.id == p['survivor'].id)
         .update({Team.api_id: p['target']}, synchronize_session=False))
    return len(pending), []


def find_duplicate_matches(session):
    seen: dict[tuple, list[Match]] = defaultdict(list)
    for m in session.query(Match).order_by(Match.id).all():
        seen[(m.competition, m.season, m.home_team_id, m.away_team_id)].append(m)
    return {k: v for k, v in seen.items() if len(v) > 1}


def blocking_dependents(session, match_ids):
    """Rows that make a duplicate match unsafe to delete, counted in bulk.

    match_features is deliberately absent: it is derived data regenerated by
    feature_engineering, so it should never keep a duplicate fixture alive.
    """
    from sqlalchemy import func
    blocked: dict[int, dict] = defaultdict(dict)
    for label, model in (('predictions', Prediction), ('prediction_snapshots', PredictionSnapshot),
                         ('bets', Bet), ('odds_snapshots', OddsSnapshot)):
        rows = (session.query(model.match_id, func.count())
                .filter(model.match_id.in_(match_ids))
                .group_by(model.match_id).all())
        for match_id, n in rows:
            if n:
                blocked[match_id][label] = n
    return blocked


def richness(match: Match, dep_count: int = 0) -> tuple:
    """Rank duplicates so the best row survives.

    Dependents come first: if one copy of a fixture carries predictions, bets or
    snapshots and another doesn't, keeping the one with dependents means the
    spare is free to delete. Ranking on data alone left 646 fixtures blocked
    purely because the richer copy wasn't the one history had attached itself to.
    """
    filled = sum(
        1 for v in (
            match.home_score, match.away_score, match.winner, match.home_ht_score,
            match.home_shots, match.home_corners, match.b365_home, match.xg_home,
        ) if v is not None
    )
    return (dep_count, filled, -match.id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='commit (default: dry run)')
    parser.add_argument('--merge-matches', action='store_true',
                        help='also delete duplicate fixtures with no dependent rows')
    parser.add_argument('--verbose', action='store_true', help='log every merge')
    parser.add_argument('--fuzzy', action='store_true',
                        help='also merge synthetic rows into the real row for the same '
                             'club spelled differently (see plan_fuzzy_pairs)')
    args = parser.parse_args()

    db = DatabaseManager()
    session = db.session

    total_teams = session.query(Team).count()
    plans = plan_groups(session)
    logger.info("Team rows: %d", total_teams)
    logger.info("Clubs with more than one row: %d", len(plans))
    logger.info("  keeping a real football-data.org ID: %d",
                sum(1 for p in plans if p['kept_real']))
    logger.info("  synthetic-only, will get a deterministic ID: %d",
                sum(1 for p in plans if not p['kept_real']))

    merged = 0
    for p in plans:
        for loser in p['losers']:
            counts = repoint(session, p['survivor'], loser)
            if args.verbose:
                logger.info("  %-28s keep id=%-5s drop id=%-5s (%s home, %s away, "
                            "%s standings, %s dropped)",
                            p['name'][:28], p['survivor'].id, loser.id, counts['home'],
                            counts['away'], counts['standings'], counts['standings_dropped'])
            (session.query(Team).filter(Team.id == loser.id)
             .delete(synchronize_session=False))
            merged += 1
        session.expire_all()

    fuzzy_merged = 0
    if args.fuzzy:
        pairs = plan_fuzzy_pairs(session)
        logger.info("")
        logger.info("Cross-spelling pairs (synthetic -> real): %d", len(pairs))
        for survivor, dup, score in pairs:
            counts = repoint(session, survivor, dup)
            logger.info("  [%s] %-30s <- %-30s (%s home, %s away, %s standings)",
                        {3: 'exact', 2: 'alias', 1: 'fuzzy'}[score],
                        survivor.name[:30], dup.name[:30],
                        counts['home'], counts['away'], counts['standings'])
            (session.query(Team).filter(Team.id == dup.id)
             .delete(synchronize_session=False))
            fuzzy_merged += 1
        session.expire_all()

    remapped, conflicts = apply_api_ids(session, plans)
    session.flush()

    logger.info("")
    logger.info("Merged %d duplicate team rows (%d by exact name, %d across spellings), "
                "rewrote %d api_ids", merged + fuzzy_merged, merged, fuzzy_merged, remapped)
    if conflicts:
        logger.warning("UNRESOLVED collisions (%d) — add TEAM_ID_OVERRIDES entries:", len(conflicts))
        for c in conflicts:
            logger.warning("  %s", c)

    dups = find_duplicate_matches(session)
    all_ids = [m.id for matches in dups.values() for m in matches]
    deps = blocking_dependents(session, all_ids) if all_ids else {}

    candidates = []
    for key, matches in dups.items():
        ranked = sorted(
            matches,
            key=lambda m: richness(m, sum(deps.get(m.id, {}).values())),
            reverse=True,
        )
        for loser in ranked[1:]:
            candidates.append((key, loser))
    removable = [(k, m) for k, m in candidates if not deps.get(m.id)]
    blocked = [(k, m) for k, m in candidates if deps.get(m.id)]

    logger.info("")
    logger.info("Duplicate fixtures exposed by the merge: %d", len(dups))
    logger.info("  deletable: %d    blocked by dependent rows: %d", len(removable), len(blocked))
    for key, loser in blocked[:5]:
        logger.info("    match id=%s %s %s — %s", loser.id, key[0], key[1], deps[loser.id])

    if args.merge_matches:
        for _key, loser in removable:
            (session.query(MatchFeatures).filter_by(match_id=loser.id)
             .delete(synchronize_session=False))
            session.query(Match).filter_by(id=loser.id).delete(synchronize_session=False)
        logger.info("  deleted %d duplicate fixtures (and their derived features)",
                    len(removable))

    if args.apply and not conflicts:
        session.commit()
        logger.info("")
        logger.info("Committed. Team rows: %d -> %d",
                    total_teams, session.query(Team).count())
        logger.info("Re-run feature engineering — history is now unified:")
        logger.info("    python src/feature_engineering.py")
    else:
        session.rollback()
        logger.info("")
        logger.info("Nothing written (%s).",
                    "conflicts must be resolved first" if conflicts else "dry run")

    db.close()
    return 1 if conflicts else 0


if __name__ == '__main__':
    raise SystemExit(main())
