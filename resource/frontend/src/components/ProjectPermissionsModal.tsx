import { Modal } from './Modal';
import { formatCamelCase } from '../utils/formatters';
import type { Project, Permission } from '../types';

interface ProjectPermissionsModalProps {
  isOpen: boolean;
  onClose: () => void;
  project: Project | null;
}

export function ProjectPermissionsModal({
  isOpen,
  onClose,
  project,
}: ProjectPermissionsModalProps) {
  if (!project) return null;

  // Group permissions by type (Group/User)
  const groupedPermissions: Record<string, Permission[]> = {
    Group: [],
    User: [],
  };

  for (const perm of project.permissions) {
    if (groupedPermissions[perm.type]) {
      groupedPermissions[perm.type].push(perm);
    } else {
      groupedPermissions[perm.type] = [perm];
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Project: ${project.name}`}
    >
      {/* Project details */}
      <div className="bg-[var(--bg-elevated)] rounded-lg p-4 mb-6 border border-[var(--border-glass)]">
        <div className="grid grid-cols-3 gap-4 text-sm">
          <div>
            <span className="text-[var(--text-muted)]">Project Key:</span>
            <span className="ml-2 font-medium text-[var(--text-primary)]">{project.key}</span>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Owner:</span>
            <span className="ml-2 font-medium text-[var(--text-primary)]">{project.owner}</span>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Permission Entries:</span>
            <span className="ml-2 font-medium text-[var(--text-primary)]">
              {project.permissions.length}
            </span>
          </div>
        </div>
      </div>

      {/* Permissions by type */}
      {Object.entries(groupedPermissions).map(
        ([type, perms]) =>
          perms.length > 0 && (
            <div key={type} className="mb-6">
              <h4 className="text-lg font-semibold text-[var(--text-primary)] mb-3">
                {type} Permissions ({perms.length})
              </h4>

              <div className="space-y-3">
                {perms.map((perm, idx) => (
                  <PermissionCard key={idx} permission={perm} />
                ))}
              </div>
            </div>
          )
      )}
    </Modal>
  );
}

function PermissionCard({ permission }: { permission: Permission }) {
  const allowedEntries = Object.entries(permission.permissions).filter(
    ([, value]) => value === true
  );

  return (
    <div className="border border-[var(--border-glass)] rounded-lg p-4 bg-[var(--bg-surface)]">
      <h5 className="font-medium text-[var(--text-primary)] mb-3">{permission.name}</h5>

      {allowedEntries.length > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
          {allowedEntries.map(([key]) => (
            <div key={key} className="flex items-center gap-2 text-sm">
              <span className="text-[var(--neon-green)] font-bold">&#x2713;</span>
              <span className="text-[var(--text-secondary)]">
                {formatCamelCase(key)}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-[var(--text-muted)]">No permissions granted</p>
      )}
    </div>
  );
}
