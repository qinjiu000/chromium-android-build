# 三期 CPython：版本冻结与最小原型

> 分支：`scratch/cpython-prototype`（从 `fingerprint-python` 分出，实验分支，
> 不要合回 `fingerprint-python`，更不要碰 `degoogle-selective` 与 main）。
> 设计依据：`docs/fingerprint-python-design.md` §3（原型先行：先跑通最小验证，
> 再写 GN 集成）。

## 1. 版本冻结（2026-11-21，亲自核实）

**冻结：CPython 3.13.16，取 Chaquopy 预编译包 `com.chaquo.python:target:3.13.16-0`，
ABI：arm64-v8a（生产）＋ x86_64（仅模拟器 CI 验证）。**

选它的理由（逐项核实，不是听说的）：

1. **预编译包可用性（已验证下载）**：Maven Central 上 `target:3.13.16-0` 真实存在，
   含 `arm64-v8a`、`x86_64`、`stdlib`、`stdlib-pyc` 四个 classifier，
   2026-11-21 已下载其中三个并校验 SHA1 全部通过
   （arm64 `31c2a9a1…`、x86_64 `f8989dcf…`、stdlib `629ce5bf…`）。
   包内齐全：`libpython3.13.so`（5.4MB）、OpenSSL/SQLite 配套库、
   57 个 `lib-dynload` 扩展模块、完整 `include/python3.13/`（含 `pyconfig.h`，
   可直接编译第三方扩展）、529 个 stdlib `.py` 文件。
2. **ABI 覆盖**：本仓库 Chromium 构建是 arm64 唯一目标
   （`config/chromium/args-*.gn`：`target_cpu = "arm64"`），
   Python 3.12+ 只提供 64 位 ABI 正好对得上；x86_64 包用于 CI 模拟器快速验证。
   32 位 ABI（armeabi-v7a）不在构建目标内，不用为它退回 3.11。
3. **维护状态**：Chaquopy 当前 17.2.0（2026 年 10 月仍在发版，CA 包更新到
   2026.10.23），Python 3.13 线已到第 16 个补丁版；3.13 起用 CPython 官方
   Android 构建脚本；3.14/3.15 的 Android wheel 生态还不如 3.13 齐全，
   3.15.0 刚发布，太新；3.12 无任何优于 3.13 的地方。
4. **16KB 页面**：已用 readelf 核实 `libpython3.13.so` 的 LOAD 段对齐为
   `0x4000`，16KB 页面设备可加载（Chaquopy 官方亦建议 3.13+）。
5. **依赖干净（已用 readelf 核实）**：`libpython3.13.so` 的 NEEDED 只有
   `libm/libdl/liblog/libc`，无 `libc++_shared.so`——与 Chromium 自带的
   libc++ 无运行时冲突；`_sqlite3` 依赖包内 `libsqlite3_python.so`，
   `_ssl` 依赖包内 `libssl_python.so` + `libcrypto_python.so`，
   `zlib` 依赖系统 `libz.so`；`.note.android.ident` 标明 min API 21，
   由 NDK r27d 构建——与我方 minSdk 29 兼容（21 ≤ 29）。
6. **Chromium 侧 NDK**：159 树 DEPS 锁定 NDK `30.0.16248370`（r30），
   `android_ndk_api_level = default_min_sdk_version = 29`（arm64）。
   我方自编译的扩展模块用 NDK r30 + API 29 构建；与 Chaquopy 包（r27d/API 21）
   混用无问题（同为 bionic，符号解析不依赖 NDK 版本）。

每 ABI 依赖清单（arm64-v8a，x86_64 同构）：

| 产物 | 依赖 |
|---|---|
| `libpython3.13.so` | `libm.so` `libdl.so` `liblog.so` `libc.so`（系统） |
| `libssl_python.so` / `libcrypto_python.so` | 系统 libc/libm/libdl（OpenSSL 3.x，包内自带） |
| `libsqlite3_python.so` | 系统 libc/libm/libdl（包内自带） |
| `_sqlite3…so` | `libsqlite3_python.so` + `libpython3.13.so` |
| `_ssl…so` / `_hashlib…so` | `libssl_python.so` + `libcrypto_python.so` + `libpython3.13.so` |
| `zlib…so` | 系统 `libz.so` + `libpython3.13.so` |
| 其余 50+ 个 `lib-dynload` 模块 | `libpython3.13.so` + 系统库 |

## 2. 原型目标

证明一件事：**扩展模块能在 Android arm64 环境里加载起来**，
且解释器核心（`_io`/导入机制/线程/sqlite/zlib）可工作。
设计文档 §3.1 要求验证四类场景：标准库、纯 Python 第三方包、
原生扩展、文件导入——原型覆盖前三类加文件导入（测试脚本本身即文件导入）。

原型形态（不建 APK、不走 Gradle）：

- 一个最小 C 嵌入程序 `pyproto`（链接 `libpython3.13.so`），
  启动解释器并执行测试脚本，全部检查通过打印 `PROTOTYPE OK`，任一失败非零退出。
- 一个最小自编译 C 扩展 `pyhello`（NDK r30 + API 29 编译），证明“我方工具链
  编出的扩展模块能被该解释器加载”，而不只是 Chaquopy 自带的模块能加载。
- 运行环境：CI 模拟器上 `adb push` 到 `/data/local/tmp` 后直接运行
  （不需要 APK、不需要 Java 层，这是能想到的最小运行载体）。
  本地 VM 无模拟器、无 qemu，本地只能做到交叉编译＋readelf 静态核查，
  运行证明必须走 CI（GitHub runner 有 KVM，见 AGENTS.md）。

## 3. 本地已验证（2026-11-21）

- [x] Java 21 存在（`/usr/bin/java`，OpenJDK 21.0.13.1）——任务简报称“无 Java”
      是旧信息，已纠正；本地 Java 层构建不受限。
- [x] 本地无 NDK、无模拟器、无 qemu；NDK r30（`android-ndk-r30-linux.zip`，
      738,633,529 字节）可从 dl.google.com 下载，已在下载中。
- [x] Chaquopy 三个包 SHA1 全通过；包内容、ELF 架构、NEEDED、min API、
      16KB 对齐均已 readelf 核实（见 §1）。
- [x] `pyconfig.h` 含 `ANDROID_API_LEVEL 21` 与完整 pthread 支持，
      可直接作扩展编译头文件。

## 4. 文件清单（本目录）

| 文件 | 说明 |
|---|---|
| `pyhello.c` | 最小 C 扩展（`add`、`platform` 两个函数） |
| `pyproto.c` | 嵌入测试程序 |
| `run_tests.py` | Python 侧测试脚本（文件导入场景） |
| `build.sh` | NDK r30 交叉编译脚本（arm64-v8a + x86_64，API 29） |
| `fetch_python.sh` | 拉 Chaquopy 包并校验 SHA1 |
| `../../.github/workflows/python-prototype.yml` | CI 验证 workflow（手动触发，两路：x86_64 快路＋arm64 实证） |

## 5. 结论（待 CI 跑完填写）

- 本地交叉编译：待 NDK 下载完成后验证。
- x86_64 模拟器运行：待 CI。
- arm64 模拟器运行：待 CI（字面意义上的 arm64 实证；x86_64  runner 上跑 ARM
  模拟器慢，job 超时按仓库惯例放宽）。
