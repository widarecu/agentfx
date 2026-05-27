#!/usr/bin/env python3
"""Trigger nightly evolution runs.

Invoked by the Railway cron job declared in `railway.toml`. POSTs one
`/api/v1/evolution-runs` per configured instrument to the live API. The
orchestrator's subprocess fork then runs the loop inside the long-running
backend service — this script just kicks them off and exits.

Configuration via env vars (set in the Railway service-variables panel):

  AGENTFX_BASE_URL      Base URL of the backend service. Defaults to
                        http://localhost:8000 for local development.
  CRON_SERVICE_TOKEN    JWT (or API key) the cron uses to authenticate.
                        Required in production; a missing value here
                        means the cron has no way to authenticate, so
                        we abort with exit code 2.
  CRON_INSTRUMENTS      Comma-separated list of instruments to schedule
                        runs for. Defaults to "EUR_USD".
  CRON_GRANULARITY      Candle granularity. Defaults to "M1".
  CRON_DATA_YEARS       History window. Defaults to 5.
  CRON_LLM_BUDGET_USD   Per-run LLM budget. Defaults to 5.00.

Exit codes:
  0  All scheduled runs accepted by the API
  1  At least one run failed to schedule
  2  Misconfiguration (no auth token, etc.)
"""

from __future__ import annotations

import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    base_url = os.environ.get("AGENTFX_BASE_URL", "http://localhost:8000").rstrip("/")
    token = os.environ.get("CRON_SERVICE_TOKEN")
    if not token:
        logger.error(
            "CRON_SERVICE_TOKEN is not set; the cron has no way to authenticate."
        )
        return 2

    instruments = [
        s.strip()
        for s in os.environ.get("CRON_INSTRUMENTS", "EUR_USD").split(",")
        if s.strip()
    ]
    granularity = os.environ.get("CRON_GRANULARITY", "M1")
    data_years = int(os.environ.get("CRON_DATA_YEARS", "5"))
    llm_budget_usd = float(os.environ.get("CRON_LLM_BUDGET_USD", "5.00"))

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    failures: list[str] = []

    for instrument in instruments:
        body = {
            "instrument": instrument,
            "granularity": granularity,
            "data_years": data_years,
            "llm_budget_usd": llm_budget_usd,
        }
        try:
            r = requests.post(
                f"{base_url}/api/v1/evolution-runs",
                json=body,
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            payload = r.json()
            logger.info(
                "scheduled %s — run_id=%s status=%s",
                instrument,
                payload.get("run_id"),
                payload.get("status"),
            )
        except requests.HTTPError as e:
            failures.append(f"{instrument}: {e.response.status_code} {e.response.text}")
            logger.exception("failed to schedule %s", instrument)
        except requests.RequestException as e:
            failures.append(f"{instrument}: network error {e}")
            logger.exception("failed to reach API for %s", instrument)

    if failures:
        logger.error("scheduled with failures: %s", failures)
        return 1

    logger.info("all %d instrument(s) scheduled", len(instruments))
    return 0


if __name__ == "__main__":
    sys.exit(main())
