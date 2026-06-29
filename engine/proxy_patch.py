"""Apply SOCKS5 proxy patch to aiohttp for ccxt.pro compatibility."""
from aiohttp_socks import ProxyConnector
import aiohttp

SOCKS5_URL = "socks5://127.0.0.1:10808"

_original_init = aiohttp.ClientSession.__init__

def _patched_init(self, *args, **kwargs):
    if "connector" not in kwargs:
        kwargs["connector"] = ProxyConnector.from_url(SOCKS5_URL)
    _original_init(self, *args, **kwargs)

aiohttp.ClientSession.__init__ = _patched_init

# Also patch freqtrade's exchange to avoid duplicate proxy
import os
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
