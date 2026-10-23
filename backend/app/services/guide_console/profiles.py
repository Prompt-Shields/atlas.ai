"""Promptly Guide's admin console, as configuration profiles (promptly-guide N1, option A).

Ported from promptly-guide ``Scripts/console/make-profiles.py`` (main at cec96d4), the
file console's reference: the same console document in, the same profiles out, byte for
byte. ``tests/unit/test_guide_console_profiles.py`` holds that against profiles the
file console wrote. Change the file console first, then this, never only this.

Only the pure parts are here: checking the console document and building the profiles
(``build``), reading what a profile forces (``settings_of``) and the key-by-key
difference two consoles make (``changes``). Writing files, the file console's own log
and its command line stay in promptly-guide; Atlas keeps its changes in the audit log.

What the document can say is only what Guide lets an organisation decide. Every
exclusion and pack switch adds a restriction; there is no field that turns the cloud
model on, removes a built-in exclusion or turns on anything that reads the screen,
because there is no key that would. Pack folders and knowledge sources name the
organisation's own content, and only that. The person's own opt-ins live in their own
``privacy.json``, which no profile reaches.

The checks refuse rather than guess: an unknown field, a bad group name, a source Guide
would drop, or a pack Guide does not have raises ``ConsoleError`` with the reason. The
same document always gives the same UUIDs, so an MDM replaces a profile instead of
installing a second one beside it.
"""

from __future__ import annotations

import plistlib
import re
import uuid

DOMAIN = "com.promptly.guide"

# The file names the file console gives its profiles, kept so a download replaces the
# file an admin may already have.
EVERYONE_FILE = "promptly-guide-everyone.mobileconfig"

# The keys Guide reads (promptly-guide Sources/GuideCore/ManagedSettings.swift and the
# cloud and identity settings). promptly-guide's console tests check them against Swift.
KEY_CLOUD_DISABLED = "CloudModelDisabled"
KEY_REQUIRE_EU = "CloudRequireEUProcessing"
KEY_SOURCES = "KnowledgeSources"
KEY_GROUP_PREFIX = "KnowledgeGroup."
KEY_MEMORY_DISABLED = "MemoryDisabled"
# Adoption figures (#37, #38): where they go, and the team a Mac counts under. Neither
# sends anything: only a person who opted in on their own Mac is ever counted.
KEY_ATLAS_URL = "AtlasURL"
KEY_ADOPTION_TEAM = "AdoptionTeam"
LIST_KEYS = {
    "excludedApps": "ExcludedApps",
    "excludedWindowTitles": "ExcludedWindowTitles",
    "excludedSites": "ExcludedSites",
    "skillPacksOff": "SkillPacksOff",
    # Folders holding the organisation's own skill packs (#84). Check the packs
    # themselves with Scripts/console/check-packs.py before they go in.
    "packFolders": "SkillPackFolders",
}

# Sign-in with the organisation's identity provider (#23), through Firebase: the
# console field and the key Guide reads. Tests/console check these against the Swift
# sources.
SIGN_IN_KEYS = {
    "firebaseApiKey": "SSOFirebaseAPIKey",
    "firebaseProjectId": "SSOFirebaseProjectID",
    "provider": "SSOProvider",
    "tenantId": "SSOTenantID",
    "signInHost": "SSOSignInHost",
    "processingArea": "SSOProcessingArea",
    "groupsClaim": "SSOGroupsClaim",
    "groupsValidForHours": "SSOGroupsValidForHours",
}
SIGN_IN_REQUIRED = {"firebaseApiKey", "firebaseProjectId", "provider", "signInHost"}
# How long a sign-in's groups stay in force (#58). The same range as
# IdentityConfiguration.groupsValidForHoursRange; left out, Guide uses 12.
SIGN_IN_HOURS = ("groupsValidForHours", 1, 168)
# The same rules as Sources/GuideIdentity/IdentityConfiguration.swift, so a profile
# Guide would refuse is refused here instead.
SIGN_IN_RULES = {
    "firebaseApiKey": (re.compile(r"^[A-Za-z0-9_-]{20,80}$"), "the Firebase project's web API key"),
    "firebaseProjectId": (re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"), "a Firebase project id"),
    "provider": (re.compile(r"^(oidc|saml)\.[A-Za-z0-9_-]{1,60}$"), "oidc.<name> or saml.<name>"),
    "tenantId": (re.compile(r"^[A-Za-z0-9-]{1,128}$"), "an Identity Platform tenant id"),
    "processingArea": (
        re.compile(r"^(eu|eea|adequacy|elsewhere)$"),
        "one of eu, eea, adequacy, elsewhere (where the Firebase project processes sign-ins)",
    ),
    "signInHost": (
        re.compile(
            r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
        ),
        "a host name such as your-project.firebaseapp.com, with no https:// or path",
    ),
    # A claim name or a SAML attribute name, which Entra sends as a URI: printable ASCII,
    # no spaces (#58).
    "groupsClaim": (
        re.compile(r"^[!-~]{1,256}$"),
        "the name of the claim or SAML attribute holding the groups, with no spaces",
    ),
}

# The skill packs Guide ships. Tests/console check this against Sources/GuidePacks.
KNOWN_PACKS = {"chatgpt", "claude", "claude-code", "m365-copilot"}

# Fields only Windows PCs use (#73). Accepted here so one console file serves both, and
# never written to a Mac profile: make-windows-policy.py checks and writes them.
WINDOWS_FIELDS = {"excludedWindowsApps"}

EVERYONE_FIELDS = {
    "cloudModelDisabled",
    "requireEUProcessing",
    "memoryOff",
    "adoptionTeam",
    *LIST_KEYS,
    *WINDOWS_FIELDS,
}
GROUP_FIELDS = {"memoryOff", "adoptionTeam", *LIST_KEYS, *WINDOWS_FIELDS}
ATLAS_FIELDS = {"url"}
SOURCE_FIELDS = {"id", "name", "folder", "enabled", "groups"}
TOP_FIELDS = {
    "organisation",
    "identifier",
    "everyone",
    "knowledgeSources",
    "groups",
    "signIn",
    "atlas",
}

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")


class ConsoleError(Exception):
    """What is wrong with the console file, in words an admin can act on."""


def _unknown(fields, allowed, where):
    extra = sorted(set(fields) - allowed)
    if extra:
        raise ConsoleError(
            f"{where}: unknown field {', '.join(extra)}. Allowed: {', '.join(sorted(allowed))}."
        )


def _strings(value, where):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConsoleError(f"{where}: must be a list of text.")
    return value


def _switch(section, field, where):
    """True when the field is there and true. Only true restricts: false is the same as
    leaving the key out, and writing it would read as "managed" in Settings for nothing."""
    if field not in section:
        return False
    if not isinstance(section[field], bool):
        raise ConsoleError(f"{where}.{field}: true or false.")
    return section[field]


def _lists(section, where):
    """The exclusion and pack lists of one section, checked the way Guide reads them."""
    for field in sorted(WINDOWS_FIELDS & set(section)):
        _strings(section[field], f"{where}.{field}")
    out = {}
    for field, key in LIST_KEYS.items():
        if field not in section:
            continue
        values = _strings(section[field], f"{where}.{field}")
        for v in values:
            text = v.strip()
            if field == "excludedApps" and (not text or any(c.isspace() for c in text)):
                raise ConsoleError(f"{where}.excludedApps: {v!r} is not a bundle id.")
            if field == "excludedWindowTitles" and len([c for c in text if c not in "*? \t"]) < 2:
                raise ConsoleError(
                    f"{where}.excludedWindowTitles: {v!r} needs two letters or digits "
                    "besides wildcards, or it would exclude nearly every window."
                )
            if field == "excludedSites" and (not text or any(c.isspace() for c in text)):
                raise ConsoleError(f"{where}.excludedSites: {v!r} is not a site.")
            # The same rule as ManagedSettings.packFolder: Guide ignores anything else.
            if field == "packFolders" and not (
                len(text) > 1 and (text.startswith("/") or text.startswith("~/"))
            ):
                raise ConsoleError(
                    f"{where}.packFolders: {v!r} is not an absolute path, or one starting with ~/."
                )
            if field == "skillPacksOff" and text.lower() not in KNOWN_PACKS:
                raise ConsoleError(
                    f"{where}.skillPacksOff: no skill pack {v!r}. "
                    f"Guide has {', '.join(sorted(KNOWN_PACKS))}."
                )
        if values:
            out[key] = [
                v.strip().lower() if field == "skillPacksOff" else v.strip() for v in values
            ]
    return out


def _source(entry, index, groups):
    where = f"knowledgeSources[{index}]"
    if not isinstance(entry, dict):
        raise ConsoleError(f"{where}: must be an object.")
    _unknown(entry, SOURCE_FIELDS, where)
    sid = entry.get("id")
    if not isinstance(sid, str) or not NAME.match(sid):
        raise ConsoleError(f"{where}.id: lowercase letters, digits and dashes, up to 40.")
    folder = entry.get("folder")
    if not isinstance(folder, str) or not (
        folder.startswith("~/") or (folder.startswith("/") and len(folder) > 1)
    ):
        raise ConsoleError(f"{where}.folder: an absolute path, or one starting with ~/.")
    out = {"Id": sid, "Folder": folder}
    if "name" in entry:
        if not isinstance(entry["name"], str):
            raise ConsoleError(f"{where}.name: must be text.")
        out["Name"] = entry["name"]
    if "enabled" in entry:
        if not isinstance(entry["enabled"], bool):
            raise ConsoleError(f"{where}.enabled: true or false.")
        out["Enabled"] = entry["enabled"]
    if "groups" in entry:
        names = _strings(entry["groups"], f"{where}.groups")
        for name in names:
            if not NAME.match(name):
                raise ConsoleError(f"{where}.groups: {name!r} is not a group name.")
        groups.update(names)
        if names:
            out["Groups"] = names
    return out


def _sign_in(section):
    """The sign-in keys, for the everyone profile. Sign-in is one set-up for the whole
    organisation, so there is no group variant."""
    if not isinstance(section, dict):
        raise ConsoleError("signIn: must be an object.")
    _unknown(section, set(SIGN_IN_KEYS), "signIn")
    missing = sorted(SIGN_IN_REQUIRED - set(section))
    if missing:
        raise ConsoleError(
            f"signIn: {', '.join(missing)} missing. docs/sso.md says where each comes from."
        )
    hours_field, low, high = SIGN_IN_HOURS
    if hours_field in section and "groupsClaim" not in section:
        raise ConsoleError(
            f"signIn.{hours_field}: only means something with groupsClaim. "
            "docs/knowledge-control.md says how groups from the directory work."
        )
    out = {}
    for field, value in section.items():
        if field == hours_field:
            # A count of hours, not text. A boolean is an int in Python, and is not one.
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ConsoleError(
                    f"signIn.{field}: {value!r} is not a whole number of hours "
                    f"from {low} to {high}."
                )
            out[SIGN_IN_KEYS[field]] = value
            continue
        rule, what = SIGN_IN_RULES[field]
        if not isinstance(value, str) or not rule.match(value.strip()):
            raise ConsoleError(f"signIn.{field}: {value!r} is not {what}.")
        out[SIGN_IN_KEYS[field]] = value.strip()
    return out


def _team(section, where):
    """The adoption team, checked as Guide and Atlas.AI check it, or None."""
    if "adoptionTeam" not in section:
        return None
    value = section["adoptionTeam"]
    team = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(team) <= 100 or any(ord(c) < 32 or ord(c) == 127 for c in team):
        raise ConsoleError(
            f"{where}.adoptionTeam: {value!r} is not a team name (1 to 100 characters, one line)."
        )
    return team


def _atlas(section):
    """The Atlas.AI address, for the everyone profile: https, a host, nothing else that
    could carry a credential."""
    if not isinstance(section, dict):
        raise ConsoleError("atlas: must be an object.")
    _unknown(section, ATLAS_FIELDS, "atlas")
    value = section.get("url")
    url = value.strip() if isinstance(value, str) else ""
    match = re.match(r"^https://([A-Za-z0-9.-]+)(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$", url)
    if not match:
        raise ConsoleError(
            f"atlas.url: {value!r} is not an https address such as "
            "https://atlas.example.com, with no user, query or fragment."
        )
    return {KEY_ATLAS_URL: url}


def build(console):
    """{file name: profile dict}. Raises ConsoleError; writes nothing."""
    if not isinstance(console, dict):
        raise ConsoleError("The console file must be one JSON object.")
    _unknown(console, TOP_FIELDS, "console")
    organisation = console.get("organisation")
    identifier = console.get("identifier")
    if not isinstance(organisation, str) or not organisation.strip():
        raise ConsoleError("organisation: your organisation's name, shown in System Settings.")
    if not isinstance(identifier, str) or not IDENTIFIER.match(identifier):
        raise ConsoleError(
            "identifier: a reverse-DNS prefix of your own, such as no.nordlys.promptly."
        )

    everyone = console.get("everyone", {})
    if not isinstance(everyone, dict):
        raise ConsoleError("everyone: must be an object.")
    _unknown(everyone, EVERYONE_FIELDS, "everyone")
    settings = {}
    for field, key in (
        ("cloudModelDisabled", KEY_CLOUD_DISABLED),
        ("requireEUProcessing", KEY_REQUIRE_EU),
        ("memoryOff", KEY_MEMORY_DISABLED),
    ):
        if _switch(everyone, field, "everyone"):
            settings[key] = True
    settings.update(_lists(everyone, "everyone"))
    team = _team(everyone, "everyone")
    if team is not None:
        settings[KEY_ADOPTION_TEAM] = team
    if "atlas" in console:
        settings.update(_atlas(console["atlas"]))
    if "signIn" in console:
        settings.update(_sign_in(console["signIn"]))

    groups_section = console.get("groups", {})
    if not isinstance(groups_section, dict):
        raise ConsoleError("groups: must be an object, one entry per group.")
    groups = set(groups_section)

    if "knowledgeSources" in console:
        entries = console["knowledgeSources"]
        if not isinstance(entries, list):
            raise ConsoleError("knowledgeSources: must be a list.")
        sources = [_source(e, i, groups) for i, e in enumerate(entries)]
        ids = [s["Id"] for s in sources]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ConsoleError(f"knowledgeSources: id used twice: {', '.join(dupes)}.")
        settings[KEY_SOURCES] = sources

    profiles = {
        EVERYONE_FILE: _profile(
            organisation,
            f"{identifier}.everyone",
            "Promptly Guide — everyone",
            "Promptly Guide settings for every Mac in the organisation.",
            settings,
        )
    }

    for name in sorted(groups):
        if not NAME.match(name):
            raise ConsoleError(
                f"groups: {name!r} is not a group name (lowercase letters, digits and dashes)."
            )
        section = groups_section.get(name, {})
        if not isinstance(section, dict):
            raise ConsoleError(f"groups.{name}: must be an object.")
        _unknown(section, GROUP_FIELDS, f"groups.{name}")
        group_settings = {KEY_GROUP_PREFIX + name: True}
        if _switch(section, "memoryOff", f"groups.{name}"):
            group_settings[f"{KEY_MEMORY_DISABLED}.{name}"] = True
        team = _team(section, f"groups.{name}")
        if team is not None:
            group_settings[f"{KEY_ADOPTION_TEAM}.{name}"] = team
        for key, values in _lists(section, f"groups.{name}").items():
            group_settings[f"{key}.{name}"] = values
        profiles[f"promptly-guide-group-{name}.mobileconfig"] = _profile(
            organisation,
            f"{identifier}.group.{name}",
            f"Promptly Guide — group: {name}",
            f"Puts this Mac in the {name} group for Promptly Guide, with that group's settings.",
            group_settings,
        )
    return profiles


def _uuid(identifier):
    # Stable across runs: the same profile keeps its UUID, so an MDM updates it in place.
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, identifier)).upper()


def _profile(organisation, identifier, display, description, settings):
    return {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": identifier,
        "PayloadUUID": _uuid(identifier),
        "PayloadDisplayName": display,
        "PayloadDescription": description,
        "PayloadOrganization": organisation,
        "PayloadScope": "System",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [
            {
                "PayloadType": "com.apple.ManagedClient.preferences",
                "PayloadVersion": 1,
                "PayloadIdentifier": f"{identifier}.mcx",
                "PayloadUUID": _uuid(f"{identifier}.mcx"),
                "PayloadDisplayName": "Managed preferences",
                "PayloadContent": {DOMAIN: {"Forced": [{"mcx_preference_settings": settings}]}},
            }
        ],
    }


def settings_of(profile):
    """The managed preferences a profile forces, or {} for one this script did not write."""
    try:
        return profile["PayloadContent"][0]["PayloadContent"][DOMAIN]["Forced"][0][
            "mcx_preference_settings"
        ]
    except (KeyError, IndexError, TypeError):
        return {}


def changes(before, after):
    """What differs, profile by profile and key by key, in a stable order.

    `before` and `after` are {file name: settings}. A profile only in `before` is one the
    console no longer produces: it is removed here, and must be removed from the MDM too.
    """
    out = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        else:
            kind = "changed"
        old, new = old or {}, new or {}
        keys = [
            {"key": key, "from": old.get(key), "to": new.get(key)}
            for key in sorted(set(old) | set(new))
            if old.get(key) != new.get(key)
        ]
        out.append({"profile": name, "change": kind, "keys": keys})
    return out


def profile_bytes(profile):
    """A profile as the file console writes it: an XML plist with sorted keys."""
    return plistlib.dumps(profile, sort_keys=True)
