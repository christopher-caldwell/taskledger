#!/usr/bin/env python3
"""Check local Markdown destinations and UI manifest hashes, without network I/O.

Checks inline links and reference definitions outside fenced code blocks.
Anchors, external URLs, and example commands are deliberately outside scope.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)")
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)")


def check() -> list[str]:
    errors = []
    documents = sorted({
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "skills").rglob("*.md"),
        *(ROOT / "benchmarks").rglob("*.md"),
        *(ROOT / "audits").rglob("*.md"),
    })
    for document in documents:
        fence = None
        for number, line in enumerate(document.read_text().splitlines(), 1):
            boundary = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if boundary:
                marker = boundary.group(1)
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = None
                continue
            if fence is not None:
                continue
            destinations = INLINE_LINK.findall(line)
            reference = REFERENCE.match(line)
            if reference:
                destinations.append(reference.group(1))
            for destination in destinations:
                url = urlsplit(destination.strip("<>"))
                if url.scheme or url.netloc or not url.path:
                    continue
                path = unquote(url.path)
                target = ROOT / path.lstrip("/") if path.startswith("/") else document.parent / path
                if not target.exists():
                    errors.append(f"{document.relative_to(ROOT)}:{number}: missing {destination}")

    manifest_path = ROOT / "docs/taskledger-ui-architecture/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name, expected in manifest["files"].items():
        path = manifest_path.parent / name
        if not path.is_file():
            errors.append(f"{manifest_path.relative_to(ROOT)}: missing {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"{manifest_path.relative_to(ROOT)}: SHA-256 mismatch for {name}")
    return errors


if __name__ == "__main__":
    failures = check()
    for failure in failures:
        print(failure)
    if failures:
        raise SystemExit(1)
    print("Local Markdown destinations and UI manifest hashes pass.")
