"""ApplyPilot local web app (FastAPI backend).

Wraps the existing pipeline as a local-first web service so non-technical users
can run ApplyPilot in a browser instead of the CLI. Single-user, SQLite stays
local under ~/.applypilot — no accounts, no external database, no deployment.

Start it with: ``applypilot serve``
"""
