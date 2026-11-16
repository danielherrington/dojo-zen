"""ClassDojo Anti-Bloat Agent and Digest Engine."""

import os

for _proxy_var in ("NO_PROXY", "no_proxy"):
    _val = os.environ.get(_proxy_var)
    if _val and "[::1]" in _val:
        os.environ[_proxy_var] = _val.replace("[::1]", "::1")

__version__ = "0.1.0"
