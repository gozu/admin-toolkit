import { useTheme } from '../hooks/useTheme';
import { useUltraWideLayout } from '../hooks/useUltraWideLayout';
import { useScrolled } from '../hooks/useScrolled';
import type { DiagType } from '../types';
import dkulogo from '../assets/dkulogo.png';

interface HeaderProps {
  onBack?: () => void;
  onExport?: () => void;
  showBackButton?: boolean;
  showExportButton?: boolean;
  filename?: string;
  diagType?: DiagType;
  rightActions?: React.ReactNode;
}

function DiagTypeBadge({ type, size = 'md' }: { type: DiagType; size?: 'sm' | 'md' }) {
  const configs: Record<DiagType, { text: string; color: string; glowClass: string }> = {
    instance: {
      text: 'Instance',
      color: 'text-[var(--neon-green)]',
      glowClass: 'badge-glow-green'
    },
    job: {
      text: 'Job',
      color: 'text-[var(--neon-cyan)]',
      glowClass: 'badge-glow-cyan'
    },
    fm: {
      text: 'FM',
      color: 'text-[var(--neon-purple)]',
      glowClass: 'badge-glow-purple'
    },
    unknown: {
      text: 'Unknown',
      color: 'text-[var(--text-muted)]',
      glowClass: ''
    },
  };

  const config = configs[type];
  const sizeClasses = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm';

  return (
    <span className={`
      ${config.color} ${sizeClasses} ${config.glowClass}
      bg-[var(--bg-glass)] border border-[var(--border-glass)]
      rounded-full font-mono font-medium
    `}>
      {config.text}
    </span>
  );
}

export function Header({
  onBack,
  onExport,
  showBackButton = false,
  showExportButton = false,
  filename,
  diagType,
  rightActions,
}: HeaderProps) {
  const { theme, toggle: toggleTheme } = useTheme();
  const { layoutMode, setLayoutMode, isUltraWideRatio } = useUltraWideLayout();
  const isScrolled = useScrolled();
  const sideSlotClass = isUltraWideRatio ? 'w-[320px]' : 'w-32';

  return (
    <header
      className={`header-glass sticky top-0 z-50 ${isScrolled ? 'scrolled' : ''}`}
    >
      <div className="py-3 flex items-center justify-between">
        {/* Left: Back button */}
        <div className={`flex items-center gap-3 ${sideSlotClass}`}>
          {showBackButton && (
            <button
              onClick={onBack}
              className="p-2 -ml-2 rounded-lg text-[var(--text-secondary)]
                         hover:bg-[var(--bg-glass)] hover:text-[var(--neon-cyan)]
                         transition-all duration-150 ease-out"
              aria-label="Go back"
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 19l-7-7m0 0l7-7m-7 7h18"
                />
              </svg>
            </button>
          )}
        </div>

        {/* Center: Branding */}
        <div className="flex flex-col items-center gap-1 group cursor-default">
          {/* Neon Logo Text */}
          <div className="flex items-center gap-3">
            {/* Bird Logo */}
            <img
              src={dkulogo}
              alt="Dataiku"
              className="h-8 w-8"
            />
            <div className="relative">
              <span
                className="text-3xl font-bold text-[var(--text-primary)] tracking-tight"              >
                ADMIN
              </span>
              <span
                className="text-3xl font-bold text-[#2AB1AC] tracking-tight"              >
                TOOLKIT
              </span>
              {/* Glow effect on hover */}
              <div className="title-hover-glow" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="px-2 py-0.5 text-xs font-mono font-medium rounded
                         bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]
                         border border-[var(--neon-cyan)]/30 light-white-bg"
            >
              BETA
            </span>
          </div>
          {/* Context (when file loaded) */}
          {filename && (
            <div className="hidden md:flex items-center gap-2 text-xs text-[var(--text-muted)] mt-1">
              <span>Analyzing:</span>
              <span className="font-mono text-[var(--text-secondary)] max-w-[200px] truncate">
                {filename}
              </span>
              {diagType && diagType !== 'unknown' && (
                <DiagTypeBadge type={diagType} size="sm" />
              )}
            </div>
          )}
        </div>

        {/* Right: Actions */}
        <div className={`flex items-center gap-2 ${sideSlotClass} justify-end`}>
          {rightActions}
          {isUltraWideRatio && (
            <div className="hidden xl:flex items-center p-0.5 rounded-lg border border-[var(--border-glass)] bg-[var(--bg-glass)]">
              <button
                onClick={() => setLayoutMode('standard')}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${
                  layoutMode === 'standard'
                    ? 'bg-[var(--bg-elevated)] text-[var(--text-primary)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                Standard
              </button>
              <button
                onClick={() => setLayoutMode('ultrawide')}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${
                  layoutMode === 'ultrawide'
                    ? 'bg-[var(--bg-elevated)] text-[var(--text-primary)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                Ultra-wide
              </button>
            </div>
          )}

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[var(--text-secondary)]
                       hover:bg-[var(--bg-glass)] hover:text-[var(--neon-cyan)]
                       transition-all duration-150 text-sm"
            aria-label={`Switch to ${theme !== 'light' ? 'light' : 'dark'} mode`}
          >
            {theme !== 'light' ? (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
              </svg>
            )}
            <span className="hidden sm:inline">{theme !== 'light' ? 'Light Mode' : 'Dark Mode'}</span>
          </button>
          {showExportButton && (
            <button
              onClick={onExport}
              className="flex items-center gap-2 px-4 py-2
                         btn-primary rounded-lg text-sm"
            >
              <svg
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                />
              </svg>
              <span className="hidden sm:inline">Export</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
