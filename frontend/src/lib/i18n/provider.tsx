'use client';

/**
 * Locale provider (M7, issue #251).
 *
 * `app/layout.tsx` (a server component) resolves the initial locale from
 * the `NEXT_LOCALE` cookie (set by `middleware.ts` from Accept-Language on
 * first visit) and passes it in as `initialLocale`, so there's no
 * client-side flash of the wrong language. `setLocale` persists the choice
 * back to the cookie for subsequent requests.
 */

import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { DEFAULT_LOCALE, LOCALE_COOKIE, type Locale } from './config';
import { dictionaries, type Dictionary } from './dictionaries';

type TranslateParams = Record<string, string | number>;

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: TranslateParams) => string;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

function getByPath(dict: Dictionary, path: string): unknown {
  return path.split('.').reduce<unknown>((node, segment) => {
    if (node && typeof node === 'object' && segment in node) {
      return (node as Record<string, unknown>)[segment];
    }
    return undefined;
  }, dict);
}

function interpolate(template: string, params?: TranslateParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    name in params ? String(params[name]) : match,
  );
}

function setCookie(name: string, value: string): void {
  const expires = new Date(Date.now() + 365 * 864e5).toUTCString();
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${name}=${value}; expires=${expires}; path=/; SameSite=Lax${secure}`;
}

export function LocaleProvider({
  initialLocale,
  children,
}: {
  initialLocale: Locale;
  children: React.ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    setCookie(LOCALE_COOKIE, next);
  }, []);

  const t = useCallback(
    (key: string, params?: TranslateParams) => {
      const value =
        getByPath(dictionaries[locale], key) ?? getByPath(dictionaries[DEFAULT_LOCALE], key);
      if (typeof value !== 'string') return key;
      return interpolate(value, params);
    },
    [locale],
  );

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error('useLocale must be used within a LocaleProvider');
  return ctx;
}
