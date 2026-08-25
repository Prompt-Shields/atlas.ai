/**
 * Locale resolution (M7, issue #251).
 *
 * Pure function shared by `middleware.ts` (request-time, no cookie yet) and
 * `app/layout.tsx` (reads the cookie `middleware.ts` set on the prior
 * request). Mirrors AISPM's cookie → Accept-Language precedence; atlas has
 * no IP-geolocation step, so that AISPM stage is dropped.
 */

import { DEFAULT_LOCALE, isLocale, type Locale } from './config';

/** Parses an `Accept-Language` header into locale tags ordered by q-value. */
function parseAcceptLanguage(header: string | null | undefined): string[] {
  if (!header) return [];
  return header
    .split(',')
    .map((part) => {
      const [tag, qPart] = part.trim().split(';q=');
      const q = qPart ? parseFloat(qPart) : 1;
      return { tag: tag.trim().toLowerCase(), q: Number.isNaN(q) ? 1 : q };
    })
    .sort((a, b) => b.q - a.q)
    .map((entry) => entry.tag);
}

export function resolveLocale(
  cookieValue: string | null | undefined,
  acceptLanguageHeader: string | null | undefined,
): Locale {
  if (isLocale(cookieValue)) return cookieValue;

  for (const tag of parseAcceptLanguage(acceptLanguageHeader)) {
    const primary = tag.split('-')[0];
    if (isLocale(primary)) return primary;
    if (isLocale(tag)) return tag;
  }

  return DEFAULT_LOCALE;
}
