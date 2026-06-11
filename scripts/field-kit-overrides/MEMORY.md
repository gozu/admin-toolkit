# Memory Index

> Field-kit note: this is a sanitized subset of the home memory. Home-instance
> workflows (auto-deploy after build, secure-push gate, home test-instance
> targets, fork PR flow, public-no-auth assumption) are deliberately omitted —
> in a customer environment, never deploy, push, or mutate DSS state without
> explicit permission.

- [feedback_no_gold_plating.md](feedback_no_gold_plating.md) — When fixing reported issues, fix exactly those issues; don't invent adjacent capabilities
- [feedback_no_unrequested_fallbacks.md](feedback_no_unrequested_fallbacks.md) — Don't add unrequested fallbacks/alternatives; "pending veto" plan items need confirmation, not auto-apply
- [feedback_prefer_dss_settings_over_prompting.md](feedback_prefer_dss_settings_over_prompting.md) — Read values from DSS config (get_general_settings etc.) instead of prompting the user to type them
- [feedback_verify_api_shape.md](feedback_verify_api_shape.md) — Verify actual API response shape with a test script before writing code that consumes it
- [feedback_verify_in_dss_after_deploy.md](feedback_verify_in_dss_after_deploy.md) — Real verification happens in the live DSS instance; headless/preview smoke tests can't reach a backend
- [feedback_ship_full_import_closure.md](feedback_ship_full_import_closure.md) — When bundling a self-contained artifact, ship the COMPLETE transitive first-party import closure; no leftover external siblings
- [project_build_speed_and_rolldown_blocker.md](project_build_speed_and_rolldown_blocker.md) — Frontend build REQUIRES Node ≥20.19 (rolldown native binding); clean-reinstall node_modules when swapping bundlers
- [project_perf_phase3_tuning.md](project_perf_phase3_tuning.md) — Perf model: DSS API throughput flat 8→32 workers (contention, not starvation); staged phase-3 loading; Settings benchmark auto-tuner; prewarm rides the studioExternalUrl self-proxy
- [project_algorithm_review_notebooks.md](project_algorithm_review_notebooks.md) — "Algorithm review notebooks" Settings feature: ships adk_notebook libs + 16 scan notebooks into the webapp's OWN project, on the plugin's CURRENT managed code env (newest `_N` family member)
- [project_tracking_db_dropped_but_email_cleaner_live.md](project_tracking_db_dropped_but_email_cleaner_live.md) — Tracking DB dropped, but email/cleaner/mail-channel plumbing is LIVE (FS Migration + Projects Cleaner) — don't delete it
- [reference_live_api_access.md](reference_live_api_access.md) — Read-only debug endpoints on the deployed webapp backend (logs, perf, support bundle) and how to find the backend base URL
- [reference_dss_container_settings_registry.md](reference_dss_container_settings_registry.md) — Where registry URL + provider hint live inside containerSettings (per-execConfig), for image-cleaner provider detection
- [reference_dss_library_notebook_api.md](reference_dss_library_notebook_api.md) — Verified dataikuapi recipe: write project-library files (utf-8 bytes!), create notebooks (delete+create for idempotency), kernel resolution
- [reference_red_gated_tool_pages_playwright.md](reference_red_gated_tool_pages_playwright.md) — Tool (red) pages hidden without unlock cookie; GETs are open, only mutations are @advanced-gated; Playwright-verify by fulfilling /api/auth/red/status
