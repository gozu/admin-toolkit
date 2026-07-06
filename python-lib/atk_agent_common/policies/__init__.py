"""Policy engines — the LOWEST enforcement layer of the remediation suite.

Pure modules (no dataiku imports, unit-testable) imported by BOTH the actuator
planners (fast refusal / good UX) and the macros / executors (authoritative
enforcement). An LLM can never talk its way past these: the macro re-applies
the policy to whatever it is actually about to touch, regardless of what the
plan said.

Modules:
  log_files      — rotated-log-only deletion policy (whitelisted DIP_HOME roots)
  docker_cmds    — fixed-argv docker command builder (no shell, no --all)
  kubectl_policy — kubectl verb/kind/namespace/token whitelist
  settings_paths — DSS general-settings path blacklist + path get/set helpers
"""
