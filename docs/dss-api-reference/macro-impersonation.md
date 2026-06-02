# Macro Impersonation in Dataiku Plugin Runnables

## Overview

When a user triggers a macro (python-runnable) in DSS, the `impersonate` setting in `runnable.json` controls **which Linux user** the macro's process runs as:

| `impersonate` | Runs as | Use when |
|---|---|---|
| `true` | The Linux user mapped to the DSS user who clicks "Run" (via User Isolation Framework) | The macro needs to act **on behalf of** the triggering user, respecting their permissions |
| `false` | The `dataiku` service account (the Linux user running the DSS process) | The macro performs system-level operations that require elevated or shared access |

## Setting it

In your macro's `runnable.json`, set the `impersonate` field at the top level:

```json
{
  "meta": {
    "label": "My Macro",
    "description": "..."
  },
  "impersonate": true,
  "params": [ ... ]
}
```

## Examples from this plugin

### `impersonate: true` -- Rebuild code envs

```json
{
  "meta": {
    "label": "Rebuild code envs",
    "description": "Rebuild some or all code envs, including building/pushing images"
  },
  "impersonate": true,
  "requiresGlobalAdmin": true
}
```

**Why impersonate here?** This macro calls the DSS API (`dataiku.api_client()`) to list and rebuild code environments. By impersonating the triggering user, the API calls carry that user's identity and permissions. Combined with `requiresGlobalAdmin: true`, this ensures only a global admin can run it, and the resulting API actions are auditable back to that specific admin.

### `impersonate: false` -- Remove redundant local images

```json
{
  "meta": {
    "label": "Remove redundant local images",
    "description": "Macro to identify and delete images that are no longer required"
  },
  "impersonate": false,
  "requiresGlobalAdmin": true
}
```

**Why not impersonate here?** This macro talks directly to the Docker daemon via the Docker socket (`unix:///var/run/docker.sock`). The triggering user's Linux account likely does not have access to the Docker socket -- only the `dataiku` service account (which is typically in the `docker` group) does. Impersonating the user would cause a permission denied error.

### `impersonate: false` -- Clear old Jupyter working dirs

```json
{
  "meta": {
    "label": "Clear old Jupyter working dirs",
    "description": "Free up disk space, by removing old Jupyter working directories"
  },
  "impersonate": false,
  "requiresGlobalAdmin": true
}
```

**Why not impersonate here?** Jupyter working directories are stored under the DSS data directory, owned by the `dataiku` service account. An impersonated user would not have filesystem permissions to list or delete other users' working directories.

## How to choose

Use this decision guide:

1. **Does the macro only use the DSS API?** -> `impersonate: true` is usually correct. The API respects per-user permissions and actions are attributed to the triggering user in audit logs.

2. **Does the macro access system resources (Docker, filesystem, shell commands)?** -> `impersonate: false` is likely needed, since the `dataiku` service account has the required OS-level permissions.

3. **Does the macro do both?** -> Prefer `impersonate: false` and use a personal API key or the macro's built-in client for API calls. System-level access is the harder constraint to work around.

## Interaction with `requiresGlobalAdmin`

`impersonate` and `requiresGlobalAdmin` are independent settings:

| Combination | Meaning |
|---|---|
| `impersonate: true` + `requiresGlobalAdmin: true` | Only global admins can run it; runs as their Linux user |
| `impersonate: false` + `requiresGlobalAdmin: true` | Only global admins can run it; runs as `dataiku` |
| `impersonate: true` + `requiresGlobalAdmin: false` | Any permitted user can run it; runs as their Linux user |
| `impersonate: false` + `requiresGlobalAdmin: false` | Any permitted user can run it; runs as `dataiku` |

All three macros in this plugin use `requiresGlobalAdmin: true` because they perform administrative operations (rebuilding code envs, deleting Docker images, cleaning up Jupyter directories) regardless of whether they impersonate.

## Security considerations

- When `impersonate: false`, the macro runs with the full privileges of the `dataiku` service account. Ensure the macro code does not allow arbitrary actions that could be abused by a non-admin user (unless gated by `requiresGlobalAdmin`).
- When `impersonate: true`, be aware that the impersonated user's permissions apply. If the macro requires access to resources the user doesn't own, it will fail with permission errors.
- In both cases, `requiresGlobalAdmin: true` is strongly recommended for macros that modify system state.
