// ─────────────────────────────────────────────────────────────────────
// drift-notes — parse the [atlas-drift] markers that the backend's
// drift_signal_dispatcher writes into UseCaseReview.notes.
//
// The schema (see app/services/drift_signals.encode_findings_for_notes):
//
//   [atlas-drift] kind=K signature=S severity=SEV
//   <title>
//   <detail>
//   ─
//
// Multiple findings are separated by a blank line. Free-form notes
// the reviewer adds when marking reviewed are NOT inside a marker
// block and are skipped by the parser.
// ─────────────────────────────────────────────────────────────────────

export interface ParsedDriftFinding {
  kind: string;
  signature: string;
  severity: string;
  title: string;
  detail: string;
}

const MARKER_PREFIX = '[atlas-drift]';

export function parseDriftFindings(notes: string | null): ParsedDriftFinding[] {
  if (!notes) return [];
  const findings: ParsedDriftFinding[] = [];

  // Split on the trailing ─ separator OR a blank line between blocks.
  // Each block looks like:
  //   [atlas-drift] kind=K signature=S severity=SEV
  //   <title>
  //   <detail line 1>
  //   <detail line 2>
  //   ─
  //
  // We split on the literal "─" — every block ends with one. Any
  // trailing user-authored content after the last "─" is ignored.
  const blocks = notes.split('─').map((b) => b.trim()).filter(Boolean);

  for (const block of blocks) {
    if (!block.startsWith(MARKER_PREFIX)) continue;
    const lines = block.split('\n').map((l) => l.trim()).filter(Boolean);
    if (lines.length < 2) continue;

    // Line 0: [atlas-drift] kind=K signature=S severity=SEV
    const header = lines[0];
    const kind = extract(header, /\bkind=([^\s]+)/);
    const signature = extract(header, /\bsignature=([^\s]+)/);
    const severity = extract(header, /\bseverity=([^\s]+)/);

    // Line 1: title
    const title = lines[1];
    // Lines 2..: detail (joined as space-separated for compactness in UI)
    const detail = lines.slice(2).join(' ');

    findings.push({
      kind: kind || 'UNKNOWN',
      signature: signature || '',
      severity: severity || 'LOW',
      title: title || '',
      detail,
    });
  }

  return findings;
}

function extract(line: string, regex: RegExp): string | null {
  const m = line.match(regex);
  return m ? m[1] : null;
}
