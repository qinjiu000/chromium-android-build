#!/usr/bin/env python3
"""APK delivery gate: alignment + signing-certificate fingerprint.

Run on every APK before it is uploaded as a build artifact. All checks
must pass; any failure exits 1 and the hop fails loudly instead of
shipping a bad package.

Checks:
  1. resources.arsc (if present and stored) starts at a 4-byte boundary.
  2. Every lib/*.so (stored) starts at a 16 KiB boundary.
  3. Every other stored entry starts at a 4-byte boundary.
     (Compressed entries need no alignment; directories and zero-size
     entries are skipped.)
  4. The APK Signature Scheme v2 certificate SHA-256 matches the pinned
     fingerprint (config/chromium/apk-signing.json). A different key
     means the build signed with something unexpected — never ship it.

Usage:
  verify_apk.py --apk PATH --expected-fingerprint HEX
  verify_apk.py --apk PATH --config config/chromium/apk-signing.json
  verify_apk.py --self-test        # synthetic fixtures, no real APK needed

Stdlib only. Exit 0 = PASS, 1 = FAIL.
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
import zipfile

APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_BLOCK_ID = 0x7109871A
DEFAULT_ALIGNMENT = 4
LIB_ALIGNMENT = 0x4000  # 16 KiB


def _read_u16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def _read_u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _read_u64(buf, off):
    return struct.unpack_from("<Q", buf, off)[0]


def entry_data_offset(apk_path, info):
    """Exact data offset of a zip entry, parsed from its local header."""
    with open(apk_path, "rb") as f:
        f.seek(info.header_offset)
        header = f.read(30)
    if len(header) < 30 or header[:4] != b"PK\x03\x04":
        raise ValueError(
            f"{info.filename}: bad local header at {info.header_offset}"
        )
    name_len = _read_u16(header, 26)
    extra_len = _read_u16(header, 28)
    return info.header_offset + 30 + name_len + extra_len


def check_alignment(apk_path):
    """Return (ok, failures, summary).

    failures: list of (entry, offset, required_alignment).
    summary: dict with entry counts.
    """
    failures = []
    n_stored = 0
    n_checked = 0
    with zipfile.ZipFile(apk_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size == 0:
                continue
            if info.compress_type != zipfile.ZIP_STORED:
                continue
            n_stored += 1
            if info.filename == "resources.arsc":
                required = DEFAULT_ALIGNMENT
            elif (
                info.filename.startswith("lib/")
                and info.filename.endswith(".so")
            ):
                required = LIB_ALIGNMENT
            else:
                required = DEFAULT_ALIGNMENT
            offset = entry_data_offset(apk_path, info)
            n_checked += 1
            if offset % required != 0:
                failures.append((info.filename, offset, required))
    summary = {"stored_entries": n_stored, "checked": n_checked}
    return (not failures), failures, summary


def _length_prefixed(buf, off):
    """Read a uint32 length-prefixed slice. Returns (slice, next_offset)."""
    length = _read_u32(buf, off)
    return buf[off + 4 : off + 4 + length], off + 4 + length


def v2_cert_sha256(apk_path):
    """SHA-256 (hex) of the first certificate in the APK v2 signature
    block, or None if the APK has no v2 block.

    Layout (all integers little-endian):
      EOCD -> central-directory offset -> signing-block footer
      (size u64 + magic) -> ID/value pairs -> v2 value:
      length-prefixed sequence of signers, each signer =
      length-prefixed signed_data + signatures + public_key;
      signed_data = length-prefixed digests + length-prefixed
      certificates + min/max SDK; certificates = length-prefixed
      list of length-prefixed X.509 certs.
    """
    with open(apk_path, "rb") as f:
        data = f.read()
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if eocd < 0:
        raise ValueError("no end-of-central-directory record found")
    cd_offset = _read_u32(data, eocd + 16)
    footer_off = cd_offset - 24
    if footer_off < 0:
        return None
    block_size = _read_u64(data, footer_off)
    magic = data[footer_off + 8 : footer_off + 24]
    if magic != APK_SIG_BLOCK_MAGIC:
        return None  # no APK Signing Block at all
    block_start = cd_offset - (block_size + 8)
    pos = block_start + 8
    end = footer_off
    while pos < end:
        pair_len = _read_u64(data, pos)
        pair_id = _read_u32(data, pos + 8)
        if pair_id == V2_BLOCK_ID:
            v2 = data[pos + 12 : pos + 8 + pair_len]
            signers, _ = _length_prefixed(v2, 0)
            signer, _ = _length_prefixed(signers, 0)
            signed_data, _ = _length_prefixed(signer, 0)
            _, off = _length_prefixed(signed_data, 0)  # digests
            certs, _ = _length_prefixed(signed_data, off)
            cert, _ = _length_prefixed(certs, 0)
            return hashlib.sha256(cert).hexdigest()
        pos += 8 + pair_len
    return None


def run_gate(apk_path, expected_fingerprint):
    """Run all checks. Prints results. Returns True on PASS."""
    ok = True
    print(f"GATE: {apk_path}")
    aligned, failures, summary = check_alignment(apk_path)
    print(
        f"  alignment: {summary['checked']} stored entries checked, "
        f"{len(failures)} misaligned"
    )
    if not aligned:
        ok = False
        for name, offset, required in failures[:10]:
            print(
                f"    FAIL {name}: data offset {offset} "
                f"not {required}-byte aligned (mod {required} = "
                f"{offset % required})"
            )
        if len(failures) > 10:
            print(f"    ... and {len(failures) - 10} more")
    else:
        print("  alignment: PASS (arsc 4B, lib/*.so 16K, others 4B)")
    fingerprint = v2_cert_sha256(apk_path)
    if fingerprint is None:
        print("  fingerprint: FAIL — no APK Signature Scheme v2 block")
        ok = False
    elif fingerprint.lower() != expected_fingerprint.lower():
        print(
            f"  fingerprint: FAIL — v2 cert {fingerprint} != "
            f"expected {expected_fingerprint}"
        )
        ok = False
    else:
        print(f"  fingerprint: PASS ({fingerprint[:16]}...)")
    print(f"GATE: {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------------------
# Self-test fixtures (synthetic APKs; no real APK or keystore needed).
# ---------------------------------------------------------------------------

def _lp(payload):
    return struct.pack("<I", len(payload)) + payload


def _attach_v2_block(path, cert_bytes):
    """Insert a synthetic v2 signing block (carrying cert_bytes as the
    sole certificate) before the central directory. The block is
    structurally valid but the signature is empty — the gate only
    checks the certificate, not the signature, so this is enough to
    exercise the parser."""
    with open(path, "rb") as f:
        data = f.read()
    eocd = data.rfind(b"PK\x05\x06")
    cd_offset = _read_u32(data, eocd + 16)
    signed_data = _lp(b"") + _lp(_lp(cert_bytes)) + struct.pack("<II", 0, 0)
    signer = _lp(signed_data) + _lp(b"") + _lp(b"")
    v2_value = _lp(_lp(signer))
    pair = struct.pack("<Q", len(v2_value) + 4) + struct.pack(
        "<I", V2_BLOCK_ID
    ) + v2_value
    block_size = len(pair) + 24
    block = (
        struct.pack("<Q", block_size)
        + pair
        + struct.pack("<Q", block_size)
        + APK_SIG_BLOCK_MAGIC
    )
    new_cd_offset = cd_offset + len(block)
    new_data = (
        data[:cd_offset]
        + block
        + data[cd_offset:eocd]
        + data[eocd : eocd + 16]
        + struct.pack("<I", new_cd_offset)
        + data[eocd + 20 :]
    )
    with open(path, "wb") as f:
        f.write(new_data)


def build_fixture_apk(path, *, cert_bytes, with_v2=True):
    """Write a minimal APK-like zip with a resources.arsc and a lib .so
    (both deliberately misaligned — zipfile adds no padding), plus an
    optional synthetic v2 signing block carrying cert_bytes."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        # A 33-byte entry first, so resources.arsc lands at data offset
        # 77 (mod 4 = 1) instead of 44 (mod 4 = 0) — both fixture entries
        # must be misaligned, not just the .so.
        zf.writestr("zz", b"Z")
        zf.writestr("resources.arsc", b"ARSC" * 64)
        zf.writestr("lib/arm64-v8a/libtest.so", b"SO" * 512)
        zf.writestr(
            "AndroidManifest.xml", b"MANIFEST", zipfile.ZIP_DEFLATED
        )
    if with_v2:
        _attach_v2_block(path, cert_bytes)


def _align_fixture(path):
    """Realign a fixture APK in place (same padding rules as align_apk)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import align_apk

    tmp = path + ".aligned"
    align_apk.align_apk(path, tmp)
    os.replace(tmp, path)


def self_test():
    """Exercise the gate on synthetic fixtures. Returns True on success."""
    failures = []
    cert_a = b"\x30\x82\x01\x00" + b"A" * 256  # fake DER-ish blob
    cert_b = b"\x30\x82\x01\x00" + b"B" * 256
    fp_a = hashlib.sha256(cert_a).hexdigest()
    fp_b = hashlib.sha256(cert_b).hexdigest()

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Misaligned fixture must FAIL alignment, PASS fingerprint.
        p1 = os.path.join(tmp, "misaligned.apk")
        build_fixture_apk(p1, cert_bytes=cert_a)
        aligned1, fails1, _ = check_alignment(p1)
        fp1 = v2_cert_sha256(p1)
        if aligned1:
            failures.append("misaligned fixture passed alignment")
        if not fails1:
            failures.append("misaligned fixture reported no failures")
        if fp1 != fp_a:
            failures.append(f"misaligned fixture fingerprint {fp1} != {fp_a}")

        # 2. Aligned fixture (via the real aligner) must PASS alignment;
        #    the aligner must also have dropped the v2 block (the APK is
        #    unsigned until finalize re-signs it).
        p2 = os.path.join(tmp, "aligned.apk")
        build_fixture_apk(p2, cert_bytes=cert_a)
        _align_fixture(p2)
        aligned2, fails2, _ = check_alignment(p2)
        fp2 = v2_cert_sha256(p2)
        if not aligned2:
            failures.append(f"aligned fixture failed alignment: {fails2}")
        if fp2 is not None:
            failures.append("aligned fixture kept a v2 block (aligner must drop it)")

        # 3. Aligned + re-signed fixture (v2 block re-attached after
        #    aligning, the way a signer would) must PASS the whole gate.
        p3 = os.path.join(tmp, "signed.apk")
        build_fixture_apk(p3, cert_bytes=cert_a)
        _align_fixture(p3)
        _attach_v2_block(p3, cert_a)
        if not run_gate(p3, fp_a):
            failures.append("aligned+signed fixture failed the gate")

        # 4. Wrong fingerprint must FAIL the gate.
        if run_gate(p3, fp_b):
            failures.append("wrong-fingerprint fixture passed the gate")

        # 5. No v2 block must FAIL the gate.
        p5 = os.path.join(tmp, "unsigned.apk")
        build_fixture_apk(p5, cert_bytes=cert_a, with_v2=False)
        _align_fixture(p5)
        if run_gate(p5, fp_a):
            failures.append("unsigned fixture passed the gate")

    if failures:
        print("SELF-TEST: FAIL")
        for f in failures:
            print(f"  - {f}")
        return False
    print("SELF-TEST: PASS (5 scenarios)")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", help="APK to check")
    parser.add_argument(
        "--expected-fingerprint", help="expected v2 cert SHA-256 (hex)"
    )
    parser.add_argument(
        "--config",
        help="JSON config with expected_v2_cert_sha256 (alternative to "
        "--expected-fingerprint)",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run synthetic self-test"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return 0 if self_test() else 1
    if not args.apk:
        parser.error("--apk is required (or use --self-test)")
    expected = args.expected_fingerprint
    if args.config:
        with open(args.config) as f:
            expected = json.load(f)["expected_v2_cert_sha256"]
    if not expected:
        parser.error(
            "expected fingerprint required (--expected-fingerprint or --config)"
        )
    return 0 if run_gate(args.apk, expected) else 1


if __name__ == "__main__":
    sys.exit(main())
