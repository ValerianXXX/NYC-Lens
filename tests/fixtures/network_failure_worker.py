"""Exercise the real worker failure envelope without accessing any network."""
import httpx
from nyc_lens import assistant
from nyc_lens.analysis_worker import main


def unavailable(*args, **kwargs):
    raise httpx.RemoteProtocolError('synthetic-upstream-sensitive-text-must-not-escape')


assistant.execute_comparison = unavailable
main()
