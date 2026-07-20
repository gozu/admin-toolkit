import { type CSSProperties } from 'react';
import { ExplainerScene } from './ExplainerScene';
import { LEDGER } from './content';
import { RollingNumber } from '../../common/RollingNumber';
import type { SceneApi } from './useSceneSteps';

/** Scene 8 — the receipt. An insert-only audit ledger (rows are demo data;
 * the mechanism — one row per execute, failures included, written in a
 * finally — is the real one), the settings before/after record, and traces
 * redacted before storage. */

const ROWS = [
  { id: '#243', ts: '02:11', action: 'log-cleanup', host: 'dss-auto', status: 'ok' },
  { id: '#244', ts: '02:12', action: 'db-vacuum', host: 'dss-auto', status: 'ok' },
  { id: '#245', ts: '02:14', action: 'project-export', host: 'dss-design', status: 'ok' },
  { id: '#246', ts: '02:15', action: 'connection-test', host: 'dss-deploy', status: 'error' },
  { id: '#247', ts: '09:41', action: 'settings-set', host: 'local', status: 'ok' },
] as const;

function LedgerDiagram({ api }: { api: SceneApi }) {
  const { step, revealed } = api;
  return (
    <div className="agx-ledger p-4 sm:p-6">
      <div className="agx-lg-grid">
        {/* Step 0 — insert-only ledger. */}
        <div className="agx-au-zone" data-zone data-hot={step === 0 || undefined}>
          <div className="agx-lg-head">
            <span className="agx-sb-zone-title">agents.agent_actions · insert-only</span>
            <span className="agx-lg-count">
              <RollingNumber
                value={revealed ? 247 : 0}
                className="text-base font-bold text-[var(--text-primary)]"
              />
              <span>rows, ever</span>
            </span>
          </div>
          <div className="agx-lg-table">
            {ROWS.map((r, i) => (
              <div
                key={r.id}
                className="agx-lg-row"
                data-status={r.status}
                style={{ '--agx-i': i } as CSSProperties}
              >
                <span className="agx-lg-id">{r.id}</span>
                <span className="agx-lg-ts">{r.ts}</span>
                <span className="agx-lg-action">{r.action}</span>
                <span className="agx-lg-host">{r.host}</span>
                <span className="agx-lg-status">{r.status}</span>
              </div>
            ))}
            <div className="agx-lg-cursor" aria-hidden="true">
              ▊ INSERT — success or failure, written in a finally
            </div>
          </div>
        </div>

        <div className="agx-lg-side">
          {/* Step 1 — before/after. */}
          <div className="agx-au-zone" data-zone data-hot={step === 1 || undefined}>
            <div className="agx-sb-zone-title">agents.settings_changes</div>
            <div className="agx-lg-diff">
              <code className="agx-lg-diff-path">limits.maxRunningActivities</code>
              <span className="agx-lg-diff-vals">
                <span className="agx-lg-before">50</span>
                <span className="agx-lg-arrow">→</span>
                <span className="agx-lg-after">20</span>
              </span>
              <span className="agx-sec-note">any change can be read back — and reversed</span>
            </div>
          </div>

          {/* Step 2 — redacted traces + persistence default. */}
          <div className="agx-au-zone" data-zone data-hot={step === 2 || undefined}>
            <div className="agx-sb-zone-title">tool-call trace</div>
            <div className="agx-lg-trace">
              <div>
                <span className="agx-lg-trace-key">tool</span> execute_admin_action
              </div>
              <div>
                <span className="agx-lg-trace-key">args</span> {'{'} action: "log-cleanup",
                confirm_token: <span className="agx-lg-redacted">&lt;redacted&gt;</span> {'}'}
              </div>
              <div>
                <span className="agx-lg-trace-key">status</span> ok · 1.9s
              </div>
            </div>
            <div className="agx-lg-persistence">
              chat persistence: <strong>OFF by default</strong> — conversations stay in your browser
              until you opt in
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SceneLedger() {
  return (
    <ExplainerScene
      sceneClass="agx-s-ledger"
      eyebrow={LEDGER.eyebrow}
      title={LEDGER.title}
      intro={LEDGER.intro}
      steps={LEDGER.steps}
    >
      {(api) => <LedgerDiagram api={api} />}
    </ExplainerScene>
  );
}
