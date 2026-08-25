// ─────────────────────────────────────────────────────────────────────
// MDM Configuration Profile generator
//
// Pure functions that emit per-MDM artefacts to force-install the
// Promptly browser extension on managed devices. See
// docs/mdm-strategy.md §3.3 "Deployment recipes per MDM" — this
// module is the v0.3 deliverable from that build plan.
//
// Architecture:
//   - One ProfileBundle per MDM (Intune / Jamf / Kandji / JumpCloud)
//   - Each bundle has a primary file + optional secondary files +
//     a step-by-step install guide rendered as a markdown-ish list
//   - Functions take ProfileInput (extensionId + tenantHandle +
//     atlasOrigin) and return ProfileBundle ready for download
//
// The Promptly extension ID is the Chrome Web Store ID. Once the
// extension is published to the store the ID is fixed and shared
// across customers. The tenant id is baked into the profile via
// chrome.storage.managed so the extension self-registers with the
// right atlas tenant on first launch.
// ─────────────────────────────────────────────────────────────────────

export interface ProfileInput {
  /** Chrome Web Store extension ID, 32 lowercase letters. */
  extensionId: string;
  /** Tenant slug (URL-safe), baked into managed storage. */
  tenantHandle: string;
  /** Atlas API origin the extension should phone home to. */
  atlasOrigin: string;
}

export interface ProfileFile {
  filename: string;
  mimeType: string;
  contents: string;
}

export interface ProfileBundle {
  mdm: 'MICROSOFT_INTUNE' | 'JAMF_PRO' | 'KANDJI' | 'JUMPCLOUD';
  /** Files the IT admin downloads. The first is the primary artefact. */
  files: ProfileFile[];
  /** Numbered install steps shown above the download buttons. */
  installSteps: string[];
  /** Optional caveats or version requirements. */
  notes: string[];
}

// ─── Helpers ──────────────────────────────────────────────────────────

/** Generate a UUID-shaped string deterministically from a seed for plist
 *  PayloadUUIDs. (Stable per tenant so re-downloads don't churn UUIDs.)
 */
function seededUuid(seed: string): string {
  // Simple FNV-1a → expanded to a 32-hex string. Not cryptographic;
  // just stable.
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i += 1) {
    h = (h ^ seed.charCodeAt(i)) >>> 0;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  const hex = h.toString(16).padStart(8, '0');
  return `${hex}-${hex.slice(0, 4)}-4${hex.slice(1, 4)}-9${hex.slice(2, 5)}-${hex}${hex.slice(0, 4)}`;
}

function xmlEscape(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

const CHROME_UPDATE_URL = 'https://clients2.google.com/service/update2/crx';

function forceListEntry(extId: string): string {
  return `${extId};${CHROME_UPDATE_URL}`;
}

// ─── Microsoft Intune ─────────────────────────────────────────────────
//
// Intune Settings Catalog uses a JSON payload. Admin uploads via
// Devices > Configuration > Create profile > Settings catalog →
// paste JSON or attach the file.

export function intuneProfile(input: ProfileInput): ProfileBundle {
  const body = {
    '@odata.type': '#microsoft.graph.deviceManagementConfigurationPolicy',
    name: 'Atlas — Promptly Extension Force-Install',
    description:
      'Force-installs the Promptly browser extension via Chrome managed policy. ' +
      `Bound to atlas tenant: ${input.tenantHandle}.`,
    platforms: 'windows10',
    technologies: 'mdm',
    settings: [
      {
        '@odata.type': '#microsoft.graph.deviceManagementConfigurationSetting',
        settingInstance: {
          '@odata.type':
            '#microsoft.graph.deviceManagementConfigurationSimpleSettingCollectionInstance',
          settingDefinitionId:
            'user_google_chrome_extensions~extensioninstallforcelist',
          simpleSettingCollectionValue: [
            {
              '@odata.type':
                '#microsoft.graph.deviceManagementConfigurationStringSettingValue',
              value: forceListEntry(input.extensionId),
            },
          ],
        },
      },
    ],
    // Atlas tenant baked into managed-app config so the extension
    // self-registers on launch.
    atlasTenant: {
      handle: input.tenantHandle,
      origin: input.atlasOrigin,
    },
  };
  return {
    mdm: 'MICROSOFT_INTUNE',
    files: [
      {
        filename: 'atlas-promptly-intune-profile.json',
        mimeType: 'application/json',
        contents: JSON.stringify(body, null, 2),
      },
    ],
    installSteps: [
      'Sign in to https://intune.microsoft.com as a Global or Intune Administrator.',
      'Go to Devices → Configuration → Create → New policy.',
      'Platform: Windows 10 and later. Profile type: Settings catalog.',
      'Click Next. In the Settings picker, search for "Google Chrome > Extensions" and add "Configure the list of force-installed apps and extensions".',
      'Paste the value from the downloaded JSON\'s settingValue (or upload the JSON directly via Endpoint Manager API).',
      'Assign the profile to your managed-device security group.',
      'Save. The extension installs on next device sync (typically <15 min).',
    ],
    notes: [
      'macOS support requires a separate profile targeting the macOS platform with the same Chrome key.',
      'Edge users: use the equivalent "Microsoft Edge > Extensions" settings path instead.',
    ],
  };
}

// ─── Jamf Pro ─────────────────────────────────────────────────────────
//
// Jamf pushes a .mobileconfig (signed plist) via Computer
// Configuration Profile → Custom Settings (Application & Custom
// Settings → Upload). Targets the com.google.Chrome plist preferences.

export function jamfProfile(input: ProfileInput): ProfileBundle {
  const payloadUuid = seededUuid(`jamf-${input.tenantHandle}`);
  const profileUuid = seededUuid(`jamf-prof-${input.tenantHandle}`);
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadType</key>
      <string>com.google.Chrome</string>
      <key>PayloadIdentifier</key>
      <string>com.atlas.promptly.chrome.${xmlEscape(input.tenantHandle)}</string>
      <key>PayloadUUID</key>
      <string>${payloadUuid}</string>
      <key>PayloadDisplayName</key>
      <string>Atlas — Promptly Chrome Force-Install</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
      <key>ExtensionInstallForcelist</key>
      <array>
        <string>${xmlEscape(forceListEntry(input.extensionId))}</string>
      </array>
      <key>ExtensionSettings</key>
      <dict>
        <key>${xmlEscape(input.extensionId)}</key>
        <dict>
          <key>installation_mode</key>
          <string>force_installed</string>
          <key>update_url</key>
          <string>${CHROME_UPDATE_URL}</string>
        </dict>
      </dict>
      <key>AtlasTenantHandle</key>
      <string>${xmlEscape(input.tenantHandle)}</string>
      <key>AtlasOrigin</key>
      <string>${xmlEscape(input.atlasOrigin)}</string>
    </dict>
  </array>
  <key>PayloadDescription</key>
  <string>Force-installs the Promptly browser extension via Chrome managed policy. Bound to atlas tenant ${xmlEscape(input.tenantHandle)}.</string>
  <key>PayloadDisplayName</key>
  <string>Atlas — Promptly Extension</string>
  <key>PayloadIdentifier</key>
  <string>com.atlas.promptly.${xmlEscape(input.tenantHandle)}</string>
  <key>PayloadOrganization</key>
  <string>Atlas (Promptly)</string>
  <key>PayloadRemovalDisallowed</key>
  <true/>
  <key>PayloadScope</key>
  <string>System</string>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>${profileUuid}</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>
`;
  return {
    mdm: 'JAMF_PRO',
    files: [
      {
        filename: 'atlas-promptly-jamf.mobileconfig',
        mimeType: 'application/x-apple-aspen-config',
        contents: xml,
      },
    ],
    installSteps: [
      'Sign in to Jamf Pro as a privileged admin.',
      'Go to Computers → Configuration Profiles → New.',
      'Under Application & Custom Settings → Upload, set Preference Domain to com.google.Chrome and upload the downloaded .mobileconfig file.',
      'Scope the profile to the Smart Group or Static Group containing your managed Macs.',
      'Save. Devices receive the profile on next check-in (~15 min); Chrome force-installs the extension on next launch.',
    ],
    notes: [
      'This profile targets Chrome only. For Edge on macOS use Preference Domain com.microsoft.Edge with the same ExtensionInstallForcelist key.',
      'PayloadRemovalDisallowed=true prevents end-users from uninstalling the profile. Toggle to <false/> if you want it removable.',
    ],
  };
}

// ─── Kandji ───────────────────────────────────────────────────────────
//
// Kandji uses Custom Profiles (same plist format as Jamf). The
// profile content is identical; the difference is in the upload
// flow. We reuse jamfProfile() and rename the file.

export function kandjiProfile(input: ProfileInput): ProfileBundle {
  const jamf = jamfProfile(input);
  return {
    mdm: 'KANDJI',
    files: [
      {
        filename: 'atlas-promptly-kandji.mobileconfig',
        mimeType: jamf.files[0].mimeType,
        contents: jamf.files[0].contents,
      },
    ],
    installSteps: [
      'Sign in to https://app.kandji.io as an admin.',
      'Go to Library → Add new → Custom Profile.',
      'Upload the downloaded .mobileconfig file.',
      'Set the Assignment to the Blueprint(s) for your managed Macs.',
      'Save and assign. Devices receive the profile on next sync.',
    ],
    notes: [
      'Same payload format as Jamf — the file is interchangeable. Kandji adds its own UI flow around upload + scope.',
    ],
  };
}

// ─── JumpCloud ────────────────────────────────────────────────────────
//
// JumpCloud uses Commands. We emit two scripts: one for macOS (writes
// to the managed-preferences plist) and one for Windows (writes to
// HKLM registry).

export function jumpcloudProfile(input: ProfileInput): ProfileBundle {
  const entry = forceListEntry(input.extensionId);
  const mac = `#!/bin/bash
# Atlas — Promptly extension force-install for macOS via JumpCloud Command.
# Tenant: ${input.tenantHandle}
set -euo pipefail

PLIST="/Library/Managed Preferences/com.google.Chrome.plist"
sudo defaults write "$PLIST" ExtensionInstallForcelist -array-add "${entry}"
sudo defaults write "$PLIST" AtlasTenantHandle -string "${input.tenantHandle}"
sudo defaults write "$PLIST" AtlasOrigin -string "${input.atlasOrigin}"
sudo chmod 644 "$PLIST"

echo "Promptly extension force-install installed for atlas tenant ${input.tenantHandle}"
`;
  const win = `@echo off
REM Atlas — Promptly extension force-install for Windows via JumpCloud Command.
REM Tenant: ${input.tenantHandle}

REM Chrome
reg add "HKLM\\Software\\Policies\\Google\\Chrome\\ExtensionInstallForcelist" /v 1 /t REG_SZ /d "${entry}" /f
reg add "HKLM\\Software\\Policies\\Atlas" /v TenantHandle /t REG_SZ /d "${input.tenantHandle}" /f
reg add "HKLM\\Software\\Policies\\Atlas" /v AtlasOrigin /t REG_SZ /d "${input.atlasOrigin}" /f

REM Edge — same extension ID, different policy hive
reg add "HKLM\\Software\\Policies\\Microsoft\\Edge\\ExtensionInstallForcelist" /v 1 /t REG_SZ /d "${entry}" /f

echo Promptly extension force-install installed for atlas tenant ${input.tenantHandle}
`;
  return {
    mdm: 'JUMPCLOUD',
    files: [
      {
        filename: 'atlas-promptly-jumpcloud-macos.sh',
        mimeType: 'application/x-sh',
        contents: mac,
      },
      {
        filename: 'atlas-promptly-jumpcloud-windows.bat',
        mimeType: 'application/bat',
        contents: win,
      },
    ],
    installSteps: [
      'Sign in to https://console.jumpcloud.com.',
      'Go to DEVICE MANAGEMENT → Commands → New Command.',
      'For Mac fleet: select Mac, Run As root, paste the macOS shell script.',
      'For Windows fleet: select Windows, paste the Windows .bat content into a Command of type Batch.',
      'Bind each Command to the Device Group containing your managed endpoints.',
      'Save & Run. The Command runs once per device and persists the extension policy.',
    ],
    notes: [
      'JumpCloud Commands run once. For new devices you add to the group later, JumpCloud auto-runs the Command on enrollment.',
      'macOS device users will see "Managed by your organisation" in Chrome → settings → Extensions after the next Chrome launch.',
    ],
  };
}

// ─── Public entry point ──────────────────────────────────────────────

export function profileFor(
  provider: string,
  input: ProfileInput,
): ProfileBundle | null {
  switch (provider) {
    case 'MICROSOFT_INTUNE':
      return intuneProfile(input);
    case 'JAMF_PRO':
      return jamfProfile(input);
    case 'KANDJI':
      return kandjiProfile(input);
    case 'JUMPCLOUD':
      return jumpcloudProfile(input);
    default:
      return null;
  }
}

// ─── Default placeholder values ──────────────────────────────────────
// Production swaps these out for the published Chrome Web Store
// extension ID + the live atlas tenant handle.

export const DEFAULT_EXTENSION_ID = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
export const DEFAULT_ATLAS_ORIGIN = 'https://app.atlas.example';
