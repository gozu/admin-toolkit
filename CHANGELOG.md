# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.4] - 2026-02-04

### Fixed
- Memory analysis layout refinements
- Removed unused status badge from Memory Analysis card

## [1.0.3] - 2026-02-04

### Fixed
- Fixed memory calculation formula (CGroup Limit - JEK × Max Activities)

## [1.0.2] - 2026-02-04

### Fixed
- Fixed CGroup memory limit parsing (accepts any key name, not just `memory.limit_in_bytes`)

## [1.0.1] - 2026-02-04

### Changed
- Memory analysis refinements and improvements

## [1.0.0] - 2026-02-04

### Added
- **Memory Analysis Card** (`MemoryAnalysisCard.tsx`)
  - CGroup Limit Check: Validates configured limit against recommended max based on VM size
  - JEK Allocation Check: Verifies JEK × Max Running Activities fits within cgroup limit
  - Color-coded status (green/yellow/red) for memory health
  - Shows "Available for Backend & Misc." calculation

### Changed
- Cluster improvements and debug screenshots

## [0.9.x] - 2026-02-04

### Added
- Memory analysis feature initial implementation
- Cluster table improvements

### Changed
- Matched K8s cluster card heights to minimize empty space
- Better error handling for malformed cluster data
- Improved node pool display

## [0.8.x] - 2026-02-04

### Added
- **Comparative Analysis Mode**
  - Upload and compare two diagnostic files side-by-side
  - Visual delta badges showing increases/decreases between environments
  - Comparison sections:
    - Health scores
    - System information
    - Configuration settings
    - Charts (filesystem, memory, connections)
    - Collections (users, projects, plugins, code envs, clusters)
  - Drag-and-drop upload for "Before" and "After" files

### New Files
- `ComparisonUpload.tsx` - Upload interface for two files
- `ComparisonResultsView.tsx` - Main comparison results display
- `ComparisonHealthSection.tsx` - Health score delta visualization
- `ComparisonSystemSection.tsx` - System metric changes
- `ComparisonSettingsSection.tsx` - Config/settings diffs
- `ComparisonCollectionsSection.tsx` - User/project/cluster/plugin changes
- `ComparisonChartsSection.tsx` - Chart comparisons
- `DeltaBadge.tsx` - Change indicators
- `compareData.ts` - Comparison utility functions

## [0.7.3] - 2026-02-03

### Changed
- K8s cluster card heights optimization
- Deployment documentation updates

## [0.7.0] - 2026-02-03

### Added
- **Landing Page with Navigation** (`LandingPage.tsx`)
  - New landing page with clear navigation options
  - Debug Mode: Single file analysis
  - Compare Mode: Two-file comparison
  - Improved onboarding experience

- **Install.ini Parser** (`InstallIniParser.ts`)
  - New parser for extracting install.ini configuration
  - Extracts node ID, install ID, and instance URL

### Changed
- **Cluster Table** (`ClustersTable.tsx`, `ClustersParser.ts`)
  - Better error handling for malformed cluster data
  - Improved node pool display

- **Connections Parser** (`ConnectionsParser.ts`)
  - Enhanced connection type detection
  - Better handling of connection details
  - Improved connection count parsing

- **Code Environments Table** (`CodeEnvsTable.tsx`, `CodeEnvsParser.ts`)
  - Expanded display with more details
  - Better categorization of code environments

- **Connections Chart** (`ConnectionsChart.tsx`)
  - Improved chart rendering
  - Better color coding for connection types

- **Health Score System** (`useHealthScore.ts`)
  - Added memory over-provisioning detection
  - CGroup limit validation against recommended max
  - JEK allocation check within cgroup
  - More accurate health scoring

- **Issue Detection** (`useIssueDetection.ts`)
  - Added memory over-provisioning detection
  - Improved issue categorization

### Fixed
- Fixed bird logo path for DSS plugin context

### UI/UX Improvements
- Dark theme refinements
- Improved table styling
- Better responsive layout
- Updated color variables

### Infrastructure
- Added plugin structure documentation to README
- Deployment process explanation
- DSS API endpoint examples
- Troubleshooting table for common errors
- Updated `bump_version.py` to sync frontend package.json version
- Added dummy Python backend for SSO settings (`backend.py`)
- Webapp configuration updates (`webapp.json`)
- Added automated screenshot testing (`screenshot-test.ts`)

---

## Pre-Plugin Development (Standalone React App)

The following versions were developed in the standalone React app before conversion to a DSS plugin.

## [0.6.0] - 2026-02-03

### Added
- **Directory Tree Visualization** (`useDirTreeLoader.ts`, `DirTreeSection.tsx`)
  - Async chunked parsing for large `datadir_listing.txt` files
  - Depth-limited tree building with byte-offset indexing
  - Fast drill-down navigation
  - Extraction progress bar for large files

### Changed
- Defer directory tree parsing until after initial UI render
- Stream `datadir_listing.txt` directly from zip (no blob in memory)

### Fixed
- Path normalization in `useDirTreeLoader` - trailing slashes causing parent lookup failures
- Drill-down in streaming mode - re-extract from zip when needed

### Performance
- Optimize dir tree parsing: removed expensive TextEncoder calls, simplified byte tracking
- Added entries/sec metric for parsing performance

## [0.5.0] - 2026-02-03

### Added
- **Light Mode Theme** with proper status colors
- Theme toggle support

### Changed
- Use solid backgrounds for cards/tables (removed backdrop-filter for better light mode support)
- Improved light mode contrast and visibility

### Fixed
- Python version badge logic

## [0.4.0] - 2026-02-03

### Added
- Bird logo branding
- Roboto Condensed font
- Version display in UI
- **Code splitting** for faster initial page load
- Auto-increment version on `make commit`

### Changed
- Add usage percentages to filesystem chart labels

## [0.3.0] - 2026-02-03

### Added
- **Visual redesign**: Enterprise professional styling
- Dark mode theme

### Changed
- Move Plugins/CodeEnvs into 3-column grid layout
- Improve hover contrast on Key Files buttons
- UI layout and label improvements

### Fixed
- Dark mode color issues
- Layout improvements

## [0.2.0] - 2026-02-02

### Added
- **Issue Detection System** with `AlertBanner` component
- **Resizable Modals** using react-rnd for drag/resize

### Changed
- Modal UX improvements: movable, no backdrop close, minimal margins

### Fixed
- Filesystem parsing issues
- Header styling
- Makefile: support multiline commits via file (`mf=`) or editor (`commit-i`)

## [0.1.0] - 2026-02-02

### Added
- **Initial React Migration** of Dataiku Diag Parser
  - Full rewrite from vanilla JS to React + TypeScript + Vite
  - Component-based architecture
  - Type-safe parsing system
  - Modern build tooling

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 0.1.0 | Feb 2, 2026 | Initial React migration |
| 0.2.0 | Feb 2, 2026 | Issue detection, resizable modals |
| 0.3.0 | Feb 3, 2026 | Visual redesign, dark mode |
| 0.4.0 | Feb 3, 2026 | Branding, code splitting |
| 0.5.0 | Feb 3, 2026 | Light mode theme |
| 0.6.0 | Feb 3, 2026 | Directory tree visualization |
| 0.7.0 | Feb 3, 2026 | **DSS Plugin conversion**, landing page |
| 0.7.3 | Feb 3, 2026 | K8s cluster card heights, deployment docs |
| 0.8.x | Feb 4, 2026 | Comparative analysis feature |
| 0.9.x | Feb 4, 2026 | Memory analysis, cluster improvements |
| 1.0.x | Feb 4, 2026 | Memory analysis refinements, layout fixes |
