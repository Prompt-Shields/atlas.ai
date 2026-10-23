'use client';

import { useEffect, useState } from 'react';
import { useLocale } from '@/lib/i18n/provider';
import {
  poolResetsAt,
  workClientApi,
  workClientView,
  type GuidedSteps,
  type WorkClientEntitlements,
  type WorkClientUpdate,
  type WorkClientView,
} from '@/lib/work-client';

// The free Work client's 90-day rule (promptly-guide #52), for the tenant admin:
// where the 90 days stand, what "connected" means, how to connect an endpoint, and
// what day 90 does today. The backend works the status out; this page only shows it
// and sends the two changes an admin can make (record day 0, declare an endpoint).

const TONES = {
  neutral: 'border-gray-200 bg-white',
  ok: 'border-green-200 bg-green-50',
  warn: 'border-amber-200 bg-amber-50',
  alert: 'border-red-200 bg-red-50',
} as const;

function tone(view: WorkClientView): keyof typeof TONES {
  switch (view.kind) {
    case 'notStarted':
      return 'neutral';
    case 'exempt':
      return 'ok';
    case 'counting':
      return view.warning ? 'warn' : 'neutral';
    case 'endpointRequired':
      return 'alert';
  }
}

function StatusCard({ view, formatDate }: { view: WorkClientView; formatDate: (iso: string) => string }) {
  const { t } = useLocale();
  let title: string;
  let lines: string[];
  switch (view.kind) {
    case 'notStarted':
      title = t('workClient.status.notStarted');
      lines = [t('workClient.status.notStartedBody')];
      break;
    case 'exempt':
      title = t(view.reason === 'paidModule' ? 'workClient.status.exemptPaid' : 'workClient.status.exemptOwn');
      lines = [
        t(view.reason === 'paidModule' ? 'workClient.status.exemptPaidBody' : 'workClient.status.exemptOwnBody', {
          day: view.day,
        }),
      ];
      break;
    case 'counting': {
      const date = formatDate(view.endsAt);
      const left =
        view.daysLeft === 1
          ? t('workClient.status.daysLeftOne', { date })
          : t('workClient.status.daysLeft', { days: view.daysLeft, date });
      title = view.warning
        ? t('workClient.status.warning')
        : t('workClient.status.counting', { day: view.day, total: view.total });
      lines = view.warning
        ? [t('workClient.status.counting', { day: view.day, total: view.total }) + ' · ' + left,
           t('workClient.status.warningBody', { date })]
        : [left];
      break;
    }
    case 'endpointRequired':
      title = t('workClient.status.endpointRequired');
      lines = [t('workClient.status.endpointRequiredBody', { date: formatDate(view.endsAt) })];
      break;
  }

  return (
    <section
      className={`mt-6 rounded-lg border p-6 ${TONES[tone(view)]}`}
      data-testid="work-client-status"
      data-status={view.kind}
    >
      <h2 className="text-sm font-medium text-gray-500">{t('workClient.status.heading')}</h2>
      <p className="mt-1 text-xl font-semibold text-gray-900">{title}</p>
      {view.kind === 'counting' && (
        <div
          className="mt-3 h-2 w-full overflow-hidden rounded-full bg-gray-200"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={view.total}
          aria-valuenow={Math.min(view.day, view.total)}
        >
          <div
            className={view.warning ? 'h-full bg-amber-500' : 'h-full bg-primary-600'}
            style={{ width: `${Math.min(100, (view.day / Math.max(view.total, 1)) * 100)}%` }}
          />
        </div>
      )}
      {lines.map((line) => (
        <p key={line} className="mt-2 text-sm text-gray-700">
          {line}
        </p>
      ))}
    </section>
  );
}

// The Work client's pool, closed months' hosted guided steps and the hard cap
// (promptly-guide #62). Step counts only: the price of a step above the pool is on the
// invoice, never on this page. The open month's count is shown only once the month
// ends (promptly-guide decision P9), so it cannot show one person's use.
function GuidedStepsSection({ locale }: { locale: string }) {
  const { t } = useLocale();
  const [steps, setSteps] = useState<GuidedSteps | null>(null);
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    workClientApi
      .guidedSteps()
      .then(setSteps)
      .catch((err: Error) => setLoadError(err.message));
  }, []);

  async function setHardCap(on: boolean) {
    if (!confirm(t(on ? 'workClient.steps.capOnConfirm' : 'workClient.steps.capOffConfirm'))) return;
    setSaving(true);
    setSaveError('');
    try {
      setSteps(await workClientApi.setHardCap(on));
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // The pool starts again at 00:00 UTC on the 1st, so the date is shown in UTC.
  const date = steps
    ? poolResetsAt(steps.period).toLocaleDateString(locale, {
        day: 'numeric',
        month: 'long',
        timeZone: 'UTC',
      })
    : '';

  // A "YYYY-MM" month by name, in UTC like the meter.
  const monthName = (period: string) => {
    const [year, month] = period.split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString(locale, {
      month: 'long',
      year: 'numeric',
      timeZone: 'UTC',
    });
  };

  return (
    <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6" data-testid="guided-steps">
      <h2 className="text-lg font-semibold text-gray-900">{t('workClient.steps.heading')}</h2>
      {loadError && (
        <p className="mt-2 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {t('workClient.steps.loadError', { error: loadError })}
        </p>
      )}
      {saveError && (
        <p className="mt-2 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {t('workClient.saveError', { error: saveError })}
        </p>
      )}
      {!steps && !loadError && <p className="mt-2 text-sm text-gray-500">{t('workClient.loading')}</p>}

      {steps && !steps.work_client && (
        <p className="mt-2 text-sm text-gray-700">{t('workClient.steps.noWorkClient')}</p>
      )}
      {steps && steps.work_client && steps.own_endpoint && (
        <p className="mt-2 text-sm text-gray-700">{t('workClient.steps.ownEndpoint')}</p>
      )}

      {steps && steps.work_client && !steps.own_endpoint && (
        <>
          <p className="mt-2 text-sm text-gray-700" data-testid="guided-steps-pool">
            {steps.pool === null
              ? t('workClient.steps.poolUnknown')
              : t('workClient.steps.pool', { pool: steps.pool })}
          </p>
          <p className="mt-2 text-sm text-gray-700" data-testid="guided-steps-open-month">
            {t('workClient.steps.openMonth', { month: monthName(steps.period), date })}
          </p>
          <h3 className="mt-4 text-sm font-semibold text-gray-900">{t('workClient.steps.closedHeading')}</h3>
          {steps.closed_months.length === 0 ? (
            <p className="mt-2 text-sm text-gray-500">{t('workClient.steps.noClosedMonths')}</p>
          ) : (
            <ul className="mt-2 divide-y divide-gray-100 text-sm" data-testid="guided-steps-closed">
              {steps.closed_months.map((m) => (
                <li key={m.period} className="flex justify-between py-1.5">
                  <span className="text-gray-700">{monthName(m.period)}</span>
                  <span className="font-medium text-gray-900">
                    {t('workClient.steps.closedUsed', { used: m.used })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {steps && steps.work_client && (
        <div className="mt-4 border-t border-gray-100 pt-4">
          <label className="flex items-center gap-2 text-sm font-medium text-gray-900">
            <input
              type="checkbox"
              checked={steps.hard_cap}
              disabled={saving}
              onChange={(e) => setHardCap(e.target.checked)}
              data-testid="guided-steps-hard-cap"
            />
            {t('workClient.steps.capLabel')}
          </label>
          <p className="mt-2 text-sm text-gray-700">
            {steps.hard_cap ? t('workClient.steps.capOn', { date }) : t('workClient.steps.capOff')}
          </p>
        </div>
      )}

      <p className="mt-4 text-xs text-gray-500">{t('workClient.steps.note')}</p>
    </section>
  );
}

export default function WorkClientPage() {
  const { t, locale } = useLocale();
  const [ent, setEnt] = useState<WorkClientEntitlements | null>(null);
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    workClientApi
      .entitlements()
      .then(setEnt)
      .catch((err: Error) => setLoadError(err.message));
  }, []);

  const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' });

  async function save(body: WorkClientUpdate, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    setSaving(true);
    setSaveError('');
    try {
      setEnt(await workClientApi.update(body));
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const view = ent ? workClientView(ent) : null;

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-gray-900">{t('workClient.title')}</h1>
      <p className="mt-1 text-sm text-gray-500">{t('workClient.subtitle')}</p>

      {loadError && (
        <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {t('workClient.loadError', { error: loadError })}
        </div>
      )}
      {saveError && (
        <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {t('workClient.saveError', { error: saveError })}
        </div>
      )}
      {!ent && !loadError && <p className="mt-6 text-sm text-gray-500">{t('workClient.loading')}</p>}

      {ent && view && (
        <>
          <StatusCard view={view} formatDate={formatDate} />

          {view.kind === 'notStarted' && (
            <div className="mt-4">
              <button
                type="button"
                disabled={saving}
                onClick={() => save({ started: true }, t('workClient.start.confirm'))}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {t('workClient.start.button')}
              </button>
              <p className="mt-2 text-xs text-gray-500">{t('workClient.start.note')}</p>
            </div>
          )}

          <GuidedStepsSection locale={locale} />

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('workClient.connected.heading')}</h2>
            <p className="mt-2 text-sm text-gray-700">{t('workClient.connected.body')}</p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <span className="text-sm font-medium text-gray-900" data-testid="own-endpoint-declared">
                {ent.own_endpoint_declared ? t('workClient.connected.declared') : t('workClient.connected.notDeclared')}
              </span>
              {ent.own_endpoint_declared ? (
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => save({ own_endpoint_declared: false }, t('workClient.connected.withdrawConfirm'))}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  {t('workClient.connected.withdraw')}
                </button>
              ) : (
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => save({ own_endpoint_declared: true })}
                  className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
                >
                  {t('workClient.connected.declare')}
                </button>
              )}
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('workClient.howTo.heading')}</h2>
            <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-gray-700">
              <li>{t('workClient.howTo.step1')}</li>
              <li>{t('workClient.howTo.step2')}</li>
              <li>{t('workClient.howTo.step3')}</li>
              <li>{t('workClient.howTo.step4')}</li>
            </ol>
            <p className="mt-3 text-xs text-gray-500">{t('workClient.howTo.note')}</p>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('workClient.dayNinety.heading')}</h2>
            <p className="mt-2 text-sm text-gray-700">{t('workClient.dayNinety.rule')}</p>
            <p className="mt-2 text-sm text-gray-700">{t('workClient.dayNinety.today')}</p>
          </section>
        </>
      )}
    </div>
  );
}
