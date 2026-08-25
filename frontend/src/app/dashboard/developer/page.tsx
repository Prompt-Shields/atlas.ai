'use client';

import Link from 'next/link';

export default function DeveloperLandingPage() {
  return (
    <div className="p-8 max-w-4xl">
      <h1 className="text-3xl font-semibold mb-2">Developer API</h1>
      <p className="text-gray-600 mb-8">
        Enable your engineers to send custom telemetry into Atlas AI. Define
        event types, issue scoped API keys, then review events as they flow in.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link
          href="/dashboard/developer/settings"
          className="block p-6 border border-gray-200 rounded-lg hover:border-indigo-500 hover:shadow transition"
        >
          <div className="text-xl mb-2">🔑 Settings</div>
          <div className="font-semibold mb-1">API Keys &amp; Event Types</div>
          <p className="text-sm text-gray-600">
            Issue and revoke API keys, assign scopes, and define the event
            types your developers can emit.
          </p>
        </Link>

        <Link
          href="/dashboard/developer/events"
          className="block p-6 border border-gray-200 rounded-lg hover:border-indigo-500 hover:shadow transition"
        >
          <div className="text-xl mb-2">📊 Events</div>
          <div className="font-semibold mb-1">Review Events</div>
          <p className="text-sm text-gray-600">
            See every event your applications have logged. Filter by type,
            severity, source, and correlation ID.
          </p>
        </Link>
      </div>

      <div className="mt-12">
        <h2 className="text-xl font-semibold mb-3">Quick start</h2>
        <ol className="list-decimal pl-6 space-y-2 text-sm text-gray-700">
          <li>
            Go to <Link href="/dashboard/developer/settings" className="text-indigo-600 underline">Settings</Link> and create at least one event type
            (e.g. <code>agent.task_started</code>).
          </li>
          <li>Issue an API key with the <code>events:write</code> scope.</li>
          <li>
            Send events from your app:
            <pre className="bg-gray-100 p-3 rounded mt-2 text-xs overflow-x-auto">{`curl -X POST $ATLAS_API/api/v1/developer/events \\
  -H "X-API-Key: $ATLAS_DEVELOPER_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "events": [
      {
        "event_type": "agent.task_started",
        "payload": {"task_id": "t-123", "agent": "researcher"},
        "session_id": "sess-abc"
      }
    ]
  }'`}</pre>
          </li>
          <li>
            Open <Link href="/dashboard/developer/events" className="text-indigo-600 underline">Events</Link> to review what was ingested.
          </li>
        </ol>
      </div>
    </div>
  );
}
