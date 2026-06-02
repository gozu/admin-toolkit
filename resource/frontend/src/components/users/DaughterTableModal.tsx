import { Modal } from '../Modal';
import { DataGrid } from '../common/DataGrid';
import type { DaughterSpec } from '../../utils/userDaughterSpecs';

interface DaughterTableModalProps {
  spec: DaughterSpec | null;
  isOpen: boolean;
  onClose: () => void;
}

export function DaughterTableModal({ spec, isOpen, onClose }: DaughterTableModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title={spec?.title || 'Details'} sizePreset="large">
      {!spec ? (
        <div className="text-sm text-[var(--text-muted)]">No data.</div>
      ) : (
        <DataGrid
          rows={spec.rows}
          columns={spec.columns}
          rowKey={(_, i) => String(i)}
          defaultSortColumnId={spec.defaultSortColumn}
          defaultSortDir={spec.defaultSortDir}
          emptyMessage={spec.emptyMessage || 'No rows.'}
          showRowCount
          scroll={{ maxH: '70vh' }}
        />
      )}
    </Modal>
  );
}
