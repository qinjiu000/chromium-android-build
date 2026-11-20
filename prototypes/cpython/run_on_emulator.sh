#!/usr/bin/env bash
# Push the built prototype artifacts to a booted emulator and run them.
# Called as a SINGLE line from the CI workflow because
# android-emulator-runner executes its `script:` input line-by-line, each
# line in a fresh `sh -c` (shell variables do NOT survive across lines).
# Usage: run_on_emulator.sh <arm64-v8a|x86_64>
set -euo pipefail

abi="${1:?Usage: $0 <arm64-v8a|x86_64>}"
case "$abi" in
    arm64-v8a) triple="aarch64" ;;
    x86_64) triple="x86_64" ;;
    *) echo "Unsupported ABI: $abi" >&2; exit 2 ;;
esac

remote=/data/local/tmp/pyproto
chaquopy="prototypes/cpython/unpacked/$abi"

adb shell mkdir -p "$remote/lib-dynload"
adb push "prototypes/cpython/out/$abi/pyproto-$abi" "$remote/"
adb push "prototypes/cpython/out/$abi/pyhello.cpython-313-$triple-linux-android.so" "$remote/"
adb push "$chaquopy/jniLibs/$abi/"*.so "$remote/"
adb push "$chaquopy/lib-dynload/$abi" "$remote/lib-dynload/"
adb push "$chaquopy/stdlib" "$remote/"
adb push prototypes/cpython/run_tests.py "$remote/"
adb shell chmod 755 "$remote/pyproto-$abi"

output="$(adb shell "LD_LIBRARY_PATH=$remote $remote/pyproto-$abi $remote $abi" 2>&1)" || {
    printf '%s\n' "$output"
    exit 1
}
printf '%s\n' "$output"
grep -Fq 'PROTOTYPE OK' <<<"$output"
