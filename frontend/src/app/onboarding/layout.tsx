// ─────────────────────────────────────────────────────────────────────
// Onboarding wizard layout — minimal chrome.
//
// Sits outside the /dashboard layout so the sidebar nav doesn't
// distract from the wizard's linear flow. Brand-top, content-center,
// optional footer. Auth still required — the layout doesn't gate
// auth itself; the page does on mount (because the wizard happens
// AFTER login, not before).
// ─────────────────────────────────────────────────────────────────────

export default function OnboardingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      <header className="border-b border-gray-100 bg-white">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
          <span className="text-base font-bold text-primary-700">
            atlas
          </span>
          <span className="text-xs text-gray-500">Setup</span>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-10">{children}</main>
    </div>
  );
}
