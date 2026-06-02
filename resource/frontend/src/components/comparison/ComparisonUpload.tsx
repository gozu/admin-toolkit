import { useCallback, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { useFileProcessor } from '../../hooks/useFileProcessor';
import { useDataParser } from '../../hooks/useDataParser';
import { calculateHealthScore } from '../../hooks/useHealthScore';
import { PacmanLoader } from '../PacmanLoader';
import { computeFullComparison } from '../../utils/compareData';
import type { DiagFile } from '../../types';

interface UploadSlotProps {
  slot: 'before' | 'after';
  file: DiagFile | null;
  isProcessing: boolean;
  onFileSelect: (file: File) => void;
  onClear: () => void;
  accept: 'zip' | 'json';
}

function UploadSlot({ slot, file, isProcessing, onFileSelect, onClear, accept }: UploadSlotProps) {
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const label = slot === 'before' ? 'Before' : 'After';
  const description = accept === 'json'
    ? (slot === 'before' ? 'Older snapshot' : 'Newer snapshot')
    : (slot === 'before' ? 'Older diagnostic' : 'Newer diagnostic');
  const accentColor = slot === 'before' ? 'var(--neon-cyan)' : 'var(--neon-purple)';

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const droppedFile = e.dataTransfer.files[0];
    if (accept === 'zip' && droppedFile?.name.endsWith('.zip')) {
      onFileSelect(droppedFile);
    }
    if (accept === 'json' && droppedFile?.name.endsWith('.json')) {
      onFileSelect(droppedFile);
    }
  }, [accept, onFileSelect]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleClick = useCallback(() => {
    if (!file && !isProcessing) {
      fileInputRef.current?.click();
    }
  }, [file, isProcessing]);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (accept === 'zip' && selectedFile?.name.endsWith('.zip')) {
      onFileSelect(selectedFile);
    }
    if (accept === 'json' && selectedFile?.name.endsWith('.json')) {
      onFileSelect(selectedFile);
    }
    // Reset input so same file can be selected again
    e.target.value = '';
  }, [accept, onFileSelect]);

  // Processing state
  if (isProcessing) {
    return (
      <div className="flex-1 p-6 rounded-xl border-2 border-dashed border-[var(--border-glass)] bg-[var(--bg-surface)] flex flex-col items-center justify-center min-h-[320px]">
        <PacmanLoader />
        <p className="text-[var(--text-muted)] mt-4 text-sm">Processing {label.toLowerCase()} file...</p>
      </div>
    );
  }

  // File uploaded state
  if (file) {
    return (
      <motion.div
        className="flex-1 p-6 rounded-xl border-2 bg-[var(--bg-surface)] min-h-[320px]"
        style={{ borderColor: accentColor }}
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <span
            className="px-3 py-1 rounded-full text-sm font-medium"
            style={{ backgroundColor: `color-mix(in srgb, ${accentColor} 15%, transparent)`, color: accentColor }}
          >
            {label}
          </span>
          <button
            type="button"
            onClick={onClear}
            className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
            title="Remove file"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* File info */}
        <div className="space-y-3">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded-lg bg-[var(--bg-glass)]">
              <svg className="w-5 h-5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[var(--text-primary)] font-medium truncate" title={file.filename}>
                {file.filename}
              </p>
              <p className="text-[var(--text-muted)] text-sm">
                {formatBytes(file.fileSize)}
              </p>
            </div>
          </div>

          {/* Parsed data summary */}
          <div className="pt-3 border-t border-[var(--border-glass)] space-y-2">
            {file.parsedData.dssVersion && (
              <div className="flex justify-between text-sm">
                <span className="text-[var(--text-muted)]">DSS Version</span>
                <span className="text-[var(--text-primary)] font-mono">{file.parsedData.dssVersion}</span>
              </div>
            )}
            {file.parsedData.company && (
              <div className="flex justify-between text-sm">
                <span className="text-[var(--text-muted)]">Company</span>
                <span className="text-[var(--text-primary)]">{file.parsedData.company}</span>
              </div>
            )}
            {file.diagType && (
              <div className="flex justify-between text-sm">
                <span className="text-[var(--text-muted)]">Type</span>
                <span className={`badge ${file.diagType === 'instance' ? 'badge-success' : file.diagType === 'job' ? 'badge-info' : 'badge-purple'}`}>
                  {file.diagType}
                </span>
              </div>
            )}
            {file.healthScore && (
              <div className="flex justify-between text-sm items-center">
                <span className="text-[var(--text-muted)]">Health Score</span>
                <span className={`font-bold ${
                  file.healthScore.overall >= 80 ? 'text-green-400' :
                  file.healthScore.overall >= 60 ? 'text-yellow-400' : 'text-red-400'
                }`}>
                  {file.healthScore.overall}
                </span>
              </div>
            )}
          </div>
        </div>
      </motion.div>
    );
  }

  // Empty state - upload zone
  const hoverBorderColor = slot === 'before' ? 'var(--neon-cyan)' : 'var(--neon-purple)';
  return (
    <motion.div
      className={`flex-1 p-6 rounded-xl border-2 border-dashed cursor-pointer min-h-[320px]
                  flex flex-col items-center justify-center text-center transition-all duration-200
                  ${isDragging
                    ? `border-[${hoverBorderColor}] bg-[${hoverBorderColor}]/10`
                    : 'border-[var(--border-glass)] bg-[var(--bg-surface)]'
                  }`}
      style={{
        borderColor: isDragging ? accentColor : undefined,
        backgroundColor: isDragging ? `color-mix(in srgb, ${accentColor} 10%, transparent)` : undefined,
      }}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={handleClick}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
    >
      {/* Label badge */}
      <span
        className="px-3 py-1 rounded-full text-sm font-medium mb-4"
        style={{ backgroundColor: `color-mix(in srgb, ${accentColor} 15%, transparent)`, color: accentColor }}
      >
        {label}
      </span>

      {/* Icon */}
      <motion.div
        className="w-14 h-14 rounded-full flex items-center justify-center mb-4 transition-colors"
        style={{
          backgroundColor: isDragging ? `color-mix(in srgb, ${accentColor} 20%, transparent)` : 'var(--bg-glass)',
        }}
        whileHover={{
          scale: 1.05,
          backgroundColor: `color-mix(in srgb, ${accentColor} 15%, transparent)`,
        }}
      >
        <svg
          className="w-7 h-7 transition-colors"
          style={{ color: isDragging ? accentColor : 'var(--text-muted)' }}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
      </motion.div>

      {/* Text */}
      <p className="text-[var(--text-primary)] font-medium mb-1">
        {isDragging ? 'Drop file here' : `Drop ${description.toLowerCase()}`}
      </p>
      <p className="text-[var(--text-muted)] text-sm">
        or click to select
      </p>

      <input
        ref={fileInputRef}
        type="file"
        accept={accept === 'zip' ? '.zip' : '.json'}
        className="hidden"
        onChange={handleFileChange}
      />
    </motion.div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

// Parse date from diagnostic filename like dku_diagnosis_2025-02-12-15-11-44.zip
function parseDiagDate(filename: string): Date | null {
  const match = filename.match(/(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match;
  return new Date(
    parseInt(year),
    parseInt(month) - 1, // months are 0-indexed
    parseInt(day),
    parseInt(hour),
    parseInt(minute),
    parseInt(second)
  );
}

export function ComparisonUpload() {
  const {
    state,
    setComparisonFile,
    clearComparisonFile,
    setComparisonProcessing,
    setComparisonResult,
    setError,
  } = useDiag();
  const { comparison, dataSource } = state;
  const { processFile } = useFileProcessor();
  const { parseFilesSync } = useDataParser();
  const isApi = dataSource === 'api';

  interface SnapshotPayload {
    version?: number;
    metadata?: {
      timestamp?: string;
      dssVersion?: string;
      instanceUrl?: string;
      diagType?: string;
    };
    parsedData?: DiagFile['parsedData'];
  }

  const handleFileSelect = useCallback(async (slot: 'before' | 'after', file: File) => {
    setComparisonProcessing(slot, true);
    setError(null);

    // Yield to browser to render loading state before heavy processing
    await new Promise(resolve => setTimeout(resolve, 0));

    try {
      let diagFile: DiagFile;

      if (isApi && file.name.endsWith('.json')) {
        const text = await file.text();
        const snapshot = JSON.parse(text) as SnapshotPayload;
        if (!snapshot.parsedData) {
          throw new Error('Snapshot file is missing parsedData');
        }
        const uploadedAt = snapshot.metadata?.timestamp ? new Date(snapshot.metadata.timestamp) : new Date();
        const healthScore = calculateHealthScore(snapshot.parsedData);
        diagFile = {
          id: `${slot}-${Date.now()}`,
          filename: file.name,
          uploadedAt,
          fileSize: file.size,
          parsedData: snapshot.parsedData,
          extractedFiles: {},
          diagType: (snapshot.metadata?.diagType as DiagFile['diagType']) || 'instance',
          dsshome: '',
          originalFile: null,
          healthScore,
        };
      } else {
        const result = await processFile(file);
        const parsedData = parseFilesSync(
          result.extractedFiles,
          result.dsshome,
          result.projectFiles
        );
        const healthScore = calculateHealthScore(parsedData);
        diagFile = {
          id: `${slot}-${Date.now()}`,
          filename: file.name,
          uploadedAt: new Date(),
          fileSize: file.size,
          parsedData,
          extractedFiles: result.extractedFiles,
          diagType: result.diagType,
          dsshome: result.dsshome,
          originalFile: result.originalFile,
          healthScore,
        };
      }

      setComparisonFile(slot, diagFile);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(`Error processing ${slot} file: ${errorMessage}`);
    } finally {
      setComparisonProcessing(slot, false);
    }
  }, [isApi, processFile, parseFilesSync, setComparisonFile, setComparisonProcessing, setError]);

  const handleClear = useCallback((slot: 'before' | 'after') => {
    clearComparisonFile(slot);
  }, [clearComparisonFile]);

  const handleCompare = useCallback(() => {
    if (!comparison.before || !comparison.after) return;

    let beforeFile = comparison.before;
    let afterFile = comparison.after;

    // Check if files are in wrong order based on filename timestamps
    const beforeDate = parseDiagDate(beforeFile.filename) || beforeFile.uploadedAt;
    const afterDate = parseDiagDate(afterFile.filename) || afterFile.uploadedAt;

    if (beforeDate && afterDate && beforeDate > afterDate) {
      // Swap the files - user uploaded them in wrong order
      [beforeFile, afterFile] = [afterFile, beforeFile];
      // Update state so UI reflects the swap
      setComparisonFile('before', beforeFile);
      setComparisonFile('after', afterFile);
    }

    // Compute full comparison using the delta calculation engine
    const result = computeFullComparison(beforeFile, afterFile);

    setComparisonResult(result);
  }, [comparison.before, comparison.after, setComparisonFile, setComparisonResult]);

  const canCompare = comparison.before && comparison.after &&
                     !comparison.isProcessingBefore && !comparison.isProcessingAfter;

  return (
    <div className="w-full max-w-4xl p-8">
      <motion.div
        className="text-center mb-8"
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h2 className="text-2xl font-bold text-[var(--text-primary)] mb-2">
          Compare {isApi ? 'Snapshots' : 'Diagnostics'}
        </h2>
        <p className="text-[var(--text-muted)]">
          {isApi
            ? 'Upload two snapshot JSON files to compare changes between them'
            : 'Upload two diagnostic files to compare changes between them'}
        </p>
      </motion.div>

      {/* Upload zones */}
      <motion.div
        className="flex gap-6 mb-8"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <UploadSlot
          slot="before"
          file={comparison.before}
          isProcessing={comparison.isProcessingBefore}
          onFileSelect={(file) => handleFileSelect('before', file)}
          onClear={() => handleClear('before')}
          accept={isApi ? 'json' : 'zip'}
        />

        {/* Arrow between */}
        <div className="flex flex-col items-center justify-center gap-1 px-2">
          <motion.div
            className="w-12 h-12 rounded-full bg-gradient-to-br from-[var(--neon-cyan)]/20 to-[var(--neon-purple)]/20 border border-[var(--border-glass)] flex items-center justify-center shadow-lg"
            animate={{
              boxShadow: [
                '0 0 10px rgba(0, 245, 255, 0.1)',
                '0 0 20px rgba(168, 85, 247, 0.15)',
                '0 0 10px rgba(0, 245, 255, 0.1)',
              ],
            }}
            transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
          >
            <svg className="w-5 h-5 text-[var(--text-primary)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
            </svg>
          </motion.div>
        </div>

        <UploadSlot
          slot="after"
          file={comparison.after}
          isProcessing={comparison.isProcessingAfter}
          onFileSelect={(file) => handleFileSelect('after', file)}
          onClear={() => handleClear('after')}
          accept={isApi ? 'json' : 'zip'}
        />
      </motion.div>

      {/* Compare button */}
      <motion.div
        className="flex justify-center"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2 }}
      >
        <button
          type="button"
          onClick={handleCompare}
          disabled={!canCompare}
          className={`px-10 py-4 rounded-xl font-semibold text-lg transition-all duration-300 shadow-lg
                     ${canCompare
                       ? 'bg-gradient-to-r from-[var(--neon-cyan)] to-[var(--neon-purple)] text-white hover:opacity-90 hover:scale-105 hover:shadow-[0_0_30px_rgba(0,245,255,0.3)]'
                       : 'bg-[var(--bg-glass)] border border-[var(--border-glass)] text-[var(--text-muted)] cursor-not-allowed shadow-none'
                     }`}
        >
          {canCompare ? (
            <span className="flex items-center gap-2">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              Compare Files
            </span>
          ) : (
            'Upload both files to compare'
          )}
        </button>
      </motion.div>

      {/* Privacy note */}
      {!isApi && (
        <motion.p
          className="mt-8 text-center text-sm text-[var(--text-muted)] flex items-center justify-center gap-2"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
          Files are processed locally in your browser
        </motion.p>
      )}
    </div>
  );
}
