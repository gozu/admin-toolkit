import { useState, type ReactNode } from 'react';
import { Modal } from '../Modal';
import { Button } from './Button';

interface ConfirmDeleteDialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  // Exact phrase the user must type, e.g. `delete 3 images`.
  confirmPhrase: string;
  confirmLabel: string;
  loadingLabel: string;
  loading: boolean;
  error: string | null;
  progress?: string;
  onConfirm: () => void;
  // Site-specific intro/list/notes rendered above the type-to-confirm line.
  children: ReactNode;
}

// Outer wrapper unmounts the content when closed so the typed confirmation
// resets on every open (Modal itself renders nothing while closed anyway).
export function ConfirmDeleteDialog(props: ConfirmDeleteDialogProps) {
  if (!props.isOpen) return null;
  return <ConfirmDeleteDialogContent {...props} />;
}

function ConfirmDeleteDialogContent({
  isOpen,
  onClose,
  title,
  confirmPhrase,
  confirmLabel,
  loadingLabel,
  loading,
  error,
  progress,
  onConfirm,
  children,
}: ConfirmDeleteDialogProps) {
  const [input, setInput] = useState('');

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="modalCancel" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="modalDanger"
            onClick={onConfirm}
            disabled={loading || input !== confirmPhrase}
          >
            {loading ? loadingLabel : confirmLabel}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        {children}
        <p className="text-sm text-[var(--text-muted)]">
          Type{' '}
          <code className="px-1.5 py-0.5 rounded bg-[var(--bg-glass)] text-[var(--text-primary)]">
            {confirmPhrase}
          </code>{' '}
          to confirm.
        </p>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={confirmPhrase}
          className="w-full input-glass font-mono text-sm"
          autoFocus
        />
        {progress && !error && (
          <div className="text-sm text-[var(--text-secondary)]">{progress}</div>
        )}
        {error && <div className="text-sm text-[var(--neon-red)]">{error}</div>}
      </div>
    </Modal>
  );
}
