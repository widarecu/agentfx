"""Inbound webhook endpoints from third-party platforms.

Grouped so the main router mount stays readable and each platform's
auth / replay / dedup lives in one place rather than being shared
across unrelated routers.

Today:

- `linear` — Linear Agent platform webhook receiver (AFX-66).
"""
