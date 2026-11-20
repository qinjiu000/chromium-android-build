#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ndk="${1:-${ANDROID_NDK_HOME:-}}"
chaquopy="${2:-}"
abi="${3:-}"

if [[ -z "$ndk" || -z "$chaquopy" || -z "$abi" ]]; then
    echo "Usage: $0 <NDK path> <Chaquopy unpacked ABI dir> <arm64-v8a|x86_64>" >&2
    exit 2
fi
case "$abi" in
    arm64-v8a)
        compiler="aarch64-linux-android29-clang"
        triple="aarch64"
        ;;
    x86_64)
        compiler="x86_64-linux-android29-clang"
        triple="x86_64"
        ;;
    *)
        echo "Unsupported ABI: $abi" >&2
        exit 2
        ;;
esac

toolchain="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
cc="$toolchain/$compiler"
if [[ ! -x "$cc" ]]; then
    echo "NDK compiler not found: $cc" >&2
    exit 1
fi
if [[ ! -d "$chaquopy/include/python3.13" || ! -d "$chaquopy/jniLibs/$abi" ]]; then
    echo "Invalid Chaquopy directory: $chaquopy" >&2
    exit 1
fi

out="$script_dir/out/$abi"
mkdir -p "$out"
extension="$out/pyhello.cpython-313-$triple-linux-android.so"
executable="$out/pyproto-$abi"
common=(-I"$chaquopy/include/python3.13" -L"$chaquopy/jniLibs/$abi")

"$cc" -shared -fPIC "${common[@]}" \
    "$script_dir/pyhello.c" -lpython3.13 -o "$extension"
"$cc" -fPIE -pie "${common[@]}" \
    "$script_dir/pyproto.c" -lpython3.13 -lm -ldl -llog -o "$executable"

printf 'Extension: %s\nExecutable: %s\n' "$extension" "$executable"
for artifact in "$extension" "$executable"; do
    if command -v file >/dev/null 2>&1; then
        file "$artifact"
    fi
    if command -v readelf >/dev/null 2>&1; then
        readelf -h "$artifact" | grep -E 'Class:|Machine:|Type:'
        readelf -d "$artifact" | grep NEEDED || true
    fi
done
