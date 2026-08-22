"""
Regression cover for the ingestion layer's two silent-corruption bugs.

Both were the kind that never raise: one matched the wrong team's odds, the
other minted a new identity for a club on every run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / 'src')


# ---------------------------------------------------------------------------
# Odds API team-name matching
# ---------------------------------------------------------------------------

class TestTeamNameMatching:
    """
    _normalize strips club-type tokens (fc, ac, psv, ...). When a name consisted
    only of such tokens it returned '', and _matches tests substring containment
    — '' is a substring of every string, so the name matched every team on the
    board. 'PSV' is the live example.
    """

    def test_normalize_never_returns_empty(self):
        from odds_api import _normalize
        for name in ('PSV', 'AC', 'FC', 'SC', 'AS'):
            assert _normalize(name) != '', f"{name!r} normalized away entirely"

    @pytest.mark.parametrize('a,b', [
        ('PSV', 'Ajax'),
        ('PSV', 'Feyenoord'),
        ('Ajax', 'PSV'),
        ('Real Madrid CF', 'Real Sociedad'),
        ('AC Milan', 'AC Monza'),
    ])
    def test_distinct_clubs_do_not_match(self, a, b):
        from odds_api import _matches
        assert _matches(a, b) is False

    @pytest.mark.parametrize('a,b', [
        ('PSV', 'PSV Eindhoven'),
        ('Arsenal', 'Arsenal FC'),
        ('Man United', 'Manchester United FC'),
        ('Real Madrid', 'Real Madrid CF'),
        ('Bayern Munich', 'Bayern München'),
        ('Inter', 'FC Internazionale Milano'),
        ('Wolves', 'Wolverhampton Wanderers FC'),
        ('Lyon', 'Olympique Lyonnais'),
    ])
    def test_same_club_still_matches(self, a, b):
        from odds_api import _matches
        assert _matches(a, b) is True

    def test_empty_input_never_matches(self):
        from odds_api import _matches
        assert _matches('', 'Arsenal FC') is False
        assert _matches('Arsenal FC', '') is False


# ---------------------------------------------------------------------------
# Synthetic team IDs
# ---------------------------------------------------------------------------

class TestSyntheticTeamIds:
    """
    generate_team_id used builtin hash(), which Python randomises per process.
    The ID is the identity add_team matches on, so every re-load inserted a new
    Team row and split the club's history.
    """

    def test_stable_across_processes(self):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from load_external_csv import generate_team_id\n"
            "print(generate_team_id('Arsenal FC', 'E0'))\n" % SRC
        )
        seen = {
            subprocess.run([sys.executable, '-c', code], capture_output=True,
                           text=True, check=True).stdout.strip()
            for _ in range(3)
        }
        assert len(seen) == 1, f"ID differs between interpreters: {seen}"

    def test_matches_expected_crc32_value(self):
        """Pins the scheme itself — a change here silently orphans every team row."""
        from load_external_csv import generate_team_id
        assert generate_team_id('Arsenal FC', 'E0') == 140735

    def test_stays_inside_the_league_band(self):
        from load_external_csv import LEAGUE_ID_OFFSETS, generate_team_id
        for code, (offset, _match_offset) in LEAGUE_ID_OFFSETS.items():
            team_id = generate_team_id(f'Probe Team {code}', code)
            assert offset <= team_id < offset + 49000

    def test_collision_raises_instead_of_merging_two_clubs(self, monkeypatch):
        import load_external_csv as lx
        monkeypatch.setattr(lx, '_TEAM_ID_SEEN', {}, raising=True)
        monkeypatch.setattr(lx, 'TEAM_ID_OVERRIDES', {'Club A': 7, 'Club B': 7}, raising=True)
        assert lx.generate_team_id('Club A', 'E0') == lx.LEAGUE_ID_OFFSETS['E0'][0] + 7
        with pytest.raises(ValueError, match='collision'):
            lx.generate_team_id('Club B', 'E0')

    def test_override_resolves_a_collision(self, monkeypatch):
        import load_external_csv as lx
        monkeypatch.setattr(lx, '_TEAM_ID_SEEN', {}, raising=True)
        monkeypatch.setattr(lx, 'TEAM_ID_OVERRIDES', {'Club A': 7, 'Club B': 8}, raising=True)
        assert lx.generate_team_id('Club A', 'E0') != lx.generate_team_id('Club B', 'E0')
