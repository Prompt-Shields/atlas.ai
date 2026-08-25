import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import './globals.css';
import { LocaleProvider } from '@/lib/i18n/provider';
import { LOCALE_COOKIE, isLocale } from '@/lib/i18n/config';

export const metadata: Metadata = {
  title: 'AI Governance Security Platform',
  description: 'AI-powered Governance, Security, and Secure AI Use platform',
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(cookieLocale) ? cookieLocale : 'en';

  return (
    <html lang={locale}>
      <body className="min-h-screen bg-gray-50 antialiased">
        <LocaleProvider initialLocale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
