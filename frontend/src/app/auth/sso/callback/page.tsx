'use client';

// Landing page for the Entra SSO handoff (PRO-55). The backend callback
// redirects here with a one-time ?code=...; we exchange it for a session and
// forward to the dashboard. On any failure we bounce back to /login with an
// sso_error so the user sees a friendly message.

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';

export default function SsoCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code');
    if (!code) {
      router.replace('/login?sso_error=invalid');
      return;
    }
    api
      .exchangeSsoCode(code)
      .then(() => router.replace('/dashboard'))
      .catch(() => router.replace('/login?sso_error=exchange_failed'));
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="flex flex-col items-center gap-3 text-gray-600">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-primary-600" />
        <p className="text-sm">Signing you in…</p>
      </div>
    </div>
  );
}
