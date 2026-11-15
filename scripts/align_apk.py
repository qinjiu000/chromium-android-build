#!/usr/bin/env python3
"""Realign an APK's stored entries.

  - Every stored (uncompressed) entry's data starts at a 4-byte boundary.
  - lib/*.so entries start at a 16 KiB boundary (Android 15+ 16K page size).

Compressed entries need no alignment and are recompressed as-is (same
content, same entry order, names, timestamps and extra fields preserved).

The APK Signing Block is dropped: realigning moves entry data, which
invalidates the v2 signature, so the output MUST be re-signed before
use (see finalize_apk.py — it re-signs with the same tree test key the
build used). The output is an unsigned, aligned APK.

Why not zipalign? The checkout's build-tools zipalign is the official
tool, but its .so page size depends on the build-tools version (4K vs
16K), and the pipeline must guarantee 16K regardless of version. This
aligner states the rule explicitly and is covered by --self-test.

Usage:
  align_apk.py INPUT.apk OUTPUT.apk
  align_apk.py --self-test

Stdlib only. Exit 0 on success, 1 on failure.
"""

import argparse
import os
import struct
import sys
import tempfile
import zipfile

DEFAULT_ALIGNMENT = 4
LIB_ALIGNMENT = 0x4000  # 16 KiB
# Extra-field header ID used for alignment padding (same value zipalign
# uses: 0xA11E). Parsers skip unknown extra-field blocks, so any ID with
# a well-formed (id, size, data) shape works; this one is recognizable.
ALIGN_EXTRA_ID = 0xA11E


def _required_alignment(name):
    if name.startswith("lib/") and name.endswith(".so"):
        return LIB_ALIGNMENT
    return DEFAULT_ALIGNMENT


def _padding_extra(padding_needed, alignment):
    """Extra-field bytes that grow the local header by exactly the
    amount needed to align the entry's data.

    The extra field is a sequence of (id u16, size u16, data) blocks,
    so a padding block needs at least 4 bytes of header. When the
    needed padding is 1-3 bytes, pad to the NEXT boundary instead
    (alignment + needed bytes) — same alignment, room for the header.
    """
    if padding_needed == 0:
        return b""
    total = padding_needed
    if total < 4:
        total += alignment
    return struct.pack("<HH", ALIGN_EXTRA_ID, total - 4) + b"\x00" * (total - 4)


def align_apk(src_path, dst_path):
    """Rewrite src_path to dst_path with aligned stored entries."""
    with zipfile.ZipFile(src_path, "r") as zin:
        infos = zin.infolist()
        comment = zin.comment
        # Read entry contents first (one entry at a time below would
        # interleave reads/writes on the same ZipFile; reading up front
        # keeps the logic simple — peak memory is the largest entry,
        # ~250 MB for libchrome.so, fine on a 16 GB runner).
        with open(dst_path, "wb", buffering=0) as raw:
            with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zout:
                zout.comment = comment
                for info in infos:
                    data = zin.read(info.filename)
                    new_info = zipfile.ZipInfo(
                        filename=info.filename, date_time=info.date_time
                    )
                    new_info.compress_type = info.compress_type
                    new_info.comment = info.comment
                    new_info.extra = info.extra
                    new_info.create_system = info.create_system
                    new_info.create_version = info.create_version
                    new_info.extract_version = info.extract_version
                    new_info.external_attr = info.external_attr
                    new_info.internal_attr = info.internal_attr
                    new_info.flag_bits = info.flag_bits & ~0x08  # no data descriptor
                    if (
                        info.compress_type == zipfile.ZIP_STORED
                        and not info.is_dir()
                        and len(data) > 0
                    ):
                        alignment = _required_alignment(info.filename)
                        # Local header size zipfile is about to write:
                        # 30 + filename bytes + extra bytes.
                        name_len = len(info.filename.encode("utf-8"))
                        header_start = raw.tell()
                        data_off = (
                            header_start
                            + 30
                            + name_len
                            + len(new_info.extra)
                        )
                        padding = (-data_off) % alignment
                        if padding:
                            new_info.extra = (
                                new_info.extra
                                + _padding_extra(padding, alignment)
                            )
                    zout.writestr(new_info, data)
    return dst_path


def self_test():
    """Align a synthetic misaligned APK and check the result with the
    gate's alignment checker; also verify entry contents survive."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import verify_apk

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.apk")
        dst = os.path.join(tmp, "out.apk")
        verify_apk.build_fixture_apk(
            src, cert_bytes=b"\x30\x82\x01\x00" + b"A" * 256
        )
        before, _, _ = verify_apk.check_alignment(src)
        if before:
            failures.append("fixture was already aligned (test is void)")
        align_apk(src, dst)
        after, fails, summary = verify_apk.check_alignment(dst)
        if not after:
            failures.append(f"aligned output failed alignment: {fails}")
        # Contents must survive byte-identical.
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst) as zout:
            if zin.namelist() != zout.namelist():
                failures.append("entry list changed")
            for name in zin.namelist():
                if zin.read(name) != zout.read(name):
                    failures.append(f"contents changed: {name}")
            if zin.comment != zout.comment:
                failures.append("archive comment changed")
        # Idempotence: aligning an aligned APK changes nothing except
        # possibly recompression bytes — alignment must still pass.
        dst2 = os.path.join(tmp, "out2.apk")
        align_apk(dst, dst2)
        after2, fails2, _ = verify_apk.check_alignment(dst2)
        if not after2:
            failures.append(f"second align failed alignment: {fails2}")
    if failures:
        print("SELF-TEST: FAIL")
        for f in failures:
            print(f"  - {f}")
        return False
    print("SELF-TEST: PASS")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", nargs="?", help="input APK")
    parser.add_argument("dst", nargs="?", help="output APK (aligned)")
    parser.add_argument(
        "--self-test", action="store_true", help="run synthetic self-test"
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return 0 if self_test() else 1
    if not args.src or not args.dst:
        parser.error("SRC and DST are required (or use --self-test)")
    align_apk(args.src, args.dst)
    print(f"ALIGNED: {args.src} -> {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
