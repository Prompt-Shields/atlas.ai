/**
 * Locale configuration (M7, issue #251).
 *
 * Ported from AISPM's `lib/i18n/config` — atlas seeds `en` fully and `fr`/`nb`
 * for the primary nav + dashboard home page (see scope note on #251).
 */

export const LOCALES = ['en', 'fr', 'nb'] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = 'en';

export const LOCALE_LABELS: Record<Locale, string> = {
  en: 'English',
  fr: 'Français',
  nb: 'Norsk (Bokmål)',
};

export const LOCALE_COOKIE = 'NEXT_LOCALE';

export function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}
