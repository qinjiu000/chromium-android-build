#!/usr/bin/env bash
# =============================================================================
# Android build (adapted from iNWEB-Browser scripts/build_android.sh)
#
# Runs on the GitHub-hosted runner, inside the build-hop workflow's time box.
# Generates the build directory with the committed GN arguments, applies the
# android_stealth.patch, and builds the upstream chrome_public_apk target
# with PLAIN NINJA.
#
# Plain ninja is not a preference — it is the load-bearing choice of the
# whole chain. autoninja dispatches to siso, whose incremental state
# (action/deps records) does NOT survive the cross-runner out/ transfer:
# iNWEB hops 4-7 each re-executed ~30k edges per box (~14 h wasted) before
# the chain switched to plain ninja at hop 9. Plain ninja rests on
# mtime/size restat, which the transferred state satisfies (tree mtimes
# normalized to a fixed epoch; out/ artifact mtimes preserved by tar).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Build variant, from the workflow (CAB_BUILD_TYPE env). development is the
# default so a bare local run behaves exactly as before this variable
# existed. release uses args-release.gn and out/release.
CAB_BUILD_TYPE="${CAB_BUILD_TYPE:-development}"
case "$CAB_BUILD_TYPE" in
  development|release) ;;
  *)
    echo "ERROR: unknown CAB_BUILD_TYPE '$CAB_BUILD_TYPE' (want development or release)" >&2
    exit 2
    ;;
esac

ARGS_FILE="$ROOT/config/chromium/args-${CAB_BUILD_TYPE}.gn"
if [ ! -f "$ARGS_FILE" ]; then
  echo "ERROR: missing GN args file: $ARGS_FILE" >&2
  exit 2
fi

PATCH_FILE="$ROOT/android_stealth.patch"
if [ ! -s "$PATCH_FILE" ]; then
  echo "ERROR: missing patch file: $PATCH_FILE" >&2
  exit 2
fi

WORKSPACE="${CAB_WORKSPACE:-$ROOT/chromium}"
if [ ! -d "$WORKSPACE/src" ]; then
  echo "ERROR: Chromium source not found at $WORKSPACE/src" >&2
  echo "       run scripts/fetch_chromium.sh first" >&2
  exit 2
fi

export PATH="$WORKSPACE/depot_tools:$PATH"
cd "$WORKSPACE/src"

# Apply the stealth patch. Every step is checked explicitly: a hop that
# cannot build must fail loudly instead of packaging state as if nothing
# had happened (iNWEB hop 31 reported "success" while gn gen had failed).
git apply --check "$PATCH_FILE" ||
  { echo "FATAL: android_stealth.patch does not apply cleanly to this tree" >&2; exit 3; }
git apply "$PATCH_FILE" ||
  { echo "FATAL: patch application failed" >&2; exit 3; }
git diff --check ||
  { echo "FATAL: patch left whitespace errors" >&2; exit 3; }
echo "Patch applied:"
git diff --stat | tail -3

OUT_DIR="out/${CAB_BUILD_TYPE}"

# mtime discipline, part 2 — the part that actually works (iNWEB hops 29/30).
#
# The hop workflow normalizes the tree to a fixed epoch BEFORE this script
# runs, then this script applies the patch, which rewrites every patched
# file with a CURRENT mtime. Those files are then newer than the resumed
# out/ artifacts, so ninja treats every patched input as changed and
# re-executes the graph instead of resuming. Measured at iNWEB: hop 29
# advanced 220 edges and hop 30 only 49, both re-running the C++ edges of
# the patched targets — the resumed state was buying almost nothing.
#
# Normalizing again here, with the patch already in place, is what makes
# the inputs permanently "not newer" than the artifacts. Keep it after the
# patch application above; moving it earlier silently disables it.
find "$WORKSPACE/src" -path "$WORKSPACE/src/$OUT_DIR" -prune -o \
  -exec touch -h -c -d '2020-01-01 00:00:00 UTC' {} +
gn gen "$OUT_DIR" --args="$(cat "$ARGS_FILE")" ||
  { echo "FATAL: gn gen failed (see the error above)" >&2; exit 4; }

# Record build metadata for reproducibility (stamp consumed by CI).
{
  echo "chromium_tag=${CHROMIUM_TAG:-$(sed -n 's/^CHROMIUM_TAG=//p' "$ROOT/config/chromium/BASELINE" | tr -d '[:space:]')}"
  echo "chromium_head=$(git rev-parse HEAD)"
  echo "channel=${CAB_BUILD_TYPE}"
  echo "gn_args_file=$ARGS_FILE"
  echo "built_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT_DIR/cab_build_metadata.txt"

# Chromium's android_static_analysis steps (Java validate_deps etc.)
# hard-require AUTONINJA_BUILD_ID even under plain ninja — without it they
# raise "AUTONINJA_BUILD_ID is not set ... requires autoninja integration"
# (hit at iNWEB hop 13, edge ~10390). This repo sets
# android_static_analysis="off" in both args files, which removes those
# steps; the ID is still exported as belt-and-braces. With the ID set but
# no AUTONINJA_STDOUT_NAME, server_utils.MaybeRunCommand deliberately falls
# back to normal LOCAL execution.
export AUTONINJA_BUILD_ID="${AUTONINJA_BUILD_ID:-cab-$$-$(date +%s)}"

CAB_BUILD_DRYRUN="${CAB_BUILD_DRYRUN:-false}"
if [ "$CAB_BUILD_DRYRUN" = "true" ] || [ "$CAB_BUILD_DRYRUN" = "1" ]; then
  ninja -C "$OUT_DIR" -n chrome_public_apk > "$OUT_DIR/ninja_dryrun.txt"
  echo "ninja dry-run: $(wc -l < "$OUT_DIR/ninja_dryrun.txt") commands would run"
  echo "--- first 20 planned commands ---"
  head -20 "$OUT_DIR/ninja_dryrun.txt"
  echo "--- why dirty (explain, first 40) ---"
  ninja -C "$OUT_DIR" -n -d explain chrome_public_apk 2>&1 | head -40
else
  ninja -C "$OUT_DIR" -j "${NINJA_JOBS:-6}" chrome_public_apk
fi

echo "Build complete."
echo "  output dir : $OUT_DIR"
echo "  metadata   : $OUT_DIR/cab_build_metadata.txt"
ls -lh "$OUT_DIR/apks/" || true
