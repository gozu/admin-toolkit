# Model replacement validation — 15 September 2026

The 64-check comparison suite now uses whole-task model replacement. The live
application's routing was not changed by this tooling work.

- 56 matching comparisons; median Cobuild/Mesh total time 0.98×, range 0.44–1.81×.
- One log-tail comparison differs only in live window metadata. Filtered log
  results match; the report retains `needs_review` rather than claiming full
  output equality.
- Six checks await new prerequisites: cluster start/stop, Kubernetes repair,
  completed-pod cleanup, image deletion, and fresh per-run Python approval.
  Previous cloud/image targets and Python approvals were not reused.
- `plugin-deploy` remains excluded at the user's request.

The models select tools and produce final answers. Each action retains ADTK
planning, execution, permissions, confirmation-token validation and audit.
Fixture checks independently verify the scoped state changes. One sample per
path, always Mesh first, does not establish a latency distribution. Final-answer
presence is checked; every sentence's correctness is not automatically graded.
The recorded model is DSS's configured main model, not proof of Cobuild's
internal routing or a particular underlying GPT deployment.

The targeted reruns fixed two harness problems: omitted nullable canonical-plan
fields and rejected follow-up reads after successful execution. The controller
still sends the original full signed target and executes at most once. Earlier
failed attempts remain in the report history.

A setup mistake attached the temporary test plugin to ADTK's environment;
DSS deleted that environment when removing the temporary plugin. ADTK's
environment was recreated from the installed plugin requirements, and its
backend subsequently restarted successfully. The runner now creates its own
environment and refuses to delete a plugin attached to a foreign environment.
A regression test covers this guard.

Final read-only checks verified 17 tracked fixture projects and 10 tracked
fixture plugins absent, plus the associated test environments, users and
variables absent. The manual cluster definition was also absent. Group cleanup
and permission restoration reported no failures. ADTK's environment is installed
and the backend reports live mode. Audit/history records intentionally remain.

Verification: 909 backend tests and 23 subtests passed. Chromium checked all
64 report rows and filtering. The maintenance audit has no errors; its existing
unreferenced `docs/screenshots/overview.png` warning remains. No frontend or
plugin runtime code changed, so no frontend build or deployment was required.
Private transcripts, credentials and confirmation tokens are excluded from the
published report.
