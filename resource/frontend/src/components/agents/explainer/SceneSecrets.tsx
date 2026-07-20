import { type CSSProperties } from 'react';
import { ExplainerScene } from './ExplainerScene';
import { OffsetChip } from './primitives';
import { SECRETS } from './content';
import type { SceneApi } from './useSceneSteps';

/** Scene 5 — one regex, both directions. Write lane: a secret-path settings
 * write bounces off the blocklist wall. Read lane: a scan sweeps the config
 * card and masks every secret-shaped value. Ticket: newValue + expectedCurrent
 * are inside the signature, so live drift voids it. */

const REGEX_TOKENS = [
  'password',
  'passphrase',
  'secret',
  'credential',
  'token',
  'keytab',
  'keyfile',
  '*key',
];

const CONFIG_LINES: readonly { key: string; value: string; secret?: boolean }[] = [
  { key: 'smtp.host', value: 'mail.corp.io' },
  { key: 'smtp.password', value: 'hunter2', secret: true },
  { key: 's3.accessKey', value: 'AKIA94X…', secret: true },
  { key: 's3.region', value: 'eu-west-1' },
  { key: 'ldap.bindPassword', value: 'Tr0ub4dor', secret: true },
];

function SecretsDiagram({ api }: { api: SceneApi }) {
  const { step } = api;
  return (
    <div className="agx-secrets p-4 sm:p-6">
      <div className="agx-sec-lanes">
        {/* Write lane */}
        <div className="agx-sec-write" data-zone data-hot={step === 0 || undefined}>
          <div className="agx-sec-lane-title">write path</div>
          <div className="agx-sec-bounce">
            <OffsetChip className="agx-sec-chip">settings-set · ldap.bindPassword</OffsetChip>
            <div className="agx-sec-wall">
              <span className="agx-sec-wall-title">blocklist</span>
              <div className="agx-sec-wall-tokens">
                {REGEX_TOKENS.map((t) => (
                  <code key={t}>{t}</code>
                ))}
              </div>
            </div>
          </div>
          <div className="agx-sec-note">refused at plan time — and re-checked at execute</div>
        </div>

        {/* Read lane */}
        <div className="agx-sec-read" data-zone data-hot={step === 1 || undefined}>
          <div className="agx-sec-lane-title">read path</div>
          <div className="agx-sec-card">
            <span className="agx-sec-scan" aria-hidden="true" />
            {CONFIG_LINES.map((l, i) => (
              <div key={l.key} className="agx-sec-line" style={{ '--agx-i': i } as CSSProperties}>
                <code className="agx-sec-key">{l.key}</code>
                {l.secret ? (
                  <span className="agx-sec-val">
                    <span className="agx-sec-plain">{l.value}</span>
                    <span className="agx-sec-mask">▮▮▮▮▮▮</span>
                  </span>
                ) : (
                  <code className="agx-sec-open">{l.value}</code>
                )}
              </div>
            ))}
          </div>
          <div className="agx-sec-note">the same pattern masks values before the model sees them</div>
        </div>
      </div>

      {/* The drift-proof ticket (a non-secret path — secret ones never get this far). */}
      <div className="agx-sec-ticketzone" data-zone data-hot={step === 2 || undefined}>
        <div className="agx-sec-ticket">
          <div className="agx-sec-ticket-title">signed ticket · settings-set</div>
          <div className="agx-sec-ticket-row">
            <span>path</span>
            <code>limits.maxRunningActivities</code>
          </div>
          <div className="agx-sec-ticket-row">
            <span>newValue</span>
            <code>20</code>
          </div>
          <div className="agx-sec-ticket-row" data-pinned>
            <span>expectedCurrent</span>
            <code>50</code>
          </div>
          <span className="agx-sec-void" aria-hidden="true">
            VOID
          </span>
        </div>
        <div className="agx-sec-drift">
          <span className="agx-sec-drift-chip">live value changed: 50 → 45</span>
          <span className="agx-sec-note">the signature no longer matches — replan required</span>
        </div>
      </div>
    </div>
  );
}

export function SceneSecrets() {
  return (
    <ExplainerScene
      sceneClass="agx-s-secrets"
      eyebrow={SECRETS.eyebrow}
      title={SECRETS.title}
      intro={SECRETS.intro}
      steps={SECRETS.steps}
    >
      {(api) => <SecretsDiagram api={api} />}
    </ExplainerScene>
  );
}
