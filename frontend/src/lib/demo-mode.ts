/**
 * Demo fallback gate for AISPM / curated-demo pages.
 * - NEXT_PUBLIC_DEMO_MODE=1 → always allow demo seeds
 * - NEXT_PUBLIC_DEMO_MODE=0 → never allow (even in development)
 * - unset → allow only when NODE_ENV === "development"
 */
export function isDemoFallbackEnabled(): boolean {
  const flag = process.env.NEXT_PUBLIC_DEMO_MODE;
  if (flag === "1") return true;
  if (flag === "0") return false;
  return process.env.NODE_ENV === "development";
}

export function useLiveOrDemo<T>(
  live: T | null | undefined,
  demo: T,
): { data: T | null; isDemo: boolean } {
  if (live != null) return { data: live, isDemo: false };
  if (isDemoFallbackEnabled()) return { data: demo, isDemo: true };
  return { data: null, isDemo: false };
}
