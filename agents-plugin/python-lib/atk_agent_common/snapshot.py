"""Daily triage snapshot zips — schema-free record of the sweep's scan inputs.

One zip per sweep run, named admin-toolkit-snapshot-<YYMMDDHHMM>.zip, written
into a managed folder in the scenario's project: `<host>/<name>.json` entries
(exactly what the scan endpoints returned — scan-time output is the record, no
extra sanitization layer) plus a manifest.json. No schema, no migrations.

Managed-folder API shapes verified live (tam-global):
  project.list_managed_folders() -> [{'id', 'name', ...}]
  project.create_managed_folder(name) -> DSSManagedFolder (.id)
  folder.put_file(name, bytes) -> {'path', 'size', 'lastModified'}
"""
import io
import json
import zipfile

DEFAULT_FOLDER_NAME = 'admin-toolkit-snapshots'


def build_snapshot_zip(payloads_by_host, manifest):
    """Zip bytes: manifest.json + one <host>/<name>.json entry per collected
    payload (None payloads — inputs that were skipped — are omitted)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, indent=2, default=str))
        for host in sorted(payloads_by_host or {}):
            for name in sorted(payloads_by_host[host] or {}):
                payload = payloads_by_host[host][name]
                if payload is None:
                    continue
                zf.writestr('%s/%s.json' % (host, name), json.dumps(payload, default=str))
    return buf.getvalue()


def resolve_snapshot_folder(project, folder_ref=''):
    """Managed folder to write into: `folder_ref` may be a folder id or name;
    empty ⇒ find-or-create DEFAULT_FOLDER_NAME in `project`."""
    ref = (folder_ref or '').strip()
    folders = project.list_managed_folders()
    if ref:
        for f in folders:
            if f.get('id') == ref or f.get('name') == ref:
                return project.get_managed_folder(f['id'])
        raise RuntimeError('Snapshot managed folder %r not found in project %s'
                           % (ref, project.project_key))
    for f in folders:
        if f.get('name') == DEFAULT_FOLDER_NAME:
            return project.get_managed_folder(f['id'])
    return project.create_managed_folder(DEFAULT_FOLDER_NAME)


def write_snapshot(project, payloads_by_host, manifest, stamp, folder_ref=''):
    """Build + upload the run's snapshot zip; returns {folderId, file, bytes}."""
    folder = resolve_snapshot_folder(project, folder_ref)
    zip_name = 'admin-toolkit-snapshot-%s.zip' % stamp
    data = build_snapshot_zip(payloads_by_host, manifest)
    folder.put_file(zip_name, data)
    return {'folderId': folder.id, 'file': zip_name, 'bytes': len(data)}
