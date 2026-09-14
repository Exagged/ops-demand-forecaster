"""Small Socrata client: ordered paging, throttling, and retries.

The public endpoint does not publish a fixed quota for unauthenticated reads,
and everyone on the same IP shares a throttling pool. So we assume nothing:
sleep between calls, honor Retry-After, and back off on 429 and 5xx.

Paging is ordered by unique_key rather than left to the server's default,
because an unordered offset walk over a dataset that is being written to can
skip or repeat rows between pages.
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class SocrataError(RuntimeError):
    pass


class SocrataClient:
    def __init__(
        self,
        json_url: str,
        page_size: int = 5000,
        requests_per_second: float = 2.0,
        max_retries: int = 6,
        app_token: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.json_url = json_url
        self.page_size = page_size
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        if app_token:
            self.session.headers["X-App-Token"] = app_token
            log.info("Using Socrata app token")
        else:
            log.info("No Socrata app token set, sharing the anonymous throttling pool")
        self._last_call = 0.0

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _get(self, params: dict) -> list[dict]:
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn()
            try:
                response = self.session.get(self.json_url, params=params, timeout=self.timeout)
                self._last_call = time.monotonic()
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise SocrataError(f"request failed after {attempt} attempts: {exc}") from exc
                self._sleep_backoff(attempt, None)
                continue

            if response.status_code in RETRY_STATUS:
                if attempt == self.max_retries:
                    raise SocrataError(f"{response.status_code} after {attempt} attempts")
                self._sleep_backoff(attempt, response.headers.get("Retry-After"))
                continue

            if response.status_code >= 400:
                raise SocrataError(f"{response.status_code}: {response.text[:400]}")

            return response.json()

        raise SocrataError("retries exhausted")

    def _sleep_backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 2.0**attempt
        else:
            delay = 2.0**attempt
        delay = min(delay, 60.0)
        log.warning("Backing off %.1fs (attempt %d)", delay, attempt)
        time.sleep(delay)

    def count(self, where: str) -> int:
        """Row count for a where clause, used to reconcile a loaded partition."""
        rows = self._get({"$select": "count(unique_key) as n", "$where": where})
        if not rows:
            return 0
        return int(rows[0].get("n", 0))

    def iter_pages(self, where: str) -> Iterator[list[dict]]:
        """Yield ordered pages until a short page comes back."""
        offset = 0
        while True:
            params = {
                "$where": where,
                "$order": "unique_key",
                "$limit": self.page_size,
                "$offset": offset,
            }
            page = self._get(params)
            if not page:
                return
            yield page
            if len(page) < self.page_size:
                return
            offset += self.page_size
