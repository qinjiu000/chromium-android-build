#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
downloads="$script_dir/downloads"
unpacked="$script_dir/unpacked"
base_url="https://repo.maven.apache.org/maven2/com/chaquo/python/target/3.13.16-0"
mkdir -p "$downloads" "$unpacked"

fetch_and_verify() {
    local artifact="$1"
    local expected="$2"
    local archive="$downloads/$artifact"
    local checksum="$downloads/$artifact.sha1"
    local actual

    if [[ ! -f "$checksum" ]]; then
        curl -fL "$base_url/$artifact.sha1" -o "$checksum"
    fi
    actual="$(awk '{print $1}' "$checksum")"
    if [[ "$actual" != "$expected" ]]; then
        echo "Unexpected published SHA1 for $artifact: $actual" >&2
        exit 1
    fi

    if [[ -f "$archive" ]]; then
        actual="$(sha1sum "$archive" | awk '{print $1}')"
    else
        actual=""
    fi
    if [[ "$actual" != "$expected" ]]; then
        curl -fL "$base_url/$artifact" -o "$archive.tmp"
        actual="$(sha1sum "$archive.tmp" | awk '{print $1}')"
        if [[ "$actual" != "$expected" ]]; then
            rm -f "$archive.tmp"
            echo "SHA1 mismatch for $artifact: $actual" >&2
            exit 1
        fi
        mv "$archive.tmp" "$archive"
    fi
    echo "Verified $artifact ($expected)"
}

arm64="target-3.13.16-0-arm64-v8a.zip"
x86_64="target-3.13.16-0-x86_64.zip"
stdlib="target-3.13.16-0-stdlib.zip"
fetch_and_verify "$arm64" "31c2a9a1e45b3f3de8b35e8aeda8c32d3669dc23"
fetch_and_verify "$x86_64" "f8989dcf87bac8bf2bed746911366178cfe30c2e"
fetch_and_verify "$stdlib" "629ce5bffa9131cb3576c15d92990803009dd305"

for abi in arm64-v8a x86_64; do
    mkdir -p "$unpacked/$abi/stdlib"
    case "$abi" in
        arm64-v8a) archive="$downloads/$arm64" ;;
        x86_64) archive="$downloads/$x86_64" ;;
    esac
    unzip -oq "$archive" -d "$unpacked/$abi"
    unzip -oq "$downloads/$stdlib" -d "$unpacked/$abi/stdlib"
    printf 'Prepared %s: %s\n' "$abi" "$unpacked/$abi"
done
printf 'Downloads: %s\n' "$downloads"
