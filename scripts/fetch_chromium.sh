#!/usr/bin/env bash
# =============================================================================
# Chromium source fetch (adapted from iNWEB-Browser scripts/fetch_chromium.sh)
#
# Reproduces a pristine upstream Chromium checkout at the pinned baseline tag
# with Android as a target OS, per upstream Android build instructions.
# Runs on the GitHub-hosted runner; measured ~10-15 min per hop by iNWEB.
#
# Tag source (first match wins):
#   1. $CHROMIUM_TAG env var (set by the workflows from the chromium_tag input)
#   2. config/chromium/BASELINE
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Pinned tooling (same pin iNWEB validated on ubuntu-24.04 runners).
DEPOT_TOOLS_URL="https://chromium.googlesource.com/chromium/tools/depot_tools.git"
DEPOT_TOOLS_PINNED_REV="73fc5a4d6bd051f1fd58404e62dfb83f734b0cfa"

WORKSPACE="${CAB_WORKSPACE:-$ROOT/chromium}"

# -----------------------------------------------------------------------------
# 1. Read the pinned Chromium tag
# -----------------------------------------------------------------------------
if [ -n "${CHROMIUM_TAG:-}" ]; then
  TAG="$CHROMIUM_TAG"
  echo "Chromium tag from CHROMIUM_TAG env: $TAG"
else
  TAG="$(sed -n 's/^CHROMIUM_TAG=//p' "$ROOT/config/chromium/BASELINE" | tr -d '[:space:]')"
fi
if [ -z "$TAG" ]; then
  echo "ERROR: no Chromium tag (set CHROMIUM_TAG or config/chromium/BASELINE)" >&2
  exit 2
fi
echo "Pinned Chromium tag: $TAG"

command -v git >/dev/null || { echo "ERROR: git is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 is required" >&2; exit 2; }

mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# -----------------------------------------------------------------------------
# 2. depot_tools at the pinned revision
# -----------------------------------------------------------------------------
if [ ! -d depot_tools ]; then
  echo "Cloning depot_tools..."
  git clone "$DEPOT_TOOLS_URL" depot_tools
fi
(
  cd depot_tools
  git fetch origin main --quiet
  git checkout --quiet "$DEPOT_TOOLS_PINNED_REV"
)
export PATH="$WORKSPACE/depot_tools:$PATH"

# Bootstrap the pinned depot_tools (CIPD-managed python + wrappers) WITHOUT
# updating it — ensure_bootstrap explicitly works on the current checkout.
# DEPOT_TOOLS_UPDATE=0 then keeps gclient from moving the pinned revision.
# (iNWEB: the first real fetch attempt failed with
# "python3_bin_reldir.txt not found" until this bootstrap was added.)
export DEPOT_TOOLS_UPDATE=0
export DEPOT_TOOLS_METRICS=0
( cd depot_tools && ./ensure_bootstrap )

# -----------------------------------------------------------------------------
# 3. Chromium checkout (no-history keeps the checkout small and tag-reproducible)
#
# Syncs DIRECTLY at the pinned tag: fetch's wrapper would first sync
# top-of-tree main and the script would then re-sync at the tag — double the
# network/disk for the same result, which matters on constrained runners.
# -----------------------------------------------------------------------------
if [ ! -f .gclient ]; then
  gclient config --name=src "https://chromium.googlesource.com/chromium/src.git"
fi

# Upstream instruction for Android: add android to target_os in .gclient
# ("the only difference between fetch android and fetch chromium").
if ! grep -q "'android'" .gclient; then
  printf "\ntarget_os = [ 'android' ]\n" >> .gclient
fi

# Official builds need the PGO profile downloads.  gclient reads custom_vars
# from the first solution dictionary, not from a top-level .gclient variable.
python3 - <<'PY'
import ast
from pathlib import Path

path = Path(".gclient")
text = path.read_text(encoding="utf-8")

def line_col_offset(source, node):
    lines = source.splitlines(keepends=True)
    return sum(len(line) for line in lines[: node.lineno - 1]) + node.col_offset

tree = ast.parse(text, filename=str(path))
solutions_assignment = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id == "solutions"
               for target in node.targets):
            if solutions_assignment is not None:
                raise SystemExit("ERROR: multiple solutions assignments in .gclient")
            solutions_assignment = node

if (solutions_assignment is None or
        not isinstance(solutions_assignment.value, ast.List) or
        not solutions_assignment.value.elts):
    raise SystemExit("ERROR: expected solutions list was not found in .gclient")
solution = solutions_assignment.value.elts[0]
if not isinstance(solution, ast.Dict):
    raise SystemExit("ERROR: expected solutions[0] dictionary was not found")

custom_vars = None
for name, value in zip(solution.keys, solution.values):
    if isinstance(name, ast.Constant) and name.value == "custom_vars":
        custom_vars = value
        break
if not isinstance(custom_vars, ast.Dict):
    raise SystemExit("ERROR: expected solutions[0]['custom_vars'] dictionary was not found")

profile = None
for name, value in zip(custom_vars.keys, custom_vars.values):
    if isinstance(name, ast.Constant) and name.value == "checkout_pgo_profiles":
        profile = value
        break
if profile is not None:
    if isinstance(profile, ast.Constant) and profile.value is True:
        pass
    else:
        start = line_col_offset(text, profile)
        end = (sum(len(line) for line in text.splitlines(keepends=True)
                   [: profile.end_lineno - 1]) + profile.end_col_offset)
        text = text[:start] + "True" + text[end:]
else:
    if text[line_col_offset(text, custom_vars):line_col_offset(text, custom_vars) + 1] != "{":
        raise SystemExit("ERROR: expected custom_vars = {} pattern was not found")
    start = line_col_offset(text, custom_vars) + 1
    separator = "" if not custom_vars.keys else " "
    text = text[:start] + (
        separator + "'checkout_pgo_profiles': True" +
        (", " if custom_vars.keys else "")
    ) + text[start:]

# Remove an obsolete top-level custom_vars assignment left by the old block.
tree = ast.parse(text, filename=str(path))
lines = text.splitlines(keepends=True)
removals = []
for node in tree.body:
    if (isinstance(node, ast.Assign) and
            any(isinstance(target, ast.Name) and target.id == "custom_vars"
                for target in node.targets)):
        removals.append(node)
for node in sorted(removals, key=lambda item: item.lineno, reverse=True):
    del lines[node.lineno - 1:node.end_lineno]
text = "".join(lines)
path.write_text(text, encoding="utf-8")

tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
solutions = None
for node in tree.body:
    if (isinstance(node, ast.Assign) and
            any(isinstance(target, ast.Name) and target.id == "solutions"
                for target in node.targets)):
        solutions = ast.literal_eval(node.value)
        break
assert solutions is not None
assert solutions[0]["custom_vars"]["checkout_pgo_profiles"] is True
PY

# -----------------------------------------------------------------------------
# 4. Sync dependencies at the pinned tag (-D deletes stale dependencies)
# -----------------------------------------------------------------------------
echo "Syncing at src@$TAG (this is the long step)..."
gclient sync --no-history --revision "src@$TAG" --with_branch_heads -D

# -----------------------------------------------------------------------------
# 5. Confirm the checked-out revision
# -----------------------------------------------------------------------------
ACTUAL="$(git -C src rev-parse HEAD)"
echo "Chromium source ready:"
echo "  path    : $WORKSPACE/src"
echo "  tag     : $TAG"
echo "  HEAD    : $ACTUAL"
echo "Next: scripts/build_android.sh"
