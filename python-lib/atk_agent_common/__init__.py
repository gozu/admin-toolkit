"""Shared layer for the admin-toolkit plugin's agents (tools + plugin agents).

Everything the agent tools and agents do goes through here:
config resolution → ToolkitClient (HTTP to the admin-toolkit webapp backend)
→ tools_impl (pure functions, one per tool) → shaped, budgeted outputs.
"""
