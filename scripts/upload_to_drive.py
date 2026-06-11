#!/usr/bin/env python3
"""Upload the freshly-built plugin ZIP to a Google Drive (Shared Drive) folder.

Invoked by `make deploy` after the plugin archive is produced. Designed to be a
soft no-op when not configured, so deploys never break for a checkout without
the service-account key.

Credential resolution (first hit wins):
  - service account: $GDRIVE_SA, else ./.gdrive-sa.json, else ./fe-us-host-*.json
  - target folder:   $GDRIVE_FOLDER_ID, else ./.gdrive-folder-id (one line)

Usage:
  python3 scripts/upload_to_drive.py dist/<plugin>.zip
  python3 scripts/upload_to_drive.py            # auto-picks newest dist/*.zip
"""
import glob
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log(msg):
    print(f"[drive] {msg}")


def _find_sa_key():
    if os.environ.get("GDRIVE_SA"):
        return os.environ["GDRIVE_SA"]
    explicit = os.path.join(REPO, ".gdrive-sa.json")
    if os.path.exists(explicit):
        return explicit
    matches = sorted(glob.glob(os.path.join(REPO, "fe-us-host-*.json")))
    return matches[0] if matches else None


def _find_folder_id():
    if os.environ.get("GDRIVE_FOLDER_ID"):
        return os.environ["GDRIVE_FOLDER_ID"].strip()
    f = os.path.join(REPO, ".gdrive-folder-id")
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            return fh.read().strip()
    return None


def _find_zip(argv):
    if len(argv) > 1 and argv[1]:
        return argv[1]
    zips = sorted(
        glob.glob(os.path.join(REPO, "dist", "*.zip")),
        key=os.path.getmtime,
        reverse=True,
    )
    return zips[0] if zips else None


def main(argv):
    sa_key = _find_sa_key()
    folder_id = _find_folder_id()

    # Soft no-op: not configured → warn and let the deploy succeed.
    if not sa_key or not folder_id:
        missing = []
        if not sa_key:
            missing.append("service-account key (.gdrive-sa.json / fe-us-host-*.json)")
        if not folder_id:
            missing.append("folder id (.gdrive-folder-id)")
        _log(f"skip — not configured: missing {', '.join(missing)}")
        return 0

    zip_path = _find_zip(argv)
    if not zip_path or not os.path.exists(zip_path):
        _log(f"skip — no ZIP found ({zip_path or 'dist/*.zip'})")
        return 0

    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = service_account.Credentials.from_service_account_file(
        sa_key, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    name = os.path.basename(zip_path)
    media = MediaFileUpload(zip_path, mimetype="application/zip", resumable=True)

    # Update in place if a same-name file already exists in the folder (no dupes).
    q = (
        f"name = '{name}' and '{folder_id}' in parents and trashed = false"
    )
    existing = (
        drive.files()
        .list(
            q=q,
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
        )
        .execute()
        .get("files", [])
    )

    if existing:
        file_id = existing[0]["id"]
        drive.files().update(
            fileId=file_id, media_body=media, supportsAllDrives=True,
            fields="id, webViewLink",
        ).execute()
        _log(f"updated existing '{name}' in folder")
    else:
        meta = {"name": name, "parents": [folder_id]}
        created = (
            drive.files()
            .create(
                body=meta, media_body=media, supportsAllDrives=True,
                fields="id, webViewLink",
            )
            .execute()
        )
        _log(f"uploaded '{name}' -> {created.get('webViewLink', created.get('id'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
