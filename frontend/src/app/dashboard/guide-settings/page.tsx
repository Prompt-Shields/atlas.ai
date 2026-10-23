'use client';

import { useEffect, useState } from 'react';
import { useLocale } from '@/lib/i18n/provider';
import {
  CLOUD_PROVIDERS,
  CLOUD_PROVIDER_FIELDS,
  KNOWN_PACKS,
  LIST_FIELDS,
  PROCESSING_AREAS,
  SIGN_IN_FIELDS,
  emptyDocument,
  fromLines,
  guideConsoleApi,
  toLines,
  withField,
  withPin,
  withProvider,
  type CloudEndpoint,
  type CloudProvider,
  type ConsoleDocument,
  type ConsoleSection,
  type GuideConsole,
  type KnowledgeSource,
  type ListField,
} from '@/lib/guide-console';

// Promptly Guide's settings for the organisation (promptly-guide N1, option A): the
// same settings as Guide's file console, and the configuration profiles made from
// them to upload to Jamf or Intune. Atlas never changes a Mac itself. The settings
// only switch things off or name the organisation's own content; nothing here can
// turn on something that reads, which stays each person's own choice on their Mac.

const INPUT = 'mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm';
const BUTTON =
  'rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50';
const SECONDARY =
  'rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50';

type Translate = ReturnType<typeof useLocale>['t'];

function Switch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-gray-900">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/** The lists, packs and switches one section has: for every Mac, or for one group. */
function SectionFields({
  section,
  onChange,
  everyone,
  t,
}: {
  section: ConsoleSection;
  onChange: (next: ConsoleSection) => void;
  everyone: boolean;
  t: Translate;
}) {
  const set = <K extends keyof ConsoleSection>(field: K, value: ConsoleSection[K]) =>
    onChange(withField(section, field, value));
  const packsOff = section.skillPacksOff ?? [];

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {everyone && (
          <>
            <Switch
              label={t('guideConsole.fields.cloudModelDisabled')}
              checked={section.cloudModelDisabled === true}
              onChange={(on) => set('cloudModelDisabled', on)}
            />
            <Switch
              label={t('guideConsole.fields.requireEUProcessing')}
              checked={section.requireEUProcessing === true}
              onChange={(on) => set('requireEUProcessing', on)}
            />
          </>
        )}
        <Switch
          label={t('guideConsole.fields.memoryOff')}
          checked={section.memoryOff === true}
          onChange={(on) => set('memoryOff', on)}
        />
        {!everyone && (
          <Switch
            label={t('guideConsole.fields.workClientNotices')}
            checked={section.workClientNotices === true}
            onChange={(on) => set('workClientNotices', on)}
          />
        )}
      </div>

      <fieldset>
        <legend className="text-sm font-medium text-gray-900">{t('guideConsole.fields.skillPacksOff')}</legend>
        <div className="mt-1 flex flex-wrap gap-4">
          {KNOWN_PACKS.map((pack) => (
            <Switch
              key={pack}
              label={pack}
              checked={packsOff.includes(pack)}
              onChange={(on) =>
                set('skillPacksOff', on ? [...packsOff, pack] : packsOff.filter((p) => p !== pack))
              }
            />
          ))}
        </div>
      </fieldset>

      {LIST_FIELDS.map((field: ListField) => (
        <label key={field} className="block text-sm font-medium text-gray-900">
          {t(`guideConsole.fields.${field}`)}
          <textarea
            className={INPUT}
            rows={2}
            value={toLines(section[field])}
            onChange={(e) => set(field, fromLines(e.target.value))}
          />
          <span className="text-xs font-normal text-gray-500">{t(`guideConsole.hints.${field}`)}</span>
        </label>
      ))}

      <label className="block text-sm font-medium text-gray-900">
        {t('guideConsole.fields.adoptionTeam')}
        <input
          className={INPUT}
          value={section.adoptionTeam ?? ''}
          onChange={(e) => set('adoptionTeam', e.target.value)}
        />
        <span className="text-xs font-normal text-gray-500">{t('guideConsole.hints.adoptionTeam')}</span>
      </label>
    </div>
  );
}

// The dictionary keys for each provider's name: dictionary keys have no dashes.
const PROVIDER_LABEL: Record<CloudProvider, string> = {
  'azure-openai': 'azureOpenai',
  bedrock: 'bedrock',
  anthropic: 'anthropic',
  'openai-compatible': 'openaiCompatible',
};

/** A pin that can also be left to the person: unset, yes or no. */
function PinSelect({
  label,
  value,
  onChange,
  t,
}: {
  label: string;
  value: boolean | undefined;
  onChange: (value: boolean | undefined) => void;
  t: Translate;
}) {
  return (
    <label className="text-sm text-gray-900">
      {label}
      <select
        className={INPUT}
        value={value === undefined ? '' : value ? 'yes' : 'no'}
        onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value === 'yes')}
      >
        <option value="">{t('guideConsole.endpoint.pin.unset')}</option>
        <option value="yes">{t('guideConsole.endpoint.pin.yes')}</option>
        <option value="no">{t('guideConsole.endpoint.pin.no')}</option>
      </select>
    </label>
  );
}

/**
 * The organisation's own model endpoint (promptly-guide#40). It pins where the cloud
 * model may send and never turns it on, so there is no switch for that here.
 */
function EndpointFields({
  endpoint,
  onChange,
  t,
}: {
  endpoint: CloudEndpoint | undefined;
  onChange: (next: CloudEndpoint | undefined) => void;
  t: Translate;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm text-gray-900 sm:col-span-2">
        {t('guideConsole.endpoint.provider')}
        <select
          className={INPUT}
          value={endpoint?.provider ?? ''}
          onChange={(e) =>
            onChange(e.target.value === '' ? undefined : withProvider(endpoint, e.target.value as CloudProvider))
          }
        >
          <option value="">{t('guideConsole.endpoint.none')}</option>
          {CLOUD_PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {t(`guideConsole.endpoint.providers.${PROVIDER_LABEL[provider]}`)}
            </option>
          ))}
        </select>
      </label>
      {endpoint && (
        <>
          {CLOUD_PROVIDER_FIELDS[endpoint.provider].map((field) => (
            <label key={field} className="text-sm text-gray-900">
              {t(`guideConsole.endpoint.${field}`)}
              <input
                className={INPUT}
                value={endpoint[field] ?? ''}
                onChange={(e) =>
                  onChange(
                    withField(endpoint, field, field === 'dataRegion' ? e.target.value : e.target.value.trim()),
                  )
                }
              />
            </label>
          ))}
          <label className="text-sm text-gray-900">
            {t('guideConsole.endpoint.processingArea')}
            <select
              className={INPUT}
              value={endpoint.processingArea ?? ''}
              onChange={(e) => onChange(withField(endpoint, 'processingArea', e.target.value))}
            >
              <option value="">{t('guideConsole.endpoint.pin.unset')}</option>
              {PROCESSING_AREAS.map((area) => (
                <option key={area} value={area}>
                  {t(`guideConsole.endpoint.areas.${area}`)}
                </option>
              ))}
            </select>
          </label>
          <PinSelect
            label={t('guideConsole.endpoint.acceptsImages')}
            value={endpoint.acceptsImages}
            onChange={(v) => onChange(withPin(endpoint, 'acceptsImages', v))}
            t={t}
          />
          <PinSelect
            label={t('guideConsole.endpoint.zeroRetentionConfirmed')}
            value={endpoint.zeroRetentionConfirmed}
            onChange={(v) => onChange(withPin(endpoint, 'zeroRetentionConfirmed', v))}
            t={t}
          />
          <p className="text-xs text-gray-500 sm:col-span-2">{t('guideConsole.endpoint.hint')}</p>
        </>
      )}
    </div>
  );
}

function SourceRow({
  source,
  onChange,
  onRemove,
  t,
}: {
  source: KnowledgeSource;
  onChange: (next: KnowledgeSource) => void;
  onRemove: () => void;
  t: Translate;
}) {
  return (
    <div className="grid gap-2 rounded-lg border border-gray-100 p-3 sm:grid-cols-2">
      <label className="text-sm text-gray-900">
        {t('guideConsole.sources.id')}
        <input className={INPUT} value={source.id} onChange={(e) => onChange({ ...source, id: e.target.value.trim() })} />
      </label>
      <label className="text-sm text-gray-900">
        {t('guideConsole.sources.name')}
        <input
          className={INPUT}
          value={source.name ?? ''}
          onChange={(e) => onChange(withField(source, 'name', e.target.value))}
        />
      </label>
      <label className="text-sm text-gray-900 sm:col-span-2">
        {t('guideConsole.sources.folder')}
        <input className={INPUT} value={source.folder} onChange={(e) => onChange({ ...source, folder: e.target.value })} />
      </label>
      <label className="text-sm text-gray-900">
        {t('guideConsole.sources.groups')}
        <input
          className={INPUT}
          value={(source.groups ?? []).join(', ')}
          onChange={(e) =>
            onChange(
              withField(
                source,
                'groups',
                e.target.value
                  .split(',')
                  .map((g) => g.trim())
                  .filter(Boolean),
              ),
            )
          }
        />
      </label>
      <div className="flex items-end justify-between gap-2">
        <Switch
          label={t('guideConsole.sources.paused')}
          checked={source.enabled === false}
          onChange={(paused) => onChange(paused ? { ...source, enabled: false } : withField(source, 'enabled', undefined))}
        />
        <button type="button" className={SECONDARY} onClick={onRemove}>
          {t('guideConsole.remove')}
        </button>
      </div>
    </div>
  );
}

export default function GuideSettingsPage() {
  const { t, locale } = useLocale();
  const [state, setState] = useState<GuideConsole | null>(null);
  const [doc, setDoc] = useState<ConsoleDocument>(emptyDocument());
  const [note, setNote] = useState('');
  const [newGroup, setNewGroup] = useState('');
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    guideConsoleApi
      .get()
      .then((s) => {
        setState(s);
        if (s.console) setDoc(s.console);
      })
      .catch((err: Error) => setLoadError(err.message));
  }, []);

  const groups = doc.groups ?? {};
  const sources = doc.knowledgeSources ?? [];
  const signIn = doc.signIn ?? {};
  const edit = (next: ConsoleDocument) => {
    setDoc(next);
    setSaved(false);
  };

  async function save() {
    setBusy(true);
    setSaveError('');
    try {
      const s = await guideConsoleApi.save(doc, note);
      setState(s);
      if (s.console) setDoc(s.console);
      setNote('');
      setSaved(true);
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function download(action: () => Promise<void>) {
    setSaveError('');
    try {
      await action();
    } catch (err) {
      setSaveError((err as Error).message);
    }
  }

  function addGroup() {
    const name = newGroup.trim().toLowerCase();
    if (!name || groups[name]) return;
    edit({ ...doc, groups: { ...groups, [name]: {} } });
    setNewGroup('');
  }

  function removeGroup(name: string) {
    const rest = { ...groups };
    delete rest[name];
    edit({ ...doc, groups: rest });
  }

  const savedAt = state?.updated_at
    ? new Date(state.updated_at).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' })
    : '';
  const hasFiles = !!state && state.profiles.length > 0;

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-gray-900">{t('guideConsole.title')}</h1>
      <p className="mt-1 text-sm text-gray-500">{t('guideConsole.subtitle')}</p>

      {loadError && (
        <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {t('guideConsole.loadError', { error: loadError })}
        </div>
      )}
      {!state && !loadError && <p className="mt-6 text-sm text-gray-500">{t('guideConsole.loading')}</p>}

      {state && (
        <>
          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6" data-testid="guide-console-download">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.download.heading')}</h2>
            {state.revision === 0 && <p className="mt-2 text-sm text-gray-700">{t('guideConsole.download.nothingYet')}</p>}
            {state.revision > 0 && (
              <p className="mt-2 text-sm text-gray-700">
                {t('guideConsole.download.version', {
                  revision: state.revision,
                  date: savedAt,
                  by: state.updated_by ?? '',
                })}
              </p>
            )}
            {state.problem && (
              <p className="mt-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                {t('guideConsole.download.problem', { problem: state.problem })}
              </p>
            )}
            {hasFiles && (
              <>
                <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-gray-700">
                  <li>{t('guideConsole.download.step1')}</li>
                  <li>{t('guideConsole.download.step2')}</li>
                  <li>{t('guideConsole.download.step3')}</li>
                </ol>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button type="button" className={BUTTON} onClick={() => download(guideConsoleApi.downloadProfiles)}>
                    {t('guideConsole.download.allProfiles')}
                  </button>
                  <button type="button" className={SECONDARY} onClick={() => download(guideConsoleApi.downloadWindows)}>
                    {t('guideConsole.download.windows')}
                  </button>
                </div>
                <ul className="mt-4 space-y-1 text-sm">
                  {state.profiles.map((file) => (
                    <li key={file.name} className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-gray-700">
                        {file.group
                          ? t('guideConsole.download.forGroup', { group: file.group })
                          : t('guideConsole.download.forEveryone')}
                      </span>
                      <button
                        type="button"
                        className="text-primary-700 hover:underline"
                        onClick={() => download(() => guideConsoleApi.downloadProfile(file.name))}
                      >
                        {file.name}
                      </button>
                    </li>
                  ))}
                </ul>
                {state.windows_notes.length > 0 && (
                  <div className="mt-4 text-xs text-gray-500">
                    <p>{t('guideConsole.download.windowsNotes')}</p>
                    <ul className="mt-1 list-disc pl-5">
                      {state.windows_notes.map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
            <p className="mt-4 text-xs text-gray-500">{t('guideConsole.download.note')}</p>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.organisation.heading')}</h2>
            <label className="mt-3 block text-sm font-medium text-gray-900">
              {t('guideConsole.organisation.name')}
              <input className={INPUT} value={doc.organisation} onChange={(e) => edit({ ...doc, organisation: e.target.value })} />
            </label>
            <label className="mt-3 block text-sm font-medium text-gray-900">
              {t('guideConsole.organisation.identifier')}
              <input
                className={INPUT}
                value={doc.identifier}
                placeholder="com.example.promptly"
                onChange={(e) => edit({ ...doc, identifier: e.target.value.trim() })}
              />
              <span className="text-xs font-normal text-gray-500">{t('guideConsole.organisation.identifierHint')}</span>
            </label>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.everyone.heading')}</h2>
            <p className="mb-4 mt-1 text-sm text-gray-500">{t('guideConsole.everyone.body')}</p>
            <SectionFields section={doc.everyone ?? {}} everyone t={t} onChange={(s) => edit({ ...doc, everyone: s })} />
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.groups.heading')}</h2>
            <p className="mt-1 text-sm text-gray-500">{t('guideConsole.groups.body')}</p>
            {Object.entries(groups).map(([name, section]) => (
              <div key={name} className="mt-4 rounded-lg border border-gray-100 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="font-medium text-gray-900">{name}</h3>
                  <button type="button" className={SECONDARY} onClick={() => removeGroup(name)}>
                    {t('guideConsole.remove')}
                  </button>
                </div>
                <SectionFields
                  section={section}
                  everyone={false}
                  t={t}
                  onChange={(s) => edit({ ...doc, groups: { ...groups, [name]: s } })}
                />
              </div>
            ))}
            <div className="mt-4 flex flex-wrap items-end gap-2">
              <label className="text-sm text-gray-900">
                {t('guideConsole.groups.name')}
                <input className={INPUT} value={newGroup} onChange={(e) => setNewGroup(e.target.value)} />
              </label>
              <button type="button" className={SECONDARY} onClick={addGroup}>
                {t('guideConsole.groups.add')}
              </button>
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.sources.heading')}</h2>
            <p className="mb-3 mt-1 text-sm text-gray-500">{t('guideConsole.sources.body')}</p>
            <div className="space-y-3">
              {sources.map((source, i) => (
                <SourceRow
                  key={i}
                  source={source}
                  t={t}
                  onChange={(s) => edit({ ...doc, knowledgeSources: sources.map((x, j) => (j === i ? s : x)) })}
                  onRemove={() => edit({ ...doc, knowledgeSources: sources.filter((_, j) => j !== i) })}
                />
              ))}
            </div>
            <button
              type="button"
              className={`${SECONDARY} mt-3`}
              onClick={() => edit({ ...doc, knowledgeSources: [...sources, { id: '', folder: '' }] })}
            >
              {t('guideConsole.sources.add')}
            </button>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.signIn.heading')}</h2>
            <p className="mb-3 mt-1 text-sm text-gray-500">{t('guideConsole.signIn.body')}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {SIGN_IN_FIELDS.map((field) => (
                <label key={field} className="text-sm text-gray-900">
                  {t(`guideConsole.signIn.${field}`)}
                  <input
                    className={INPUT}
                    value={signIn[field] ?? ''}
                    onChange={(e) => edit({ ...doc, signIn: withField(signIn, field, e.target.value.trim()) })}
                  />
                </label>
              ))}
              <label className="text-sm text-gray-900">
                {t('guideConsole.signIn.groupsValidForHours')}
                <input
                  className={INPUT}
                  type="number"
                  min={1}
                  max={168}
                  value={signIn.groupsValidForHours ?? ''}
                  onChange={(e) =>
                    edit({
                      ...doc,
                      signIn: withField(
                        signIn,
                        'groupsValidForHours',
                        e.target.value === '' ? undefined : Number(e.target.value),
                      ),
                    })
                  }
                />
              </label>
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.atlas.heading')}</h2>
            <label className="mt-3 block text-sm font-medium text-gray-900">
              {t('guideConsole.atlas.url')}
              <input
                className={INPUT}
                value={doc.atlas?.url ?? ''}
                placeholder="https://atlas.example.com"
                onChange={(e) => edit({ ...doc, atlas: { ...doc.atlas, url: e.target.value.trim() } })}
              />
              <span className="text-xs font-normal text-gray-500">{t('guideConsole.atlas.hint')}</span>
            </label>
            <div className="mt-3">
              <Switch
                label={t('guideConsole.atlas.scimGroups')}
                checked={doc.atlas?.scimGroups === true}
                onChange={(on) => edit({ ...doc, atlas: withField(doc.atlas ?? { url: '' }, 'scimGroups', on) })}
              />
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6" data-testid="guide-console-endpoint">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.endpoint.heading')}</h2>
            <p className="mb-3 mt-1 text-sm text-gray-500">{t('guideConsole.endpoint.body')}</p>
            <EndpointFields
              endpoint={doc.cloudEndpoint}
              t={t}
              onChange={(next) => {
                const rest = { ...doc };
                delete rest.cloudEndpoint;
                edit(next ? { ...rest, cloudEndpoint: next } : rest);
              }}
            />
          </section>

          <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-gray-900">{t('guideConsole.cannot.heading')}</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-gray-700">
              <li>{t('guideConsole.cannot.reads')}</li>
              <li>{t('guideConsole.cannot.cloud')}</li>
              <li>{t('guideConsole.cannot.exclusions')}</li>
              <li>{t('guideConsole.cannot.mac')}</li>
            </ul>
          </section>

          <div className="sticky bottom-0 mt-6 rounded-lg border border-gray-200 bg-white p-4">
            {saveError && (
              <p className="mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-700" data-testid="guide-console-error">
                {t('guideConsole.saveError', { error: saveError })}
              </p>
            )}
            {saved && <p className="mb-3 text-sm text-green-700">{t('guideConsole.saved')}</p>}
            <label className="block text-sm text-gray-900">
              {t('guideConsole.note')}
              <input className={INPUT} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
            </label>
            <button type="button" className={`${BUTTON} mt-3`} disabled={busy} onClick={save}>
              {t('guideConsole.save')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
