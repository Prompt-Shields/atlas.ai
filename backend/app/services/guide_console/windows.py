"""Windows registry policy from the same console document (promptly-guide #73).

Ported from promptly-guide ``Scripts/console/make-windows-policy.py`` (main after promptly-guide#310 and #315):
the ADMX template and its en-US ADML that an admin imports into Intune ("Import ADMX")
or the Group Policy central store, and ``.reg`` files with the values a PC would end up
with, for trying a build on a test PC. ``tests/unit/test_guide_console_profiles.py``
holds them against what the file console wrote. Change the file console first.

The console document is checked by ``profiles.build``, unchanged, so a document the Mac
side refuses is refused here too, and the registry values are the settings ``build``
produces, translated only where Windows differs. Those differences wait on owner
decisions (promptly-guide ``docs/research/windows-deployment.md``); the constants below
are the file console's assumptions, kept the same.
"""

from __future__ import annotations

import json
import re
from xml.sax.saxutils import escape

from app.services.guide_console import profiles as mp

# ---------------------------------------------------------------------------------------
# Assumed decisions (docs/research/windows-deployment.md, "Open decisions for the owner").
# ---------------------------------------------------------------------------------------

# 3. The key under HKLM\SOFTWARE\Policies. In every ADMX and every customer's policies,
#    so it is chosen once.
VENDOR_KEY = r"PromptShields\PromptlyGuide"

# 5. How an excluded app is named on Windows: "executable" (casebook.exe),
#    "packageFamilyName" (Nordlys.Casebook_8wekyb3d8bbwe), or "both". Taken from the
#    console's excludedWindowsApps; a bundle id in excludedApps means nothing on Windows.
APP_IDS = "executable"

# How a list is stored: "list" (a subkey with values 1, 2, ...; Group Policy has always
# supported it) or "multiText" (one REG_MULTI_SZ; whether Intune's import takes it is
# unverified).
LIST_ELEMENT = "list"

# 4. Groups: "PolicyGroups" (a fixed ADMX; each PC gets one policy naming all its groups,
#    with the groups' settings merged in, so one Intune profile per combination of
#    groups) or "perGroup" (a policy per group and per group variant, as on the Mac; the
#    ADMX changes, and Intune needs a re-import, whenever a group is added).
GROUPS = "PolicyGroups"

# KnowledgeSources: the console's array of records as JSON in one REG_SZ. ADMX has no
# element for a list of records, and JSON keeps the console's checks intact. The only
# encoding written so far.
SOURCES = "json"

# Paths (pack folders, knowledge source folders) follow the Mac rule: "/..." or "~/...".
# "home-only" keeps the "~/" ones, which the Windows client is assumed to read as the
# person's profile folder, and leaves out absolute Mac paths, which mean nothing on a PC.
# "verbatim" writes every path as given. A Windows path rule is not decided yet.
PATHS = "home-only"

# ---------------------------------------------------------------------------------------

ConsoleError = mp.ConsoleError

HIVE = "HKEY_LOCAL_MACHINE"
BASE_KEY = f"SOFTWARE\\Policies\\{VENDOR_KEY}"
APP_NAME = VENDOR_KEY.split("\\")[-1]
ADMX_FILE = f"{APP_NAME}.admx"
ADML_FILE = f"en-US/{APP_NAME}.adml"
NAMESPACE = f"{VENDOR_KEY.split(chr(92))[0]}.Policies.{APP_NAME}"
PREFIX = APP_NAME.lower()
ADMX_NS = "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"

WINDOWS_APPS_FIELD = "excludedWindowsApps"
KEY_POLICY_GROUPS = "PolicyGroups"
# A text element holds at most this much. The JSON of the knowledge sources must fit.
SOURCES_MAX = 32767

APP_RULES = {
    # A file name, no folder, ending in .exe.
    "executable": (
        re.compile(r'^[^\\/:*?"<>|\x00-\x1f]+\.exe$', re.IGNORECASE),
        "an executable name such as casebook.exe",
    ),
    # Name, underscore, the 13-character publisher id.
    "packageFamilyName": (
        re.compile(r"^[A-Za-z0-9.-]{3,50}_[a-z0-9]{13}$"),
        "a package family name such as Nordlys.Casebook_8wekyb3d8bbwe",
    ),
}

# Every key make-profiles.py writes, and what it becomes on Windows. Keys with a group
# variant (Key.<group>) on the Mac have one here too. The test checks this covers every
# key build() can write.
#   kind: switch (REG_DWORD 1), list, text (REG_SZ), json (REG_SZ), enum (REG_SZ),
#         decimal (REG_DWORD)
# (key, kind, category, display name, explanation)
POLICIES = [
    (
        mp.KEY_CLOUD_DISABLED,
        "switch",
        "Cloud",
        "Turn off the cloud model",
        "Enabled: Guide answers with the on-device model only. Disabled or not configured: "
        "each person decides. Only Enabled restricts; no policy turns the cloud model on.",
    ),
    (
        mp.KEY_REQUIRE_EU,
        "switch",
        "Cloud",
        "Use the cloud model only with processing in the EU",
        "Enabled: Guide uses a cloud model only where the request is processed in the EU.",
    ),
    (
        mp.KEY_MEMORY_DISABLED,
        "switch",
        "Memory",
        "Turn off memory",
        "Enabled: memory's notes are not used and none can be added.",
    ),
    (
        "ExcludedApps",
        "list",
        "Exclusions",
        "Excluded apps",
        "Apps Guide never looks at, added to the built-in exclusions and the person's own. {apps}",
    ),
    (
        "ExcludedWindowTitles",
        "list",
        "Exclusions",
        "Excluded window titles",
        "Windows whose title matches are never looked at. * and ? are wildcards; a pattern needs "
        "two letters or digits besides them.",
    ),
    (
        "ExcludedSites",
        "list",
        "Exclusions",
        "Excluded sites",
        "Sites Guide never looks at, one host per entry, such as hr.example.com.",
    ),
    (
        "SkillPacksOff",
        "list",
        "SkillPacks",
        "Skill packs turned off",
        "Guide's own skill packs to turn off, by id: " + ", ".join(sorted(mp.KNOWN_PACKS)) + ".",
    ),
    (
        "SkillPackFolders",
        "list",
        "SkillPacks",
        "Folders with your own skill packs",
        "Folders holding skill packs for your organisation's own apps. A path starting with ~/ "
        "is in the person's profile folder. Check the packs with check-packs.py first.",
    ),
    (
        mp.KEY_SOURCES,
        "json",
        "Knowledge",
        "Knowledge sources",
        "The knowledge sources as JSON, written by make-windows-policy.py from the console file. "
        "Paste its output; do not edit by hand.",
    ),
    (
        mp.KEY_ADOPTION_TEAM,
        "text",
        "Adoption",
        "Adoption team",
        "The team this PC's adoption figures count under: a team, never a person, and sent only "
        "for someone who opted in on their own PC.",
    ),
    (
        mp.KEY_ATLAS_URL,
        "text",
        "Adoption",
        "Atlas.AI address",
        "Where adoption figures go, for the people who opted in. https only.",
    ),
    (
        mp.KEY_WORK_CLIENT_NOTICES,
        "switch",
        "WorkClient",
        "Show the Work client's 90-day notices",
        "Enabled: this PC is an admin's, and gets one notice each at 14, 7 and 1 days left of the "
        "free Work client's 90 days and one when they end. Set it only in the policy for the PCs "
        "of the people who look after Guide: an employee's PC never gets one. Reads and sends "
        "nothing.",
    ),
    ("SSOFirebaseAPIKey", "text", "SignIn", "Firebase web API key", "From docs/sso.md."),
    ("SSOFirebaseProjectID", "text", "SignIn", "Firebase project id", "From docs/sso.md."),
    ("SSOProvider", "text", "SignIn", "Identity provider", "oidc.<name> or saml.<name>."),
    ("SSOTenantID", "text", "SignIn", "Identity Platform tenant id", "Only with a tenant."),
    ("SSOSignInHost", "text", "SignIn", "Sign-in host", "Such as your-project.firebaseapp.com."),
    (
        "SSOProcessingArea",
        "enum",
        "SignIn",
        "Where sign-ins are processed",
        "Where the Firebase project processes sign-ins.",
    ),
    (
        "SSOGroupsClaim",
        "text",
        "SignIn",
        "Groups claim",
        "The claim or SAML attribute that holds the person's groups.",
    ),
    (
        "SSOGroupsValidForHours",
        "decimal",
        "SignIn",
        "Hours a sign-in's groups stay in force",
        "1 to 168. Not configured: 12.",
    ),
    (
        mp.KEY_GROUPS_FROM_ATLAS,
        "switch",
        "SignIn",
        "Groups come from Atlas.AI over SCIM",
        "Enabled: your directory provisions people and groups into your Atlas.AI over SCIM, and "
        "Guide asks Atlas for a signed-in person's groups. Disabled or not configured: Guide never "
        "asks, since the request names the person to Atlas.",
    ),
]
PROCESSING_AREAS = [
    ("eu", "EU"),
    ("eea", "EEA"),
    ("adequacy", "A country with an EU adequacy decision"),
    ("elsewhere", "Elsewhere"),
]
GROUPS_POLICY = (
    KEY_POLICY_GROUPS,
    "list",
    "Knowledge",
    "Groups this PC is in",
    "The groups this PC is in, which decide its knowledge sources. Set in one "
    "policy per combination of groups: two policies setting it would conflict.",
)
GROUP_SWITCH = (
    "KnowledgeGroup",
    "switch",
    None,
    "Put this PC in the group",
    "Enabled: this PC is in the group, for its knowledge sources and settings.",
)
CATEGORIES = [
    ("Cloud", "Cloud model"),
    ("Memory", "Memory"),
    ("Exclusions", "Exclusions"),
    ("SkillPacks", "Skill packs"),
    ("Knowledge", "Knowledge"),
    ("Adoption", "Adoption figures"),
    ("WorkClient", "Work client"),
    ("SignIn", "Sign-in"),
]
SIGN_IN = set(mp.SIGN_IN_KEYS.values())
# The keys a group can set a variant of: the group fields' keys.
VARIANT_KEYS = [
    mp.KEY_MEMORY_DISABLED,
    mp.KEY_ADOPTION_TEAM,
    mp.KEY_WORK_CLIENT_NOTICES,
    *mp.LIST_KEYS.values(),
]

# Keys the Mac profile writes that Windows does not carry, and why. They are left out of
# every .reg file. ExcludedApps maps, but from excludedWindowsApps, not the bundle ids.
MAC_ONLY = dict.fromkeys(
    mp.CLOUD_ENDPOINT_KEYS.values(),
    "The organisation's own model endpoint (#40) pins the Mac's cloud model setup "
    "(ManagedEndpoint.swift); the Windows core has no cloud model setup to pin yet.",
)
# Mac keys written under another name or shape on Windows.
RENAMED = {
    mp.KEY_GROUP_PREFIX: "PolicyGroups entry (PolicyGroups) or KnowledgeGroup.<name> (perGroup)"
}


def _check_config():
    allowed = {
        "APP_IDS": (APP_IDS, {"executable", "packageFamilyName", "both"}),
        "LIST_ELEMENT": (LIST_ELEMENT, {"list", "multiText"}),
        "GROUPS": (GROUPS, {"PolicyGroups", "perGroup"}),
        "SOURCES": (SOURCES, {"json"}),
        "PATHS": (PATHS, {"home-only", "verbatim"}),
    }
    for name, (value, options) in allowed.items():
        if value not in options:
            raise ConsoleError(f"{name} = {value!r}: one of {', '.join(sorted(options))}.")
    if not re.match(r"^[A-Za-z0-9]+\\[A-Za-z0-9]+$", VENDOR_KEY):
        raise ConsoleError(f"VENDOR_KEY = {VENDOR_KEY!r}: <vendor>\\<app>, letters and digits.")


def _ident(key):
    """An ADMX/ADML id for a value name: dots are not used in ids by convention."""
    return key.replace(".", "_")


def _base(key):
    return key.split(".", 1)[0]


def _policy(key):
    """The POLICIES row for a key or one of its variants."""
    if key == KEY_POLICY_GROUPS:
        return GROUPS_POLICY
    if key.startswith(mp.KEY_GROUP_PREFIX):
        return GROUP_SWITCH
    for row in POLICIES:
        if row[0] == _base(key):
            return row
    raise KeyError(key)


# --- Settings: what build() produces, translated for Windows -----------------------------


def _windows_apps(section, where):
    if WINDOWS_APPS_FIELD not in section:
        return []
    values = mp._strings(section[WINDOWS_APPS_FIELD], f"{where}.{WINDOWS_APPS_FIELD}")
    kinds = ["executable", "packageFamilyName"] if APP_IDS == "both" else [APP_IDS]
    out = []
    for value in values:
        text = value.strip()
        if not any(APP_RULES[k][0].match(text) for k in kinds):
            raise ConsoleError(
                f"{where}.{WINDOWS_APPS_FIELD}: {value!r} is not "
                f"{' or '.join(APP_RULES[k][1] for k in kinds)}."
            )
        out.append(text)
    return out


def _path_kept(path):
    return PATHS == "verbatim" or path.startswith("~/")


def _translate(settings, windows_apps, notes, where):
    """One Mac profile's settings as Windows values, by the same names."""
    out = {}
    for key, value in settings.items():
        base = _base(key)
        if base in MAC_ONLY:
            continue
        if base == "ExcludedApps":
            if not windows_apps.get(key):
                notes.append(
                    f"{where}: {key} names Mac bundle ids, and {WINDOWS_APPS_FIELD} is "
                    "not set, so Windows PCs get no excluded apps from it."
                )
            continue
        if base == "SkillPackFolders":
            kept = [p for p in value if _path_kept(p)]
            for p in value:
                if p not in kept:
                    notes.append(f"{where}: {key}: {p!r} is a Mac path, left out on Windows.")
            if kept:
                out[key] = kept
            continue
        if key == mp.KEY_SOURCES:
            kept = []
            for source in value:
                if _path_kept(source["Folder"]):
                    kept.append(source)
                else:
                    notes.append(
                        f"{where}: knowledge source {source['Id']!r} is in a Mac path, "
                        f"{source['Folder']!r}, and is left out on Windows."
                    )
            if kept:
                text = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
                if len(text) > SOURCES_MAX:
                    raise ConsoleError(
                        f"knowledgeSources: {len(text)} characters as JSON; a policy "
                        f"holds at most {SOURCES_MAX}."
                    )
                out[key] = text
            continue
        out[key] = value
    for key, apps in windows_apps.items():
        if apps:
            out[key] = apps
    return out


def _merge(everyone, groups):
    """The values one PC in `groups` gets under PolicyGroups: everyone's, with each group's
    variants folded into the key itself the way Guide reads them (ManagedSettings.swift):
    lists joined, a switch on if any is on, a team only when the groups agree."""
    out = dict(everyone)
    for name in groups:
        for key, value in groups[name].items():
            if key.startswith(mp.KEY_GROUP_PREFIX):
                continue
            base = _base(key)
            if isinstance(value, list):
                joined = out.get(base, []) + [v for v in value if v not in out.get(base, [])]
                out[base] = joined
            elif value is True:
                out[base] = True
    teams = {
        groups[n][f"{mp.KEY_ADOPTION_TEAM}.{n}"]
        for n in groups
        if f"{mp.KEY_ADOPTION_TEAM}.{n}" in groups[n]
    }
    if len(teams) == 1:
        out[mp.KEY_ADOPTION_TEAM] = teams.pop()
    elif len(teams) > 1:
        # Guide gives a PC in two teams no team; leaving the key out says the same.
        out.pop(mp.KEY_ADOPTION_TEAM, None)
    if groups:
        out[KEY_POLICY_GROUPS] = sorted(groups)
    return out


def build_windows(console, combinations=()):
    """(policy keys for the ADMX, {reg file name: (owned keys, values)}, notes).

    Raises ConsoleError; writes nothing."""
    _check_config()
    profiles = mp.build(console)  # every Mac check, unchanged
    notes = []
    everyone_section = console.get("everyone", {})
    groups_section = console.get("groups", {})

    everyone = _translate(
        mp.settings_of(profiles[mp.EVERYONE_FILE]),
        {"ExcludedApps": _windows_apps(everyone_section, "everyone")},
        notes,
        "everyone",
    )
    if "cloudEndpoint" in console:
        notes.append(
            "cloudEndpoint pins the Mac's cloud model only. Windows PCs have no cloud model "
            "setup to pin yet, so it is left out of every .reg file."
        )
    groups = {}
    for file_name, profile in profiles.items():
        if file_name == mp.EVERYONE_FILE:
            continue
        name = file_name[len("promptly-guide-group-") : -len(".mobileconfig")]
        section = groups_section.get(name, {})
        apps = {f"ExcludedApps.{name}": _windows_apps(section, f"groups.{name}")}
        groups[name] = _translate(mp.settings_of(profile), apps, notes, f"groups.{name}")

    base_keys = [row[0] for row in POLICIES]
    reg = {}
    if GROUPS == "PolicyGroups":
        policy_keys = [*base_keys, KEY_POLICY_GROUPS]
        wanted = [()] + [(n,) for n in sorted(groups)]
        for combo in combinations:
            unknown = sorted(set(combo) - set(groups))
            if unknown:
                raise ConsoleError(
                    f"--pc-groups: no group {', '.join(unknown)} in the console file."
                )
            wanted.append(tuple(sorted(set(combo))))
        for combo in dict.fromkeys(wanted):
            file_name = (
                "promptly-guide-everyone.reg"
                if not combo
                else f"promptly-guide-groups-{'+'.join(combo)}.reg"
            )
            reg[file_name] = (policy_keys, _merge(everyone, {n: groups[n] for n in combo}))
    else:
        policy_keys = list(base_keys)
        reg["promptly-guide-everyone.reg"] = (base_keys, everyone)
        for name in sorted(groups):
            owned = [mp.KEY_GROUP_PREFIX + name] + [f"{k}.{name}" for k in VARIANT_KEYS]
            policy_keys += owned
            reg[f"promptly-guide-group-{name}.reg"] = (owned, groups[name])
    return policy_keys, reg, notes


# --- ADMX and ADML ------------------------------------------------------------------------


def _category(key):
    if key.startswith(mp.KEY_GROUP_PREFIX) or (GROUPS == "perGroup" and "." in key):
        return f"Group_{_ident(key.split('.', 1)[1])}"
    return _policy(key)[2]


def _display(key):
    row = _policy(key)
    if "." in key:
        return f"{row[3]} ({key.split('.', 1)[1]})"
    return row[3]


def _explain(key):
    row = _policy(key)
    apps = {
        "executable": "One executable name per entry, such as casebook.exe.",
        "packageFamilyName": "One package family name per entry.",
        "both": "One executable name or package family name per entry.",
    }[APP_IDS]
    text = row[4].replace("{apps}", apps)
    if "." in key and not key.startswith(mp.KEY_GROUP_PREFIX):
        text += f" For the {key.split('.', 1)[1]} group only; Guide adds it to the setting for everyone."
    where = (
        f"{BASE_KEY}\\{key}"
        if row[1] == "list" and LIST_ELEMENT == "list"
        else f"{BASE_KEY}, value {key}"
    )
    return f"{text}\n\nRegistry: HKLM\\{where}."


def admx(policy_keys):
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- Generated by Scripts/console/make-windows-policy.py (#73). Do not edit. -->",
        f'<policyDefinitions xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" revision="1.0" schemaVersion="1.0" '
        f'xmlns="{ADMX_NS}">',
        "  <policyNamespaces>",
        f'    <target prefix="{PREFIX}" namespace="{NAMESPACE}" />',
        "  </policyNamespaces>",
        '  <resources minRequiredRevision="1.0" />',
        "  <supportedOn>",
        "    <definitions>",
        '      <definition name="SUPPORTED_Guide" displayName="$(string.SUPPORTED_Guide)" />',
        "    </definitions>",
        "  </supportedOn>",
        "  <categories>",
        f'    <category name="{APP_NAME}" displayName="$(string.{APP_NAME})" />',
    ]
    used = list(dict.fromkeys(_category(k) for k in policy_keys))
    for cat in used:
        lines.append(f'    <category name="{cat}" displayName="$(string.{cat})">')
        lines.append(f'      <parentCategory ref="{APP_NAME}" />')
        lines.append("    </category>")
    lines += ["  </categories>", "  <policies>"]
    for key in policy_keys:
        kind = _policy(key)[1]
        pid = _ident(key)
        is_subkey = kind == "list" and LIST_ELEMENT == "list"
        attrs = (
            f'name="{pid}" class="Machine" displayName="$(string.{pid})" '
            f'explainText="$(string.{pid}_Explain)"'
        )
        if kind != "switch":
            attrs += f' presentation="$(presentation.{pid})"'
        attrs += f' key="{escape(BASE_KEY + chr(92) + key if is_subkey else BASE_KEY)}"'
        if kind == "switch":
            attrs += f' valueName="{escape(key)}"'
        lines.append(f"    <policy {attrs}>")
        lines.append(f'      <parentCategory ref="{_category(key)}" />')
        lines.append('      <supportedOn ref="SUPPORTED_Guide" />')
        if kind == "switch":
            # Only on restricts. Disabled deletes the value rather than writing 0, so it
            # reads the same as not configured.
            lines += [
                "      <enabledValue>",
                '        <decimal value="1" />',
                "      </enabledValue>",
                "      <disabledValue>",
                "        <delete />",
                "      </disabledValue>",
            ]
        else:
            lines.append("      <elements>")
            if kind == "list" and is_subkey:
                lines.append(f'        <list id="{pid}_List" valuePrefix="" />')
            elif kind == "list":
                lines.append(f'        <multiText id="{pid}_List" valueName="{escape(key)}" />')
            elif kind in ("text", "json"):
                limit = f' maxLength="{SOURCES_MAX}"' if kind == "json" else ""
                lines.append(
                    f'        <text id="{pid}_Text" valueName="{escape(key)}" required="true"{limit} />'
                )
            elif kind == "enum":
                lines.append(
                    f'        <enum id="{pid}_Enum" valueName="{escape(key)}" required="true">'
                )
                for value, _ in PROCESSING_AREAS:
                    sid = f"{pid}_{value}"
                    lines += [
                        f'          <item displayName="$(string.{sid})">',
                        f"            <value><string>{value}</string></value>",
                        "          </item>",
                    ]
                lines.append("        </enum>")
            elif kind == "decimal":
                low, high = mp.SIGN_IN_HOURS[1:]
                lines.append(
                    f'        <decimal id="{pid}_Number" valueName="{escape(key)}" required="true" '
                    f'minValue="{low}" maxValue="{high}" />'
                )
            lines.append("      </elements>")
        lines.append("    </policy>")
    lines += ["  </policies>", "</policyDefinitions>", ""]
    return "\r\n".join(lines)


def adml(policy_keys):
    strings = {"SUPPORTED_Guide": "Promptly Guide for Windows", APP_NAME: "Promptly Guide"}
    for cat, display in CATEGORIES:
        strings[cat] = display
    presentations = []
    for key in policy_keys:
        cat = _category(key)
        if cat.startswith("Group_"):
            strings[cat] = f"Group: {key.split('.', 1)[1]}"
        pid = _ident(key)
        kind = _policy(key)[1]
        strings[pid] = _display(key)
        strings[f"{pid}_Explain"] = _explain(key)
        label = escape(_display(key))
        if kind == "list":
            control = (
                f'<listBox refId="{pid}_List">{label}</listBox>'
                if LIST_ELEMENT == "list"
                else f'<multiTextBox refId="{pid}_List">{label}</multiTextBox>'
            )
        elif kind in ("text", "json"):
            control = f'<textBox refId="{pid}_Text"><label>{label}</label></textBox>'
        elif kind == "enum":
            control = f'<dropdownList refId="{pid}_Enum">{label}</dropdownList>'
            for value, display in PROCESSING_AREAS:
                strings[f"{pid}_{value}"] = display
        elif kind == "decimal":
            control = (
                f'<decimalTextBox refId="{pid}_Number" defaultValue="12">{label}</decimalTextBox>'
            )
        else:
            continue
        presentations.append(
            f'      <presentation id="{pid}">\r\n        {control}\r\n      </presentation>'
        )
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- Generated by Scripts/console/make-windows-policy.py (#73). Do not edit. -->",
        f'<policyDefinitionResources xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" revision="1.0" schemaVersion="1.0" '
        f'xmlns="{ADMX_NS}">',
        "  <displayName>Promptly Guide</displayName>",
        "  <description>Promptly Guide settings an organisation decides.</description>",
        "  <resources>",
        "    <stringTable>",
    ]
    for sid, text in strings.items():
        lines.append(f'      <string id="{sid}">{escape(text)}</string>')
    lines += [
        "    </stringTable>",
        "    <presentationTable>",
        *presentations,
        "    </presentationTable>",
        "  </resources>",
        "</policyDefinitionResources>",
        "",
    ]
    return "\r\n".join(lines)


# --- .reg ---------------------------------------------------------------------------------


def _reg_string(value, where):
    if any(c in value for c in "\r\n\x00"):
        raise ConsoleError(
            f"{where}: {value!r} has a line break, which a registry policy value "
            "cannot carry in a .reg file."
        )
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _multi_sz(values):
    data = "".join(v + "\x00" for v in values) + "\x00"
    return "hex(7):" + ",".join(f"{b:02x}" for b in data.encode("utf-16-le"))


def reg_text(owned, values):
    """A REG v5 file that sets exactly `values` among the `owned` keys: every owned value
    or list subkey is deleted first, so running it twice, or after another, leaves what it
    says and nothing older."""
    root = f"{HIVE}\\{BASE_KEY}"
    lines = [
        "Windows Registry Editor Version 5.00",
        "",
        "; Generated by Scripts/console/make-windows-policy.py (#73), for a test PC.",
        "; Deploy with the ADMX through Intune or Group Policy, not this file.",
        "",
        f"[{root}]",
    ]
    subkeys = []
    for key in owned:
        kind = _policy(key)[1]
        if kind == "list" and LIST_ELEMENT == "list":
            subkeys.append(key)
            continue
        if key not in values:
            lines.append(f'"{key}"=-')
            continue
        value = values[key]
        if kind == "switch":
            lines.append(f'"{key}"=dword:00000001')
        elif kind == "decimal":
            lines.append(f'"{key}"=dword:{value:08x}')
        elif kind == "list":
            for v in value:
                _reg_string(v, key)
            lines.append(f'"{key}"={_multi_sz(value)}')
        else:
            lines.append(f'"{key}"={_reg_string(value, key)}')
    for key in subkeys:
        lines += ["", f"[-{root}\\{key}]"]
        if values.get(key):
            lines += ["", f"[{root}\\{key}]"]
            lines += [f'"{n}"={_reg_string(v, key)}' for n, v in enumerate(values[key], start=1)]
    lines += ["", ""]
    # REG v5 is UTF-16 LE with a byte order mark and CRLF line ends: what regedit writes,
    # and the only encoding it reads that keeps non-ASCII text intact.
    return "﻿" + "\r\n".join(lines)


def files(console):
    """{path: bytes} as the file console writes them into its output folder: the ADMX,
    the en-US ADML and one .reg per PC group combination, plus the notes on what was
    left out on Windows. Raises ConsoleError."""
    policy_keys, reg, notes = build_windows(console)
    out = {
        ADMX_FILE: admx(policy_keys).encode("utf-8"),
        ADML_FILE: adml(policy_keys).encode("utf-8"),
    }
    for name, (owned, values) in reg.items():
        out[name] = reg_text(owned, values).encode("utf-16-le")
    return out, notes
