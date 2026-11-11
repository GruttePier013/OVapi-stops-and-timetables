"""Save a real OVapi answer as a test fixture.

    python scripts/capture_ovapi_fixture.py 30000001 [30000002 ...]

Writes ``tests/fixtures/ovapi_<codes>.json``; ``tests/test_contract.py`` picks up every ``ovapi_*.json`` automatically.
Run it on a computer that can reach v0.ovapi.nl, then check the file for anything you do not want to publish.
"""
from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from pathlib import Path

URL = "https://v0.ovapi.nl/tpc/{}"
USER_AGENT = "ovapi_bus_departures-fixture-capture"


def main(codes: list[str]) -> None:
    if not codes or not all(code.isascii() and code.isdigit() for code in codes):
        sys.exit("Give one or more numeric TPC codes.")
    context = ssl.create_default_context()
    request = urllib.request.Request(URL.format(",".join(codes)), headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        data = urllib.request.urlopen(request, timeout=20, context=context).read()  # noqa: S310
    except ssl.SSLError:  # v0.ovapi.nl has had certificate problems; accept it for this one-off capture
        context = ssl._create_unverified_context()  # noqa: S323, SLF001
        data = urllib.request.urlopen(request, timeout=20, context=context).read()  # noqa: S310
    payload = json.loads(data)
    target = Path(__file__).parent.parent / "tests" / "fixtures" / f"ovapi_{'_'.join(codes)}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {target}")


if __name__ == "__main__":
    main(sys.argv[1:])
