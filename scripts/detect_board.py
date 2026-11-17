"""Detect the ATS behind one or more company careers URLs and print (board, slug, open jobs).

    python scripts/detect_board.py https://company.com/careers [more urls...]

Read-only: it never touches the database. To add a detected board to the live crawl, call the
admin endpoint instead: GET /admin/detect-board?token=$RUN_TOKEN&url=<url>&add=1
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sources import detect  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    for url in argv:
        res = detect.detect(url, validate=True)
        print(json.dumps({k: res.get(k) for k in ("url", "board", "slug", "via", "jobs", "candidates")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
