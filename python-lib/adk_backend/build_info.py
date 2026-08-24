"""Version of the backend code that is actually *running*.

Stamped at build time by `scripts/bump_version.py`, in lockstep with
plugin.json. Deliberately a constant baked into the shipped Python source and
NOT a runtime lookup: DSS snapshots the plugin's code when a webapp backend
starts and never revisits it, so **updating the plugin does not restart, or
otherwise touch, an already-running webapp backend**. The frontend has no such
inertia — it is served straight from the installed plugin — so the first browser
reload after an upgrade pairs a brand-new UI with the previous release's Python.

Comparing this constant (what is loaded in this process) against the installed
version (`misc._plugin_version()`, read live from the DSS API) is what turns
that skew into a visible warning instead of a scatter of mystery 400s and
missing endpoints. Import it at module scope so the value is frozen at backend
boot alongside the rest of the code it describes.
"""

BUILD_VERSION = '0.4.818'
