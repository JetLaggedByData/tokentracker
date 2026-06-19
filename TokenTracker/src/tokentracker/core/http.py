"""
Shared HTTP session with retry logic for all providers.
NET-04: Retries on transient 5xx errors and connection failures.

SECURITY NOTE (Surface 3):
  We use the certifi CA bundle rather than the system CA store.
  This prevents corporate proxies with custom root CAs from silently
  intercepting API calls. If you operate behind a TLS-inspecting proxy
  you must either add your proxy CA to certifi's bundle or set
  REQUESTS_CA_BUNDLE=/path/to/your/ca.crt in the environment.
"""

import requests
import certifi
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (429, 500, 502, 503, 504),
) -> requests.Session:
    """
    Returns a requests.Session with automatic retry on transient failures.
    - 3 retries with exponential backoff (0.5s, 1s, 2s)
    - Retries on 429 (rate limit) and 5xx server errors
    - Does NOT retry on 4xx auth errors (401, 403) — those are permanent
    """
    session = requests.Session()
    # Surface 3 fix: use certifi CA bundle, not system CA store
    # Prevents corporate TLS-inspection proxies from silently intercepting calls
    session.verify = certifi.where()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods={"GET"},       # only retry safe idempotent methods
        raise_on_status=False,         # we handle status codes ourselves
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


# Module-level shared session — reused across all provider calls
_session: requests.Session = make_session()


def get(url: str, **kwargs) -> requests.Response:
    """Drop-in for requests.get() with retry built in."""
    return _session.get(url, **kwargs)
