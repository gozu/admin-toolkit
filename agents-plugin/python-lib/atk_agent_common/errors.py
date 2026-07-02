"""Typed failures for the agent layer.

Every error an agent can see is a ToolkitError rendered through to_output():
{"error": {"code", "message", "remediation"}} — never a traceback. Tool
adapters catch ToolkitError at the top and return the payload as the tool
output so the model can read it and act (retry later, ask the user, pick a
valid host id, ...).
"""


class ToolkitError(Exception):
    code = 'backend-error'
    remediation = ''

    def __init__(self, message, remediation=None, detail=None):
        super().__init__(message)
        self.message = message
        if remediation is not None:
            self.remediation = remediation
        self.detail = detail

    def to_output(self):
        err = {'code': self.code, 'message': self.message}
        if self.remediation:
            err['remediation'] = self.remediation
        if self.detail:
            err['detail'] = self.detail
        return {'error': err}


class BackendError(ToolkitError):
    """Non-2xx response that maps to nothing more specific."""
    code = 'backend-error'


class UnreachableHost(ToolkitError):
    code = 'host-unreachable'
    remediation = ('The target DSS host did not respond. Check the host id with the '
                   'list-hosts tool, or ask an admin to verify the host preset in '
                   'Admin Toolkit Settings → Remote Hosts.')


class UnknownHost(ToolkitError):
    code = 'unknown-host'

    def __init__(self, host, valid_ids):
        super().__init__(
            "Unknown host id %r. Valid host ids: %s" % (host, ', '.join(valid_ids) or '(none)'),
            remediation='Call the list-hosts tool and use one of the returned ids verbatim.')
        self.valid_ids = valid_ids


class RedLocked(ToolkitError):
    code = 'red-locked'
    remediation = ('Mutating (red) actions are locked: no valid Advanced Actions password is '
                   'configured in the Admin Toolkit Agents plugin settings. An administrator '
                   'must set it there; agents cannot bypass this.')


class RemoteKeysLocked(ToolkitError):
    code = 'remote-keys-locked'
    remediation = ('The remote host\'s API key is encrypted and the configured host-keys '
                   'password does not unlock it (or none is configured). An administrator '
                   'must set the correct password in the Admin Toolkit Agents plugin settings.')


class MacroProjectMissing(ToolkitError):
    code = 'macro-project-missing'
    remediation = ('The ADMINTOOLKIT macro project is not installed on this host. An '
                   'administrator must open the Admin Toolkit webapp, select this host, and '
                   'complete the one-time install prompt (Settings → install).')


class ScanTimeout(ToolkitError):
    code = 'scan-running'

    def __init__(self, message, progress=None, advice=None):
        super().__init__(message, remediation=advice or 'Re-invoke this tool in a few minutes; '
                         'the scan keeps running server-side and the retry will hit a warm cache.')
        self.progress = progress

    def to_output(self):
        out = super().to_output()
        out['status'] = 'scan_running'
        if self.progress is not None:
            out['progress'] = self.progress
        return out
