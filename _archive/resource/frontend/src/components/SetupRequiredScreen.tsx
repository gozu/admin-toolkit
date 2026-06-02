export function SetupRequiredScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)] p-6">
      <div className="glass-card max-w-lg w-full p-8 text-center space-y-6">
        {/* Warning icon */}
        <div className="mx-auto w-16 h-16 rounded-full bg-amber-500/15 flex items-center justify-center">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="text-amber-400">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
        </div>

        <h1 className="text-2xl font-bold text-[var(--text-primary)]">
          SQL Connection Required
        </h1>

        <p className="text-[var(--text-secondary)] text-sm leading-relaxed">
          Admin Toolkit requires a SQL connection for persistent tracking storage.
          Your instance has compatible SQL connections available, but none is configured for this plugin.
        </p>

        <div className="text-left bg-[var(--bg-elevated)] rounded-lg p-4 space-y-3 text-sm">
          <p className="font-semibold text-[var(--text-primary)]">To configure:</p>
          <ol className="list-decimal list-inside space-y-1.5 text-[var(--text-secondary)]">
            <li>Go to <span className="font-medium text-[var(--text-primary)]">Administration &gt; Plugins &gt; Admin Toolkit &gt; Settings</span></li>
            <li>Set <span className="font-medium text-[var(--text-primary)]">SQL Connection</span> to a database connection</li>
            <li>Restart the webapp (the connection is cached at startup)</li>
          </ol>
        </div>

        <p className="text-xs text-[var(--text-tertiary)]">
          If you are not a DSS administrator, contact your admin to configure the plugin.
        </p>
      </div>
    </div>
  );
}
