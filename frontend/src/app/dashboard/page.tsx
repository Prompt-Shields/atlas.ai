'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useLocale } from '@/lib/i18n/provider';

export default function DashboardPage() {
  const { t } = useLocale();
  const [stats, setStats] = useState<any>(null);
  const [user, setUser] = useState<any>(null);

  useEffect(() => {
    api.getMe().then(setUser);
    Promise.all([
      api.listUserActivityBlobs(1).catch(() => ({ total: 0 })),
      api.listSourcesOfTruth(1).catch(() => ({ total: 0 })),
      api.listRisks(1).catch(() => ({ total: 0 })),
      api.listCorrelations(1).catch(() => ({ total: 0 })),
    ]).then(([activities, sources, risks, correlations]) => {
      setStats({
        userActivities: activities.total || 0,
        sourcesOfTruth: sources.total || 0,
        riskAnalyses: risks.total || 0,
        correlations: correlations.total || 0,
      });
    });
  }, []);

  const isSuperAdmin = user?.roles?.includes('SUPER_ADMIN');

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900">{t('dashboardHome.title')}</h1>
      <p className="mt-1 text-gray-600">
        {t('dashboardHome.welcome', {
          name: user?.full_name || t('dashboardHome.defaultUserName'),
        })}
      </p>

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <p className="text-sm font-medium text-gray-500">{t('dashboardHome.stats.userActivity')}</p>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {stats?.userActivities ?? '\u2014'}
          </p>
        </div>
        <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <p className="text-sm font-medium text-gray-500">{t('dashboardHome.stats.sourcesOfTruth')}</p>
          <p className="mt-2 text-3xl font-bold text-blue-600">
            {stats?.sourcesOfTruth ?? '\u2014'}
          </p>
        </div>
        <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <p className="text-sm font-medium text-gray-500">{t('dashboardHome.stats.riskAnalyses')}</p>
          <p className="mt-2 text-3xl font-bold text-amber-600">
            {stats?.riskAnalyses ?? '\u2014'}
          </p>
        </div>
        <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <p className="text-sm font-medium text-gray-500">{t('dashboardHome.stats.correlations')}</p>
          <p className="mt-2 text-3xl font-bold text-primary-600">
            {stats?.correlations ?? '\u2014'}
          </p>
        </div>
      </div>

      <div className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900">{t('dashboardHome.quickActions.title')}</h2>
        <div className="mt-4 flex flex-wrap gap-3">
          <a
            href="/dashboard/blobs"
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            {t('dashboardHome.quickActions.ingestedData')}
          </a>
          <a
            href="/dashboard/risks"
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700"
          >
            {t('dashboardHome.quickActions.reviewRisks')}
          </a>
          <a
            href="/dashboard/correlations"
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            {t('dashboardHome.quickActions.governanceCorrelations')}
          </a>
          {isSuperAdmin && (
            <a
              href="/dashboard/admin"
              className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
            >
              {t('dashboardHome.quickActions.adminTools')}
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
