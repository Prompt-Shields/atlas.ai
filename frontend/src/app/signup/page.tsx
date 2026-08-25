'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';

// SSO failures redirected back from the callback (?sso_error=...). On the
// signup page, `not_provisioned` means self-serve sign-up is disabled on this
// deployment (SSO_SELF_SERVE_SIGNUP=false) rather than "ask an admin".
const SSO_ERRORS: Record<string, string> = {
  not_provisioned:
    'Self-serve sign-up is not enabled for this deployment. Ask your administrator for an invite.',
  invalid: 'Microsoft sign-in could not be verified. Please try again.',
  exchange_failed: 'Your sign-in session expired. Please try again.',
};

export default function SignupPage() {
  const [error, setError] = useState('');

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('sso_error');
    if (code) setError(SSO_ERRORS[code] ?? 'Microsoft sign-in failed. Please try again.');
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-gray-900">AI-GRC Platform</h1>
          <p className="mt-2 text-gray-600">Governance, Risk & Compliance</p>
        </div>

        <div className="rounded-xl bg-white p-8 shadow-lg">
          <h2 className="mb-1 text-xl font-semibold text-gray-900">Start your free trial</h2>
          <p className="mb-6 text-sm text-gray-600">
            Create a new Atlas workspace for your organisation — 14 days free, no card required.
          </p>

          {error && (
            <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>
          )}

          <a
            href={api.ssoMicrosoftLoginUrl}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-semibold text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2"
          >
            <svg aria-hidden="true" viewBox="0 0 21 21" className="h-4 w-4">
              <rect x="1" y="1" width="9" height="9" fill="#f25022" />
              <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
              <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
              <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
            </svg>
            Continue with Microsoft
          </a>

          <p className="mt-4 text-xs text-gray-500">
            The first person from your Microsoft (Entra ID) directory becomes the workspace admin.
            Colleagues who sign in afterwards join the same workspace automatically.
          </p>

          <div className="my-6 flex items-center gap-3">
            <div className="h-px flex-1 bg-gray-200" />
            <span className="text-xs font-medium uppercase tracking-wide text-gray-400">or</span>
            <div className="h-px flex-1 bg-gray-200" />
          </div>

          <p className="text-center text-sm text-gray-600">
            Already have an account?{' '}
            <Link href="/login" className="font-semibold text-primary-600 hover:text-primary-700">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
