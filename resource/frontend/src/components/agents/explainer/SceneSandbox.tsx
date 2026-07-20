import { ExplainerScene } from './ExplainerScene';
import { BoundaryBox, OffsetChip } from './primitives';
import { SANDBOX } from './content';
import type { SceneApi } from './useSceneSteps';

/** Scene 6 — the ADMINTOOLKIT sandbox. Host-bound work enters through one
 * labeled macro port; policy roots are whitelisted inside; remote hosts are
 * refused for the riskiest actions; deletes are backup-first, two beats. */

const POLICY_ROOTS = ['webappruns/', 'joblogs/', 'tmp/', 'exports/'];

function SandboxDiagram({ api }: { api: SceneApi }) {
  const { step } = api;
  return (
    <div className="agx-sandbox p-4 sm:p-6">
      <div className="agx-sb-row">
        {/* Outside: the webapp + the chip that must use the door. */}
        <div className="agx-sb-outside" data-zone data-hot={step === 0 || undefined}>
          <div className="agx-sb-webapp">webapp backend</div>
          <OffsetChip className="agx-sb-chip">log-cleanup</OffsetChip>
          <div className="agx-sec-note">never touches the filesystem itself</div>
        </div>

        <BoundaryBox label="ADMINTOOLKIT · macro sandbox" marching className="agx-sb-box">
          <span className="agx-sb-port" data-hot={step === 0 || undefined}>
            macro port
          </span>

          <div className="agx-sb-policy" data-zone data-hot={step === 1 || undefined}>
            <div className="agx-sb-zone-title">whitelisted roots — re-walked inside the macro</div>
            <div className="agx-sb-roots">
              {POLICY_ROOTS.map((r) => (
                <code key={r} className="agx-sb-root">
                  {r}
                </code>
              ))}
              <code className="agx-sb-rogue">/etc/shadow</code>
            </div>
            <div className="agx-sec-note">realpath containment · symlink refusal · age gates</div>
          </div>

          <div className="agx-sb-hosts" data-zone data-hot={step === 2 || undefined}>
            <div className="agx-sb-zone-title">riskiest actions are local-only</div>
            <div className="agx-sb-hostcards">
              <div className="agx-sb-host" data-kind="local">
                <span className="agx-sb-host-name">local DSS</span>
                <span className="agx-sb-host-verdict">runs</span>
              </div>
              <div className="agx-sb-host" data-kind="remote">
                <span className="agx-sb-host-name">remote host</span>
                <span className="agx-sb-host-verdict">refused</span>
              </div>
            </div>
            <div className="agx-sec-note">remote credentials never enter an agent kernel</div>
          </div>

          <div className="agx-sb-backup" data-zone data-hot={step === 3 || undefined}>
            <div className="agx-sb-zone-title">deletes are two-beat</div>
            <div className="agx-sb-beat">
              <span className="agx-sb-folder">backup → ADMINTOOLKIT</span>
              <span className="agx-sb-then">then</span>
              <span className="agx-sb-crate">SANDBOX_OLD</span>
            </div>
            <div className="agx-sec-note">no backup, no delete</div>
          </div>
        </BoundaryBox>
      </div>
    </div>
  );
}

export function SceneSandbox() {
  return (
    <ExplainerScene
      sceneClass="agx-s-sandbox"
      eyebrow={SANDBOX.eyebrow}
      title={SANDBOX.title}
      intro={SANDBOX.intro}
      steps={SANDBOX.steps}
    >
      {(api) => <SandboxDiagram api={api} />}
    </ExplainerScene>
  );
}
