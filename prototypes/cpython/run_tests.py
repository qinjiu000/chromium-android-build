import sys

print("sys.path:", sys.path, flush=True)


def check(name, test):
    test()
    print(f"PASS {name}", flush=True)


def test_imports_and_version():
    import io
    import json
    import os
    import sys

    assert sys.version_info[:2] == (3, 13), sys.version


def test_zlib():
    import zlib

    payload = b"CPython Android prototype"
    assert zlib.decompress(zlib.compress(payload)) == payload


def test_sqlite3():
    import sqlite3

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.executemany("INSERT INTO sample VALUES (?)", [(1,), (2,), (3,)])
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 3
    finally:
        connection.close()


def test_threading():
    import threading

    result = []
    thread = threading.Thread(target=lambda: result.append(sum(range(100000))))
    thread.start()
    thread.join()
    assert result == [4999950000], result


def test_ssl():
    import ssl

    assert isinstance(ssl.OPENSSL_VERSION, str) and ssl.OPENSSL_VERSION


def test_socket():
    import socket

    sock = socket.socket()
    sock.close()
    assert sock.fileno() == -1


def test_pyhello():
    import pyhello

    assert pyhello.add(2, 3) == 5
    # CPython 3.13 reports sys.platform == "android" on Android (verified
    # on the emulator: the first version of this test wrongly expected
    # "linux"). Compare against sys.platform so the test is portable.
    assert pyhello.platform() == sys.platform, pyhello.platform()


check("imports-and-version", test_imports_and_version)
check("zlib", test_zlib)
check("sqlite3", test_sqlite3)
check("threading", test_threading)
check("ssl", test_ssl)
check("socket", test_socket)
check("pyhello", test_pyhello)
print("PROTOTYPE OK", flush=True)
