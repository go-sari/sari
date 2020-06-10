from concurrent.futures import ThreadPoolExecutor

from requests.adapters import HTTPAdapter
from requests_futures.sessions import FuturesSession
from urllib3.util.retry import Retry

SC_TOO_MANY_REQUESTS = 429


def async_retryable_session(executor: ThreadPoolExecutor) -> FuturesSession:
    session = FuturesSession(executor)
    retries = 3
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=0.5,
        status_forcelist=(SC_TOO_MANY_REQUESTS,),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session
