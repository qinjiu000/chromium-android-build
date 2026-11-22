#!/usr/bin/env bash
# =============================================================================
# Fetch Chaquopy CPython 3.13.16 prebuilt into a Chromium source tree.
#
# Usage: fetch_python.sh <chromium-src-dir> [abi]
#   abi: arm64-v8a (default, production) or x86_64 (CI verification only)
#
# Downloads from Maven Central (SHA1-verified against pinned constants):
#   target-3.13.16-0-<abi>.zip  (interpreter .so files + extension modules
#                                + headers) and target-3.13.16-0-stdlib.zip.
#
# Layout produced (zip-internal layout preserved):
#   <src>/chrome/android/cpython/prebuilt/<abi>/
#     jniLibs/<abi>/*.so        libpython3.13 + ssl/crypto/sqlite companions
#     lib-dynload/<abi>/*.so    extension modules
#     include/python3.13/**     headers (for building the JNI bridge)
#     stdlib.zip                stdlib, packaged as an APK asset
#     .downloads/               cached zips (SHA1-verified; survives hop
#                               tree transfers, so later hops skip download)
#     .fetch-ok                 marker: fetch+unpack completed for this version
#
# Idempotent: re-running with the marker present and the SHA1s matching
# does nothing. Must run BEFORE `gn gen`: the GN targets reference the
# unpacked files (android_assets sources are existence-checked at gen time).
# =============================================================================
set -euo pipefail

SRC_DIR="${1:?Usage: $0 <chromium-src-dir> [abi]}"
ABI="${2:-arm64-v8a}"
case "$ABI" in
  arm64-v8a|x86_64) ;;
  *)
    echo "ERROR: unsupported ABI '$ABI' (want arm64-v8a or x86_64)" >&2
    exit 2
    ;;
esac
if [ ! -d "$SRC_DIR/chrome/android" ]; then
  echo "ERROR: not a Chromium source dir: $SRC_DIR" >&2
  exit 2
fi

VERSION="3.13.16-0"
BASE_URL="https://repo.maven.apache.org/maven2/com/chaquo/python/target/$VERSION"
PREBUILT="$SRC_DIR/chrome/android/cpython/prebuilt/$ABI"
DOWNLOADS="$PREBUILT/.downloads"
MARKER="$PREBUILT/.fetch-ok"

# Pinned SHA1s (verified 2026-11-21 against Maven Central, see
# prototypes/cpython/README.md §1 on scratch/cpython-prototype).
case "$ABI" in
  arm64-v8a) ABI_SHA1="31c2a9a1e45b3f3de8b35e8aeda8c32d3669dc23" ;;
  x86_64)    ABI_SHA1="f8989dcf87bac8bf2bed746911366178cfe30c2e" ;;
esac
STDLIB_SHA1="629ce5bffa9131cb3576c15d92990803009dd305"

ABI_ZIP="target-$VERSION-$ABI.zip"
STDLIB_ZIP="target-$VERSION-stdlib.zip"

if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "chaquopy-target-$VERSION-$ABI" ]; then
  if [ -f "$PREBUILT/jniLibs/$ABI/libpython3.13.so" ] && \
     [ -f "$PREBUILT/stdlib.zip" ]; then
    echo "CPython $VERSION ($ABI) already fetched at $PREBUILT"
    exit 0
  fi
  echo "Marker present but files missing; re-fetching." >&2
fi

mkdir -p "$DOWNLOADS" "$PREBUILT"

fetch_and_verify() {
  local artifact="$1"
  local expected="$2"
  local archive="$DOWNLOADS/$artifact"
  local checksum="$DOWNLOADS/$artifact.sha1"
  local actual

  if [ ! -f "$checksum" ]; then
    curl --retry 5 --retry-all-errors -fL "$BASE_URL/$artifact.sha1" -o "$checksum"
  fi
  actual="$(awk '{print $1}' "$checksum")"
  if [ "$actual" != "$expected" ]; then
    echo "ERROR: unexpected published SHA1 for $artifact: $actual" >&2
    exit 1
  fi

  if [ -f "$archive" ]; then
    actual="$(sha1sum "$archive" | awk '{print $1}')"
  else
    actual=""
  fi
  if [ "$actual" != "$expected" ]; then
    curl --retry 5 --retry-all-errors -fL "$BASE_URL/$artifact" -o "$archive.tmp"
    actual="$(sha1sum "$archive.tmp" | awk '{print $1}')"
    if [ "$actual" != "$expected" ]; then
      rm -f "$archive.tmp"
      echo "ERROR: SHA1 mismatch for $artifact: $actual" >&2
      exit 1
    fi
    mv "$archive.tmp" "$archive"
  fi
  echo "Verified $artifact ($expected)"
}

fetch_and_verify "$ABI_ZIP" "$ABI_SHA1"
fetch_and_verify "$STDLIB_ZIP" "$STDLIB_SHA1"

unzip -oq "$DOWNLOADS/$ABI_ZIP" -d "$PREBUILT"
cp "$DOWNLOADS/$STDLIB_ZIP" "$PREBUILT/stdlib.zip"

if [ ! -f "$PREBUILT/jniLibs/$ABI/libpython3.13.so" ]; then
  echo "ERROR: unpacked tree lacks jniLibs/$ABI/libpython3.13.so" >&2
  exit 1
fi
echo "chaquopy-target-$VERSION-$ABI" > "$MARKER"
echo "CPython $VERSION ($ABI) ready at $PREBUILT"
