import { useDiag } from '../context/DiagContext';

interface FileDownloadButtonsProps {
  onViewFile: (filename: string, content: string) => void;
  onDownloadFile: (filename: string, content: string) => void;
}

export function FileDownloadButtons({
  onViewFile,
  onDownloadFile,
}: FileDownloadButtonsProps) {
  const { state } = useDiag();
  const { extractedFiles, dsshome } = state;

  // Key files to show
  const keyFiles = [
    { path: 'diag.txt', label: 'diag.txt' },
    { path: dsshome + 'run/backend.log', label: 'backend.log' },
    { path: dsshome + 'run/supervisord.log', label: 'supervisord.log' },
    { path: dsshome + 'config/general-settings.json', label: 'general-settings.json' },
    { path: dsshome + 'config/connections.json', label: 'connections.json' },
    { path: dsshome + 'config/license.json', label: 'license.json' },
    { path: dsshome + 'bin/env-default.sh', label: 'env-default.sh' },
    { path: dsshome + 'bin/env-site.sh', label: 'env-site.sh' },
    { path: 'output.log', label: 'output.log' },
  ];

  // Find which files are available
  const availableFiles = keyFiles
    .map((file) => {
      // Try exact path first
      if (extractedFiles[file.path]) {
        return { ...file, actualPath: file.path };
      }
      // Try ends-with match
      const matchingPath = Object.keys(extractedFiles).find((p) =>
        p.endsWith(file.path)
      );
      if (matchingPath) {
        return { ...file, actualPath: matchingPath };
      }
      // Try filename-only match
      const fileName = file.path.split('/').pop();
      if (fileName) {
        const fileNameMatch = Object.keys(extractedFiles).find(
          (p) => p.endsWith('/' + fileName) || p === fileName
        );
        if (fileNameMatch) {
          return { ...file, actualPath: fileNameMatch };
        }
      }
      return null;
    })
    .filter((f): f is { path: string; label: string; actualPath: string } => f !== null);

  if (availableFiles.length === 0) {
    return null;
  }

  return (
    <div className="bg-[var(--bg-surface)] rounded-xl shadow-md p-4 mb-6 border border-[var(--border-glass)]">
      <h4 className="text-lg font-semibold text-[var(--text-primary)] mb-3">Key Files</h4>
      <div className="flex flex-wrap gap-2">
        {availableFiles.map((file) => (
          <div key={file.actualPath} className="flex gap-1">
            <button
              onClick={() =>
                onViewFile(file.label, extractedFiles[file.actualPath])
              }
              className="px-3 py-1.5 text-sm font-medium text-[var(--neon-cyan)] bg-[var(--neon-cyan)]/10
                         rounded-l-lg hover:bg-[var(--neon-cyan)]/30 transition-colors"
            >
              {file.label}
            </button>
            <button
              onClick={() =>
                onDownloadFile(file.label, extractedFiles[file.actualPath])
              }
              className="px-2 py-1.5 text-sm text-[var(--text-secondary)] bg-[var(--bg-elevated)]
                         rounded-r-lg hover:bg-[var(--neon-cyan)]/20 hover:text-[var(--neon-cyan)] transition-colors"
              title={`Download ${file.label}`}
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
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
