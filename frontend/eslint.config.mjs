// Flat config. `next lint` was deprecated in Next 15 and removed in Next 16, so
// linting runs through the ESLint CLI directly and needs a config of its own —
// ESLint 9+ no longer reads .eslintrc.
//
// Both entry points re-export the shared base, so the spread below repeats it;
// that is how Next's own flat-config example is written, and later entries win.
// `eslint-config-next/typescript` also contributes the ignores for .next/,
// out/, build/ and next-env.d.ts.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

export default [
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    settings: {
      // Pinned rather than "detect". eslint-config-next@16 bundles an
      // eslint-plugin-react whose version *detection* calls an ESLint API
      // removed in ESLint 10, which crashes the whole run before any file is
      // linted. Setting an explicit version skips that code path entirely.
      react: { version: "19.2" },
    },
  },

  // ── Adopted as warnings, deliberately ───────────────────────────────
  //
  // `next lint` has been broken since the Next 16 upgrade, so neither rule
  // has ever gated a commit. Turning them on as errors now would block every
  // PR on ~100 pre-existing findings that have nothing to do with the change
  // under review. They stay visible in the lint output and are tracked as
  // their own work; the rest of the ruleset gates as normal.
  //
  //   react-hooks/set-state-in-effect (43) — new in eslint-plugin-react-hooks
  //     v6, which ships with Next 16. It never existed before, so these are
  //     newly-flagged existing patterns rather than new bugs. Clearing them
  //     means restructuring data-loading effects across the dashboard, which
  //     changes runtime behaviour and needs the app exercised to verify.
  //
  //   @typescript-eslint/no-explicit-any (54) — pre-existing typing debt.
  //
  // Raise each back to "error" as its backlog reaches zero.
  {
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
];
