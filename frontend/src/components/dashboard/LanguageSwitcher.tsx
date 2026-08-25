'use client';

import { useLocale } from '@/lib/i18n/provider';
import { LOCALES, LOCALE_LABELS, isLocale } from '@/lib/i18n/config';

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useLocale();

  return (
    <label className="flex items-center gap-2 text-xs text-gray-500">
      <span className="sr-only">{t('common.languageSwitcherLabel')}</span>
      <select
        value={locale}
        onChange={(e) => {
          const next = e.target.value;
          if (isLocale(next)) setLocale(next);
        }}
        className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
        aria-label={t('common.languageSwitcherLabel')}
      >
        {LOCALES.map((l) => (
          <option key={l} value={l}>
            {LOCALE_LABELS[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
