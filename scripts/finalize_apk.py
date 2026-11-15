#!/usr/bin/env python3
"""MANUAL remediation tool: align + re-sign + gate an APK, in place.

NOT part of the pipeline. The build-hop workflow only RUNS the gate
(verify_apk.py); a build that fails the gate fails the hop, and a human
decides what to do. This script is the fix path for the case where the
gate fails on alignment: it realigns the APK and re-signs it, then
re-runs the gate.

  1. Align with align_apk.py (stored entries 4-byte, lib/*.so 16 KiB).
     This drops the APK Signing Block: moving entry data invalidates
     the v2 signature, so the APK must be re-signed.
  2. Re-sign with apksigner from the checkout's Android SDK build-tools,
     using the tree test keystore (build/android/chromium-debug.keystore)
     — the SAME key the build used, so the certificate, and therefore
     update-compatibility with already-installed builds, is unchanged.
     v2 only, matching the build's own output (no v1/v3/v4).
     apksigner also verifies the result (official v2 verification,
     including the content digests).
  3. Run the verify_apk.py gate (alignment + pinned fingerprint).
  4. On PASS: replace the original APK and remove the stale .idsig
     (the build's v4 signature for the pre-align bytes).
     On FAIL: the original APK is left untouched and this exits 1.

Usage:
  finalize_apk.py --apk chromium/src/out/release/apks/ChromePublic.apk \
      --src chromium/src \
      --config config/chromium/apk-signing.json

Stdlib only (plus apksigner + java from the checkout). Exit 0 = the APK
in place is aligned, signed with the tree key, and gate-passed.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_apk  # noqa: E402
import verify_apk  # noqa: E402

# The Chromium tree's public test keystore. Its password is the well-known
# default published in the tree itself (build/config/android/config.gni:
# default_android_keystore_password). It is NOT a secret — the keystore
# ships in the public Chromium source — and it is the key every build of
# this repo already signs with. No new key is introduced here.
KEYSTORE_REL = os.path.join("build", "android", "chromium-debug.keystore")
KEYSTORE_ALIAS = "chromiumdebugkey"
KEYSTORE_PASSWORD = "chromium"


def find_apksigner(src_dir):
    """apksigner from the checkout's Android SDK build-tools (the same
    SDK the build itself used). Highest version wins."""
    pattern = os.path.join(
        src_dir,
        "third_party",
        "android_sdk",
        "public",
        "build-tools",
        "*",
        "apksigner",
    )
    matches = sorted(glob.glob(pattern))
    for path in reversed(matches):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def find_java(src_dir):
    """A java runtime for apksigner: PATH first, then the Chromium
    bundled JDK (third_party/jdk/current)."""
    java = shutil.which("java")
    if java:
        return java
    bundled = os.path.join(
        src_dir, "third_party", "jdk", "current", "bin", "java"
    )
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return None


def run(cmd, env=None):
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True
    )
    out = (result.stdout + result.stderr).strip()
    if out:
        for line in out.splitlines()[:20]:
            print(f"    {line}")
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed (exit {result.returncode}): {' '.join(cmd)}"
        )
    return result


def finalize(apk_path, src_dir, expected_fingerprint):
    apk_path = os.path.abspath(apk_path)
    src_dir = os.path.abspath(src_dir)
    keystore = os.path.join(src_dir, KEYSTORE_REL)
    if not os.path.isfile(apk_path):
        raise RuntimeError(f"APK not found: {apk_path}")
    if not os.path.isfile(keystore):
        raise RuntimeError(f"tree keystore not found: {keystore}")
    apksigner = find_apksigner(src_dir)
    if not apksigner:
        raise RuntimeError(
            "apksigner not found under "
            f"{src_dir}/third_party/android_sdk/public/build-tools/*/ "
            "(was the Android SDK synced?)"
        )
    java = find_java(src_dir)
    if not java:
        raise RuntimeError(
            "no java runtime found (not on PATH, no bundled JDK at "
            "third_party/jdk/current)"
        )
    print(f"FINALIZE: {apk_path}")
    print(f"  keystore: {keystore} (alias {KEYSTORE_ALIAS})")
    print(f"  apksigner: {apksigner}")
    print(f"  java: {java}")

    env = dict(os.environ)
    env["PATH"] = os.path.dirname(java) + os.pathsep + env.get("PATH", "")
    tmpdir = tempfile.mkdtemp(prefix="finalize-apk-")
    try:
        aligned = os.path.join(tmpdir, "aligned.apk")
        signed = os.path.join(tmpdir, "signed.apk")
        print("  [1/3] aligning (stored 4B, lib/*.so 16K) ...")
        align_apk.align_apk(apk_path, aligned)
        print("  [2/3] re-signing with the tree test key (v2 only) ...")
        run(
            [
                apksigner,
                "sign",
                "--ks",
                keystore,
                "--ks-key-alias",
                KEYSTORE_ALIAS,
                "--ks-pass",
                f"pass:{KEYSTORE_PASSWORD}",
                "--key-pass",
                f"pass:{KEYSTORE_PASSWORD}",
                "--v1-signing-enabled",
                "false",
                "--v2-signing-enabled",
                "true",
                "--v3-signing-enabled",
                "false",
                "--v4-signing-enabled",
                "false",
                "--out",
                signed,
                aligned,
            ],
            env=env,
        )
        print("  [2b/3] apksigner verify (official v2 check) ...")
        run([apksigner, "verify", signed], env=env)
        print("  [3/3] gate (alignment + pinned fingerprint) ...")
        if not verify_apk.run_gate(signed, expected_fingerprint):
            raise RuntimeError("gate FAILED — original APK left untouched")
        os.replace(signed, apk_path)
        idsig = apk_path + ".idsig"
        if os.path.exists(idsig):
            # The build's v4 signature covers the pre-align bytes; it no
            # longer matches anything. Remove it so no stale file lingers
            # next to the shipped APK.
            os.remove(idsig)
            print(f"  removed stale {idsig}")
        print(f"FINALIZE: PASS — {apk_path} is aligned, signed, gated")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, help="APK to finalize")
    parser.add_argument(
        "--src", required=True, help="Chromium src dir (keystore + SDK)"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="JSON config with expected_v2_cert_sha256",
    )
    args = parser.parse_args(argv)
    with open(args.config) as f:
        expected = json.load(f)["expected_v2_cert_sha256"]
    try:
        finalize(args.apk, args.src, expected)
    except RuntimeError as e:
        print(f"FINALIZE: FAIL — {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
