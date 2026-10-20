import type { Metadata } from 'next';
import { IBM_Plex_Mono, Lora, Schibsted_Grotesk } from 'next/font/google';
import { cookies } from 'next/headers';
import './globals.css';
import { LocaleProvider } from '@/lib/i18n/provider';
import { LOCALE_COOKIE, isLocale } from '@/lib/i18n/config';

// The theme in globals.css names these families ('Schibsted Grotesk' and its
// generated fallback, and so on); loading them here is what defines them. The
// mono and serif loaders are attached by variable so their faces are emitted
// without setting the page's font.
const sans = Schibsted_Grotesk({ subsets: ['latin'], display: 'swap' });
const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  display: 'swap',
  variable: '--font-plex-mono',
});
const serif = Lora({ subsets: ['latin'], display: 'swap', variable: '--font-lora' });

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
    <html lang={locale} className={`${mono.variable} ${serif.variable}`}>
      <body className={`${sans.className} min-h-screen antialiased`}>
        <LocaleProvider initialLocale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
