'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { UserResponse } from '@/lib/types';
import clsx from 'clsx';
import { ReminderBanners } from '@/components/dashboard/ReminderBanners';
import { LanguageSwitcher } from '@/components/dashboard/LanguageSwitcher';
import { useLocale } from '@/lib/i18n/provider';

const navigation = [
  { key: 'dashboard', href: '/dashboard', icon: '📊' },
  { key: 'overview', href: '/dashboard/overview', icon: '🔭' },
  { key: 'tenants', href: '/dashboard/tenants', icon: '🏢', roles: ['SUPER_ADMIN'] },
  { key: 'users', href: '/dashboard/users', icon: '👥', roles: ['SUPER_ADMIN', 'TENANT_ADMIN', 'ORG_ADMIN'] },
  { key: 'teams', href: '/dashboard/teams', icon: '👪', roles: ['SUPER_ADMIN', 'TENANT_ADMIN', 'ORG_ADMIN'] },
  { key: 'blobs', href: '/dashboard/blobs', icon: '📄' },
  { key: 'risks', href: '/dashboard/risks', icon: '⚠️' },
  { key: 'correlations', href: '/dashboard/correlations', icon: '🔗' },
  { key: 'activityLog', href: '/dashboard/feed', icon: '📜' },
  { key: 'coaching', href: '/dashboard/coaching', icon: '🎯' },
  { key: 'handbook', href: '/dashboard/handbook', icon: '📖' },
  { key: 'policies', href: '/dashboard/policies', icon: '🛡️' },
  { key: 'registry', href: '/dashboard/registry', icon: '🗂️' },
  { key: 'aiInventory', href: '/dashboard/ai-inventory', icon: '🧭' },
  { key: 'saasVendorAi', href: '/dashboard/saas-vendor-ai', icon: '🛰️' },
  { key: 'agentDiscovery', href: '/dashboard/agent-discovery', icon: '🤖' },
  { key: 'agentControl', href: '/dashboard/agent-control', icon: '🎛️' },
  { key: 'adoption', href: '/dashboard/adoption', icon: '📈' },
  { key: 'reattestation', href: '/dashboard/reattestation', icon: '🔁' },
  { key: 'map', href: '/dashboard/map', icon: '🗺️' },
  { key: 'discover', href: '/dashboard/discover', icon: '🔭' },
  { key: 'register', href: '/dashboard/register', icon: '➕' },
  { key: 'owners', href: '/dashboard/owners', icon: '🧑‍💼' },
  { key: 'modelRisk', href: '/dashboard/model-risk', icon: '📐' },
  { key: 'auditReport', href: '/dashboard/audit-report', icon: '📑' },
  { key: 'comply', href: '/dashboard/comply', icon: '✅' },
  { key: 'policyEnforcement', href: '/dashboard/policy-enforcement', icon: '🚨' },
  { key: 'piiShield', href: '/dashboard/pii-shield', icon: '🛡️' },
  { key: 'deployment', href: '/dashboard/deployment', icon: '🚀' },
  { key: 'endpoints', href: '/dashboard/endpoints', icon: '💻' },
  { key: 'integrations', href: '/dashboard/integrations', icon: '🔌' },
  { key: 'aiSpend', href: '/dashboard/ai-spend', icon: '💵' },
  { key: 'developer', href: '/dashboard/developer', icon: '🛠️' },
  { key: 'promptActivity', href: '/dashboard/prompt-activity', icon: '💬' },
  { key: 'admin', href: '/dashboard/admin', icon: '🔧', roles: ['SUPER_ADMIN'] },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useLocale();
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!api.isAuthenticated) {
      router.push('/login');
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const me = await api.getMe();
        if (cancelled) return;
        setUser(me);

        // First-time tenants get routed through /onboarding before
        // they see the dashboard. Best-effort: if the endpoint fails
        // (e.g. older backend without the route), assume complete
        // so we don't block legitimate users.
        try {
          const status = await api.getOnboardingStatus();
          if (!cancelled && !status.is_complete) {
            router.replace('/onboarding');
            return;
          }
        } catch {
          // Soft fail — proceed to dashboard.
        }
      } catch {
        if (cancelled) return;
        api.logout();
        router.push('/login');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [router]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="animate-pulse text-gray-500">Loading...</div>
      </div>
    );
  }

  const userRoles = user?.roles || [];
  const filteredNav = navigation.filter((item) => {
    if (!item.roles) return true;
    return item.roles.some((r) => userRoles.includes(r));
  });

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="w-64 border-r border-gray-200 bg-white">
        <div className="flex h-16 items-center border-b border-gray-200 px-6">
          <h1 className="text-lg font-bold text-primary-700">AI-GRC</h1>
        </div>
        <nav className="mt-4 space-y-1 px-3">
          {filteredNav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                pathname === item.href
                  ? 'bg-primary-50 text-primary-700'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
              )}
            >
              <span>{item.icon}</span>
              {t(`nav.${item.key}`)}
            </Link>
          ))}
        </nav>

        <div className="absolute bottom-0 w-64 border-t border-gray-200 p-4">
          <div className="mb-3">
            <LanguageSwitcher />
          </div>
          <div className="text-sm text-gray-600">
            <p className="font-medium text-gray-900">{user?.full_name}</p>
            <p className="text-xs text-gray-500">{user?.email}</p>
            <p className="mt-1 text-xs text-primary-600">{userRoles.join(', ')}</p>
          </div>
          <button
            onClick={() => { api.logout(); router.push('/login'); }}
            className="mt-3 w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            {t('common.signOut')}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl p-6">
          <ReminderBanners />
          {children}
        </div>
      </main>
    </div>
  );
}
