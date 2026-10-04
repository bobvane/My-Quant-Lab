"""Source ingestion: user-supplied material is fetched here, never in the AI layer.

The AI layer only ever sees text (``backend/app/ai/research.py`` has no network
code at all).  Everything that talks to the outside world on behalf of a
research source lives in this package, and everything it returns is treated as
untrusted data by the roles that read it (ADR-163).
"""
