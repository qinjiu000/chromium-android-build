#!/usr/bin/env python3
"""Selective domain substitution for the de-Google Chromium build.

Rewrites Google (and a few other vendors') service domains hardcoded in the
Chromium source to dead .qjz9zk domains, so the browser cannot phone home to
them. Based on ungoogled-chromium's domain substitution, with a custom regex
list (patches/ungoogled/domain_regex.list) that EXCLUDES the three Google
services the user keeps: Google Search (www.google.com), Chrome Web Store
(chrome.google.com), and Safe Browsing endpoints (safebrowsing.googleapis.com).

IMPORTANT: run this AFTER all patches are applied (patch context lines may
contain Google domains that substitution would rewrite, breaking the patch).

Usage:
    python3 scripts/domain_substitution.py <tree-root> [--dry-run]

  <tree-root>  path to chromium/src
  --dry-run    report what would change without writing

The file list (patches/ungoogled/domain_substitution.list) is ungoogled's
158 file set; files missing from the 159 tree are skipped silently. New files
added in 159 that embed Google domains are not covered — the regexes are a
safety net, not a guarantee; the patches are the primary mechanism.
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REGEX_LIST = HERE / "patches" / "ungoogled" / "domain_regex.list"
FILE_LIST = HERE / "patches" / "ungoogled" / "domain_substitution.list"


def load_regexes(path):
    pairs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, replacement = line.split("#", 1)
        pairs.append((re.compile(pattern), replacement))
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tree", help="path to chromium/src")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tree = Path(args.tree)
    if not (tree / "chrome" / "VERSION").exists():
        print(f"FATAL: {tree} does not look like a chromium/src tree", file=sys.stderr)
        return 2

    pairs = load_regexes(REGEX_LIST)
    files = FILE_LIST.read_text(encoding="utf-8").splitlines()

    changed_files = 0
    total_subs = 0
    missing = 0
    for rel in files:
        rel = rel.strip()
        if not rel:
            continue
        # Structured-metrics codegen validates owner emails; rewriting their
        # domains breaks the build.
        if rel.startswith("tools/metrics/structured/"):
            continue
        p = tree / rel
        if not p.is_file():
            missing += 1
            continue
        try:
            data = p.read_bytes()
            text = data.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new_text, n = text, 0
        for pattern, replacement in pairs:
            new_text, count = pattern.subn(replacement, new_text)
            n += count
        if n:
            total_subs += n
            changed_files += 1
            if not args.dry_run:
                p.write_text(new_text, encoding="utf-8")

    print(f"domain substitution: {changed_files} files changed, "
          f"{total_subs} substitutions, {missing} listed files missing from tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
