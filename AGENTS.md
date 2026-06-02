# Admin Toolkit Agent Rules

## Product Soul

Build a polished, fast, dense admin experience. Interactions should feel smooth and immediate, with stable dimensions, no avoidable layout shifts, and no silent long waits. Favor progressive disclosure through expandable rows, popovers, panels, and compact controls over extra permanent chrome.

## UI Contracts

- Use the module registry for page ids, nav sections, command-palette metadata, experimental flags, and availability policies.
- Use the shared progress indicator for async module work.
- Progress colors: grey for queued/loading/unavailable, yellow for active/partial/waiting/stalled, white for ready/current/completed-neutral, red for failure.
- Keep rendering cheap: memoize derived rows, keep list/table dimensions stable, avoid expensive work during render, and prefer GPU-friendly transforms/opacity for animation.

## Data Contracts

- New trends tables must be added through the typed trends registry.
- Snapshot and compare participation must be validated by tests, not remembered manually.

## Verification

Run frontend typecheck/build after UI changes and backend tests after Python changes. Run `npm run check:contracts` when changing navigation, progress, or trends contracts.
