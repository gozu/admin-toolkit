import { useCallback, useState } from 'react';
import * as zip from '@zip.js/zip.js';
import { useDiag } from '../context/DiagContext';

interface UseExportLiteZipReturn {
  exportLiteZip: () => Promise<void>;
  isExporting: boolean;
  error: string | null;
}

export function useExportLiteZip(): UseExportLiteZipReturn {
  const { state } = useDiag();
  const { extractedFiles } = state;
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const exportLiteZip = useCallback(async () => {
    setIsExporting(true);
    setError(null);

    try {
      const zipWriter = new zip.ZipWriter(
        new zip.BlobWriter('application/zip')
      );

      for (const filePath of Object.keys(extractedFiles)) {
        // Skip log files to create a "lite" version
        if (filePath.match(/\.(log)$/)) continue;

        const content = extractedFiles[filePath];
        if (content) {
          await zipWriter.add(filePath, new zip.TextReader(content));
        }
      }

      const blob = await zipWriter.close();
      const url = URL.createObjectURL(blob);

      // Trigger download
      const a = document.createElement('a');
      a.href = url;
      a.download = 'diag-lite.zip';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);

      URL.revokeObjectURL(url);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
      console.error('Error creating lite zip:', err);
    } finally {
      setIsExporting(false);
    }
  }, [extractedFiles]);

  return { exportLiteZip, isExporting, error };
}
