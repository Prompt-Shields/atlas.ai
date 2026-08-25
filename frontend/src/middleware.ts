import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { LOCALE_COOKIE } from '@/lib/i18n/config';
import { resolveLocale } from '@/lib/i18n/resolver';

const PUBLIC_PATHS = new Set(['/', '/login', '/invite/confirm']);

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Redirect unauthenticated users away from protected routes
  if (pathname.startsWith('/dashboard')) {
    const token = request.cookies.get('access_token')?.value;
    if (!token) {
      const loginUrl = new URL('/login', request.url);
      loginUrl.searchParams.set('redirect', pathname);
      return NextResponse.redirect(loginUrl);
    }
  }

  const response = NextResponse.next();

  // Resolve locale (cookie → Accept-Language) so a first-time visitor gets
  // their browser's language without a client-side flash of English.
  if (!request.cookies.get(LOCALE_COOKIE)?.value) {
    const locale = resolveLocale(null, request.headers.get('accept-language'));
    response.cookies.set(LOCALE_COOKIE, locale, { path: '/', maxAge: 365 * 24 * 60 * 60 });
  }

  // Security headers
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains; preload');
  // Extract origin from API URL for CSP (e.g. "http://localhost:8000/api/v1" → "http://localhost:8000")
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
  const apiOrigin = new URL(apiUrl).origin;

  response.headers.set(
    'Content-Security-Policy',
    [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self'",
      `connect-src 'self' ${apiOrigin} https://*.azure.com`,
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; '),
  );
  response.headers.set(
    'Permissions-Policy',
    'camera=(), microphone=(), geolocation=(), interest-cohort=()',
  );
  response.headers.set('X-Permitted-Cross-Domain-Policies', 'none');

  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
