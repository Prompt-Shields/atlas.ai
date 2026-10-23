"""Atlas's Guide console writes what Guide's file console writes (promptly-guide N1, option A).

The fixtures in ``tests/fixtures/guide_console`` are copied from promptly-guide, not
made here. Each folder holds a console document and what promptly-guide's own scripts
wrote for it at main after promptly-guide#310 and #315, with the ``cloudEndpoint``
field of promptly-guide#338:

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
import re
from pathlib import Path

import pytest

from app.services.guide_console import profiles, windows

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "guide_console"
CASES = ("example", "full")

# Every key Guide reads from a profile: promptly-guide
# Sources/GuideCore/ManagedSettings.swift (ManagedKeys.all), the cloud switches, the
# knowledge sources and groups, and sign-in. Copied from there at main after
# promptly-guide#315.
MANAGED_KEYS = {
    "ExcludedApps",
    "ExcludedWindowTitles",
    "ExcludedSites",
    "SkillPacksOff",
    "MemoryDisabled",
    "SkillPackFolders",
    "AtlasURL",
    "AdoptionTeam",
    "WorkClientNotices",
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
    "GroupsFromAtlas",
}
# The organisation's own model endpoint (promptly-guide#40): ManagedEndpointKeys in
# promptly-guide Sources/GuideCloud/ManagedEndpoint.swift, copied from there at main after
# promptly-guide#308. They pin where the cloud model may send and never turn it on.
ENDPOINT_KEYS = {
    "CloudProvider",
    "CloudEndpoint",
    "CloudModelID",
    "CloudAPIVersion",
    "CloudRegion",
    "CloudDataRegion",
    "CloudProcessingArea",
    "CloudAcceptsImages",
    "CloudZeroRetentionConfirmed",
}
# Pins, not switches: false means something (no image; the agreement not confirmed), and
# neither turns the cloud model on.
ENDPOINT_PINS = {"CloudAcceptsImages", "CloudZeroRetentionConfirmed"}
# The switches that turn something off, which a profile only ever carries as true.
RESTRICTING_SWITCHES = {"CloudModelDisabled", "CloudRequireEUProcessing", "MemoryDisabled"}
# The one switch that turns something on: the Work client's 90-day notices, shown to an
# admin's Mac only, for a group. It reads nothing and sends nothing (promptly-guide#284).
NOTICE_SWITCHES = {"WorkClientNotices"}
# Groups from Atlas over SCIM (promptly-guide#310): signed-in Macs ask this Atlas for a
# person's groups. It reads nothing on the Mac; only true is ever written.
ATLAS_SWITCHES = {"GroupsFromAtlas"}


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
    # CloudRegion is Bedrock's only; the full console pins Azure, and Bedrock is tested below.
    assert written == MANAGED_KEYS | OTHER_KEYS | (ENDPOINT_KEYS - {"CloudRegion"})


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
    allowed = RESTRICTING_SWITCHES | NOTICE_SWITCHES | ATLAS_SWITCHES | {"KnowledgeGroup"}
    for profile in profiles.build(_console("full")).values():
        for key, value in profiles.settings_of(profile).items():
            if isinstance(value, bool) and key not in ENDPOINT_PINS:
                assert value is True, key
                assert _base(key) in allowed, key


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


# ── The Work client's 90-day notices (promptly-guide#284) ──────────────────
# The same cases as promptly-guide Scripts/console/test_make_profiles.py's
# WorkClientNotices: on for a group of admins, never for everyone.


def test_the_notices_are_for_a_group_with_a_key_of_its_own() -> None:
    console = _console("example")
    console["groups"]["sales"]["workClientNotices"] = True
    built = profiles.build(console)
    group = profiles.settings_of(built["promptly-guide-group-sales.mobileconfig"])
    assert group["WorkClientNotices.sales"] is True
    everyone = profiles.settings_of(built[profiles.EVERYONE_FILE])
    assert "WorkClientNotices" not in everyone


def test_the_notices_are_never_for_everyone() -> None:
    console = _console("example")
    console["everyone"]["workClientNotices"] = True
    with pytest.raises(profiles.ConsoleError, match="admins only"):
        profiles.build(console)


def test_the_notices_off_write_nothing_and_must_be_a_switch() -> None:
    console = _console("example")
    console["groups"]["sales"]["workClientNotices"] = False
    keys = [k for p in profiles.build(console).values() for k in profiles.settings_of(p)]
    assert not any(k.startswith("WorkClientNotices") for k in keys)
    console["groups"]["sales"]["workClientNotices"] = "yes"
    with pytest.raises(profiles.ConsoleError, match="true or false"):
        profiles.build(console)


# The Windows half (promptly-guide#315): the same cases as
# Scripts/console/test_make_windows_policy.py's WorkClientNotices. A machine policy, set
# only in the policy for the admins' PCs.


def test_on_windows_only_the_admins_group_gets_the_notices() -> None:
    _, reg, _ = windows.build_windows(_console("full"), [("finance", "sales")])
    key = profiles.KEY_WORK_CLIENT_NOTICES
    assert key not in reg["promptly-guide-everyone.reg"][1]
    assert key not in reg["promptly-guide-groups-sales.reg"][1]
    assert reg["promptly-guide-groups-finance.reg"][1][key] is True
    assert reg["promptly-guide-groups-finance+sales.reg"][1][key] is True
    files, _ = windows.files(_console("full"))
    finance = files["promptly-guide-groups-finance.reg"].decode("utf-16-le")
    assert f'"{key}"=dword:00000001' in finance


def test_on_windows_per_group_the_notices_are_the_groups_own_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(windows, "GROUPS", "perGroup")
    key = profiles.KEY_WORK_CLIENT_NOTICES
    policy_keys, reg, _ = windows.build_windows(_console("full"))
    assert reg["promptly-guide-group-finance.reg"][1][f"{key}.finance"] is True
    assert f"{key}.sales" not in reg["promptly-guide-group-sales.reg"][1]
    assert f'valueName="{key}.finance"' in windows.admx(policy_keys)


def test_on_windows_the_notices_are_a_machine_policy_in_their_own_category() -> None:
    files, _ = windows.files(_console("full"))
    admx = files[windows.ADMX_FILE].decode("utf-8")
    key = profiles.KEY_WORK_CLIENT_NOTICES
    policy = re.search(rf'<policy name="{key}" ([^>]*)>\s*<parentCategory ref="([^"]+)"', admx)
    assert policy is not None
    assert 'class="Machine"' in policy.group(1)
    assert policy.group(2) == "WorkClient"
    assert key not in windows.MAC_ONLY


def test_on_windows_the_notices_are_never_for_everyone() -> None:
    console = _console("full")
    console["everyone"]["workClientNotices"] = True
    with pytest.raises(profiles.ConsoleError, match="admins only"):
        windows.files(console)


# ── Groups from Atlas over SCIM (promptly-guide#310) ──────────────────────
# The same cases as promptly-guide Scripts/console/test_make_profiles.py's
# AdoptionFigures scimGroups tests.


def test_scim_groups_are_said_only_when_set() -> None:
    console = _console("example")
    console["atlas"] = {"url": "https://atlas.nordlys.example"}
    for profile in profiles.build(console).values():
        assert "GroupsFromAtlas" not in profiles.settings_of(profile)
    console["atlas"]["scimGroups"] = False
    built = profiles.build(console)
    assert "GroupsFromAtlas" not in profiles.settings_of(built[profiles.EVERYONE_FILE])
    console["atlas"]["scimGroups"] = True
    built = profiles.build(console)
    assert profiles.settings_of(built[profiles.EVERYONE_FILE])["GroupsFromAtlas"] is True
    for name, profile in built.items():
        if name != profiles.EVERYONE_FILE:
            assert not any(k.startswith("GroupsFromAtlas") for k in profiles.settings_of(profile))


def test_scim_groups_that_is_not_a_switch_is_refused() -> None:
    console = _console("example")
    console["atlas"] = {"url": "https://atlas.nordlys.example", "scimGroups": "yes"}
    with pytest.raises(profiles.ConsoleError, match="true or false"):
        profiles.build(console)


def test_scim_groups_need_an_address() -> None:
    console = _console("example")
    console["atlas"] = {"scimGroups": True}
    with pytest.raises(profiles.ConsoleError, match=r"atlas\.url"):
        profiles.build(console)


# ── The organisation's own model endpoint (promptly-guide#40) ──────────────

AZURE = {
    "provider": "azure-openai",
    "endpoint": "https://nordlys.openai.azure.com",
    "model": "guide-deployment",
}
BEDROCK = {"provider": "bedrock", "region": "eu-north-1", "model": "org-model-v1"}


def _with_endpoint(section: object) -> dict:
    console = _console("example")
    console["cloudEndpoint"] = section
    return profiles.build(console)


def _everyone_endpoint(section: dict) -> dict:
    return profiles.settings_of(_with_endpoint(section)[profiles.EVERYONE_FILE])


def _refused(section: object, words: str) -> None:
    with pytest.raises(profiles.ConsoleError) as raised:
        _with_endpoint(section)
    assert words in str(raised.value)


def test_the_endpoint_keys_are_the_ones_guide_reads() -> None:
    assert set(profiles.CLOUD_ENDPOINT_KEYS.values()) == ENDPOINT_KEYS
    assert set(profiles.CLOUD_PROVIDERS) == {
        "azure-openai",
        "bedrock",
        "anthropic",
        "openai-compatible",
    }


def test_the_everyone_profile_pins_the_endpoint() -> None:
    s = _everyone_endpoint(
        {
            **AZURE,
            "apiVersion": "2024-10-21",
            "dataRegion": " EU (Sweden Central) ",
            "processingArea": "eu",
            "acceptsImages": False,
            "zeroRetentionConfirmed": True,
        }
    )
    assert {k: v for k, v in s.items() if k in ENDPOINT_KEYS} == {
        "CloudProvider": "azure-openai",
        "CloudEndpoint": "https://nordlys.openai.azure.com",
        "CloudModelID": "guide-deployment",
        "CloudAPIVersion": "2024-10-21",
        "CloudDataRegion": "EU (Sweden Central)",
        "CloudProcessingArea": "eu",
        "CloudAcceptsImages": False,
        "CloudZeroRetentionConfirmed": True,
    }


def test_no_group_profile_carries_the_endpoint() -> None:
    for name, profile in _with_endpoint(AZURE).items():
        if name != profiles.EVERYONE_FILE:
            assert not set(profiles.settings_of(profile)) & ENDPOINT_KEYS, name
    console = _console("example")
    console["groups"]["finance"]["cloudEndpoint"] = AZURE
    with pytest.raises(profiles.ConsoleError):
        profiles.build(console)


def test_no_endpoint_writes_nothing() -> None:
    for profile in profiles.build(_console("example")).values():
        assert not set(profiles.settings_of(profile)) & ENDPOINT_KEYS


def test_the_endpoint_never_turns_the_cloud_model_on() -> None:
    # promptly-guide#308's rule: a profile pins and never turns on.
    _refused({**AZURE, "enabled": True}, "cannot turn the cloud model on")
    s = _everyone_endpoint(AZURE)
    assert "CloudModelDisabled" not in s
    assert {k for k in s if k.startswith("Cloud")} <= ENDPOINT_KEYS | {"CloudRequireEUProcessing"}
    console = _console("example")
    console["everyone"]["cloudModelDisabled"] = True
    console["cloudEndpoint"] = AZURE
    everyone = profiles.settings_of(profiles.build(console)[profiles.EVERYONE_FILE])
    assert everyone["CloudModelDisabled"] is True


def test_each_provider_with_what_it_needs() -> None:
    s = _everyone_endpoint(BEDROCK)
    assert (s["CloudProvider"], s["CloudRegion"], s["CloudModelID"]) == (
        "bedrock",
        "eu-north-1",
        "org-model-v1",
    )
    assert "CloudEndpoint" not in s
    # Guide has a default endpoint and model for Anthropic's API, so neither is needed.
    s = _everyone_endpoint({"provider": "anthropic"})
    assert s["CloudProvider"] == "anthropic"
    assert "CloudModelID" not in s
    s = _everyone_endpoint(
        {"provider": "anthropic", "endpoint": "https://gateway.nordlys.no/anthropic"}
    )
    assert s["CloudEndpoint"] == "https://gateway.nordlys.no/anthropic"
    s = _everyone_endpoint(
        {
            "provider": "openai-compatible",
            "endpoint": "https://llm.nordlys.no/v1/",
            "model": "org/model-1",
        }
    )
    assert s["CloudEndpoint"] == "https://llm.nordlys.no/v1/"


@pytest.mark.parametrize(
    ("section", "words"),
    [
        ({"provider": "azure-openai", "model": "guide"}, "endpoint missing"),
        ({"provider": "openai-compatible", "endpoint": "https://llm.nordlys.no"}, "model missing"),
        ({"provider": "bedrock", "model": "m"}, "region missing"),
        ({"endpoint": "https://llm.nordlys.no", "model": "m"}, "cloudEndpoint.provider missing"),
        ({**AZURE, "provider": "Azure"}, "cloudEndpoint.provider"),
        ({**AZURE, "provider": ["azure-openai"]}, "cloudEndpoint.provider"),
        ({"provider": "kimi", "model": "m"}, "internal testing only"),
        ({**BEDROCK, "endpoint": "https://bedrock.nordlys.no"}, "bedrock does not use it"),
        ({**AZURE, "region": "eu-north-1"}, "azure-openai does not use it"),
        ({"provider": "anthropic", "apiVersion": "2024-10-21"}, "anthropic does not use it"),
        ({**AZURE, "model": "guide/../other"}, "an Azure deployment name"),
        ({**AZURE, "model": " "}, "cloudEndpoint.model"),
        ({**BEDROCK, "model": "two words"}, "cloudEndpoint.model"),
        ({**BEDROCK, "region": "eu-north-1.evil.example"}, "cloudEndpoint.region"),
        ({**AZURE, "apiVersion": "2024-10-21&x=1"}, "cloudEndpoint.apiVersion"),
        ({**AZURE, "dataRegion": "EU\nSweden"}, "cloudEndpoint.dataRegion"),
        ({**AZURE, "dataRegion": ""}, "cloudEndpoint.dataRegion"),
        ({**AZURE, "modelId": "x"}, "unknown field modelId"),
        ("azure-openai", "cloudEndpoint: must be an object"),
    ],
)
def test_an_endpoint_guide_could_not_use_is_refused(section: object, words: str) -> None:
    _refused(section, words)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://nordlys.openai.azure.com",
        "nordlys.openai.azure.com",
        "https://key:secret@nordlys.openai.azure.com",
        "https://nordlys.openai.azure.com/?k=1",
        "https://nordlys.openai.azure.com/#x",
        "https://",
        "https://nordlys .azure.com",
        "",
        7,
    ],
)
def test_only_an_https_endpoint_with_no_credential_in_it(endpoint: object) -> None:
    _refused({**AZURE, "endpoint": endpoint}, "cloudEndpoint.endpoint")


def test_where_the_provider_processes() -> None:
    for area in ("eu", "eea", "adequacy", "elsewhere"):
        assert _everyone_endpoint({**AZURE, "processingArea": area})["CloudProcessingArea"] == area
    for bad in ("europe", "unstated", "EU", True):
        _refused({**AZURE, "processingArea": bad}, "cloudEndpoint.processingArea")


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("acceptsImages", "CloudAcceptsImages"),
        ("zeroRetentionConfirmed", "CloudZeroRetentionConfirmed"),
    ],
)
def test_endpoint_switches_pin_either_way(field: str, key: str) -> None:
    for value in (True, False):
        assert _everyone_endpoint({**AZURE, field: value})[key] is value
    _refused({**AZURE, field: "yes"}, f"cloudEndpoint.{field}: true or false")
    assert key not in _everyone_endpoint(AZURE)


def test_on_windows_the_endpoint_is_left_out_and_said_so() -> None:
    # The Windows core has no cloud model setup to pin yet (promptly-guide#338).
    files, notes = windows.files(_console("full"))
    for name, content in files.items():
        if name.endswith(".reg"):
            text = content.decode("utf-16-le")
            for key in ENDPOINT_KEYS:
                assert f'"{key}"' not in text, (name, key)
    assert all(key in windows.MAC_ONLY for key in ENDPOINT_KEYS)
    assert any(note.startswith("cloudEndpoint") for note in notes)
    _, notes = windows.files(_console("example"))
    assert not any("cloudEndpoint" in note for note in notes)
