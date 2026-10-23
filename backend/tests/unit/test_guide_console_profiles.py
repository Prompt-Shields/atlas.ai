"""Atlas's Guide console writes what Guide's file console writes (promptly-guide N1, option A).

The fixtures in ``tests/fixtures/guide_console`` are copied from promptly-guide, not
made here. Each folder holds a console document and what promptly-guide's own scripts
wrote for it at main cec96d4:

  * ``example``: promptly-guide ``deploy/console.example.json``;
  * ``full``: the console from ``Scripts/console/test_make_windows_policy.py``'s
    ``full()``, which sets every field there is, so every key appears;
  * ``mac/``: ``Scripts/console/make-profiles.py`` (``build``, then
    ``plistlib.dumps(profile, sort_keys=True)``, as its ``run`` writes them);
  * ``windows/``: ``Scripts/console/make-windows-policy.py``'s ``write``.

To refresh them after a change to the file console, run those two scripts on the two
console files and copy their output folders here. A difference here means the two
consoles disagree: change the port in ``app/services/guide_console`` to match, never
the fixture alone.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.guide_console import profiles, windows

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "guide_console"
CASES = ("example", "full")

# Every key Guide reads from a profile: promptly-guide
# Sources/GuideCore/ManagedSettings.swift (ManagedKeys.all), the cloud switches, the
# knowledge sources and groups, and sign-in. Copied from there at main cec96d4.
MANAGED_KEYS = {
    "ExcludedApps",
    "ExcludedWindowTitles",
    "ExcludedSites",
    "SkillPacksOff",
    "MemoryDisabled",
    "SkillPackFolders",
    "AtlasURL",
    "AdoptionTeam",
}
OTHER_KEYS = {
    "CloudModelDisabled",
    "CloudRequireEUProcessing",
    "KnowledgeSources",
    "KnowledgeGroup",
    "SSOFirebaseAPIKey",
    "SSOFirebaseProjectID",
    "SSOProvider",
    "SSOTenantID",
    "SSOSignInHost",
    "SSOProcessingArea",
    "SSOGroupsClaim",
    "SSOGroupsValidForHours",
}
# The only switches a profile may carry, and only ever as true: each turns something off.
RESTRICTING_SWITCHES = {"CloudModelDisabled", "CloudRequireEUProcessing", "MemoryDisabled"}


def _console(case: str) -> dict:
    return json.loads((FIXTURES / case / "console.json").read_text(encoding="utf-8"))


def _files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _base(key: str) -> str:
    return key.split(".", 1)[0]


# ── The same files as the file console ─────────────────────────────────────


@pytest.mark.parametrize("case", CASES)
def test_the_profiles_are_the_file_consoles_byte_for_byte(case: str) -> None:
    built = profiles.build(_console(case))
    expected = _files(FIXTURES / case / "mac")
    assert sorted(built) == sorted(expected)
    for name, profile in built.items():
        assert profiles.profile_bytes(profile) == expected[name], name


@pytest.mark.parametrize("case", CASES)
def test_the_windows_files_are_the_file_consoles_byte_for_byte(case: str) -> None:
    files, _ = windows.files(_console(case))
    expected = _files(FIXTURES / case / "windows")
    assert sorted(files) == sorted(expected)
    for name, content in files.items():
        assert content == expected[name], name


def test_the_full_console_covers_every_key_guide_reads() -> None:
    written = {
        _base(key)
        for profile in profiles.build(_console("full")).values()
        for key in profiles.settings_of(profile)
    }
    assert written == MANAGED_KEYS | OTHER_KEYS


def test_the_example_says_what_is_left_out_on_windows() -> None:
    _, notes = windows.files(_console("example"))
    assert any("it-runbooks" in note for note in notes)


def test_the_same_settings_give_the_same_uuids() -> None:
    once = profiles.build(_console("full"))
    again = profiles.build(_console("full"))
    assert {n: p["PayloadUUID"] for n, p in once.items()} == {
        n: p["PayloadUUID"] for n, p in again.items()
    }


# ── Only restrict, or name content ─────────────────────────────────────────


def test_every_switch_written_turns_something_off() -> None:
    for profile in profiles.build(_console("full")).values():
        for key, value in profiles.settings_of(profile).items():
            if isinstance(value, bool):
                assert value is True, key
                assert _base(key) in RESTRICTING_SWITCHES | {"KnowledgeGroup"}, key


def test_false_writes_nothing_rather_than_a_managed_off() -> None:
    console = _console("full")
    for field in ("cloudModelDisabled", "requireEUProcessing", "memoryOff"):
        console["everyone"][field] = False
    console["groups"]["finance"]["memoryOff"] = False
    keys = {_base(k) for p in profiles.build(console).values() for k in profiles.settings_of(p)}
    assert not keys & RESTRICTING_SWITCHES


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        # Things that read, or turn something on: the person's own switches in
        # privacy.json, which no profile can reach.
        ("everyone", "promptTips", True),
        ("everyone", "fieldTips", True),
        ("everyone", "adoptionOptIn", True),
        ("everyone", "screenReading", True),
        ("everyone", "memoryOn", True),
        ("everyone", "cloudModelEnabled", True),
        ("everyone", "pause", False),
        ("groups.sales", "promptTips", True),
        ("groups.sales", "skillPacksOn", ["claude"]),
        # A group cannot lift or set what only everyone can.
        ("groups.sales", "requireEUProcessing", True),
    ],
)
def test_a_field_that_would_turn_something_on_is_refused(
    section: str, field: str, value: object
) -> None:
    console = copy.deepcopy(_console("example"))
    target = console
    for part in section.split("."):
        target = target[part]
    target[field] = value
    with pytest.raises(profiles.ConsoleError, match="unknown field"):
        profiles.build(console)


def test_a_misspelt_field_is_refused_rather_than_dropped() -> None:
    console = _console("example")
    console["everyone"]["exludedApps"] = ["x.y"]
    with pytest.raises(profiles.ConsoleError, match="unknown field exludedApps"):
        profiles.build(console)


def test_a_pack_guide_does_not_have_is_refused() -> None:
    console = _console("example")
    console["groups"]["sales"]["skillPacksOff"] = ["gemini"]
    with pytest.raises(profiles.ConsoleError, match="no skill pack"):
        profiles.build(console)


def test_a_windows_app_that_is_not_an_executable_is_refused() -> None:
    console = _console("example")
    console["everyone"]["excludedWindowsApps"] = ["Casebook"]
    profiles.build(console)  # the Mac side does not read it
    with pytest.raises(profiles.ConsoleError, match="excludedWindowsApps"):
        windows.files(console)
