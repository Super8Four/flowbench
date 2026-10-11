"""Container readiness probe; no database or extra curl dependency required."""

import json
import sys
from urllib.request import urlopen


def main():
    try:
        with urlopen("http://127.0.0.1:5000/api/ready", timeout=3) as response:
            ready = response.status == 200 and json.load(response).get("ready") is True
    except (OSError, ValueError):
        ready = False
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
