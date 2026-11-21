# 指纹浏览器（二期）与原生 CPython（三期）设计文档

> 分支：`fingerprint-python`（从 `degoogle-selective` @ 0667e56 分出）
> 目标 Chromium：159.0.8149.1，Android，`chrome_public_apk`
> 状态：核心结论已对 159.0.8149.0 树逐项核实完毕（2026-11-21，证据见 §5），待 Copilot 评审。
> 用户拍板（2026-11-21）：这两项最重要，不等去谷歌版测完，立即动手。

## 0. 背景与已定方向

一期（去谷歌，分支 `degoogle-selective`）已通过 verify 第 7 轮，build-hop 编译链正在该分支上跑。
本分支绝不碰 `degoogle-selective`、不动正在跑的链、不推 main。

用户定好的方向（2026-11-21）：

- **指纹浏览器**：每环境独立浏览器档案（Cookie/缓存/登录态完全隔离）
  ＋每环境固定种子指纹（Canvas/音频/WebGL/时区/语言/分辨率，生成后不变）
  ＋每环境独立代理（WARP/usque-app 出 SOCKS5，浏览器只管指过去）
  ＋环境管理界面（新建/改名/换指纹/换代理/删）。
- **CPython**：两种都要——浏览器自动化桥接（调标签页、导航、执行 JS）
  ＋独立控制台。

## 1. 总体架构

```
启动器（Java，新增/改动 chrome/android）
  │ 读 SharedPreferences 里的当前环境 env_id
  │ 追加命令行：--user-data-dir=<base>/<env_id>  --stealth-seed=<hash(env_id)>
  ▼
Chromium（单 Profile 模型，见 §2.1）
  │ 每个环境 = 一个独立数据目录 = 一套独立 Cookie/存储/登录态
  │ 指纹噪声种子由 --stealth-seed 决定，同一环境永远同一套指纹
  ▼
每环境代理（PrefService 的 per-profile 代理配置）
  │ 默认预设：usque-app 的 SOCKS5（端口待核实）
  ▼
CPython（见 §3）：预编译 libpython + stdlib 嵌进 APK，JNI 桥接
```

关键取舍：**不做桌面端的多 Profile 切换**。安卓 Chromium 历史上是单档案模型；
环境切换 = 换数据目录 + 重启进程。这是最少改动、最可验证的路线。

## 2. 二期：指纹浏览器

### 2.1 环境模型（已核实，2026-11-21）

对 159.0.8149.0 树亲自核实（证据见 §5）：

- **安卓默认单 Profile**：`ProfileManager::CreateInitialProfile` 只加载
  `DIR_USER_DATA/Default`（`chrome/browser/profiles/profile_manager.cc:772-782`，
  `kInitialProfile = "Default"`）；`GetProfileBaseNameForStartup` 注释明确
  “Only the flag may start a non-Default profile”（`chrome/browser/startup_data.cc:180-189`）。
  桌面端多 Profile 管理 API 大量被 `#if !BUILDFLAG(IS_ANDROID)` 守卫，
  Java 侧 `ProfileManager.java` 只有读接口，无创建/切换 API。**桌面多 Profile 路不走。**
- **树内有备用方案（不用）**：`kAndroidMultiprofile` 与
  `kAndroidMultiprofileStartupData` 两个 flag 默认关闭（`chrome_feature_list.cc:747`、
  `chrome_features.cc:86`），开启后可走 StartupData 指定 profile；本方案不需要它，
  记为备选。
- **机制：启动前注入 `--user-data-dir`**。`InitializeUserDataDir`
  （`chrome/app/chrome_main_delegate.cc:654`）无 `IS_ANDROID` 守卫，非 Windows 分支
  直接读 `switches::kUserDataDir` 并 `PathService::OverrideAndCreateIfNeeded(DIR_USER_DATA, …)`；
  全树非测试代码只有这一处覆盖 `DIR_USER_DATA`。缺省数据目录是应用私有目录
  （`chrome_paths_android.cc:92-94`，`DIR_ANDROID_APP_DATA`）。
- **注入点**：Java 侧 `CommandLine.getInstance().appendSwitchWithValue(...)`，
  必须在 `LibraryLoader.ensureInitialized()` 之前（native 加载时
  `ensureCommandLineSwitched()` 一次性把 Java 命令行交给 native，
  `LibraryLoader.java:689-696`，注释 “Must happen as soon as native is loaded”）。
  现成范例：`ChromeBrowserInitializer.preInflationStartupDone()`（约行 140）
  已用 `appendSwitch`，且在 `ensureInitialized()`（`startChromeBrowserProcessesSync`，约行 275）之前。
  备用 native 点 `chrome_main_delegate_android.cc:46-54` 在 `PreSandboxStartup` 之后，
  对 `--user-data-dir` 已太晚，不能用。
- **结论**：每个环境 = `<app私有目录>/envs/<env_id>/` 下的独立数据目录；
  启动时读当前环境，注入 `--user-data-dir` 与 `--stealth-seed`，切换环境 = 写
  SharedPreferences + 杀进程重启。子目录放应用私有目录之下（避开 SELinux 问题）。
- **缓存隔离缺口（已核实，Copilot 评审指出，我亲自核对）**：
  `GetUserCacheDirectory`（`chrome/common/chrome_paths_android.cc:25-90`）规定
  Default profile 自身的缓存直接放在共享的 `DIR_CACHE` 之下（向后兼容，
  `GetCacheRelativePath` 对初始 profile 返回 nullopt），即**不同环境的 HTTP 磁盘缓存
  会撞在同一个目录**。修复：启动时同时注入 `--disk-cache-dir=<env>/Cache`——
  开关经 `chrome_command_line_pref_store.cc:71` 映射到 `prefs::kDiskCacheDir`，
  网络服务在 `profile_network_context_service.cc:1388` 经 `GetDiskCacheDir` 读取
  （开关名 `disk-cache-dir`，`chrome_switches.h:285`）。真机验证时确认缓存确实分开。
- **隔离范围清单（v1 只保证浏览器内状态）**：Cookie/存储/登录态/历史/缓存
  随数据目录隔离；以下不在 v1 隔离范围内，需在文档里明说：下载文件（系统
  Download 目录共享）、Android SharedPreferences（应用级，不在 Profile 内）、
  系统备份恢复、通知数据。后续版本逐项评估。
- **重启机制（已核实）**：切换环境用现成的
  `ApplicationLifetime.terminate(true)`（4 处以上调用点，如
  `ProcessInitializationHandler.java:312`、`FragmentDependencyProvider.java:197`），
  它经 `BrowserRestartActivity`（`BrowserRestartActivity.java`：杀主进程 PID，
  再发 MAIN intent 重启）完成重启。注意：Manifest 把 LAUNCHER 改指 picker 后，
  重启的 MAIN intent 会回到 picker，正好是切环境的期望行为。

- 环境元数据（名字、备注、种子、代理配置）存在 SharedPreferences 或
  一个独立的 JSON 文件里，不进 Chromium 的 Profile（避免与 Profile 数据混在一起）。

### 2.2 指纹：固定种子

现状（已亲自核对 `android_stealth.patch` 与本地 159.0.8149.0 树，2026-11-21）：

- `--stealth-seed=N` 开关已存在；`content/browser/renderer_host/render_process_host_impl.cc:4080`
  把它放进转发白名单（`PropagateBrowserCommandLineToRenderer`），浏览器进程的开关能传到渲染进程。
- 三处独立的 `StealthSeed()`（各自匿名命名空间，非共享实现）：
  `base_rendering_context_2d.cc:183`、`analyser_node.cc:54`、`image_data_buffer.cc:66`，
  读不到开关时都回缺省值 `0x5EEDC10A`，首次调用后 `static const` 缓存。
  消费点：Canvas 噪声（`getImageData`/`toDataURL`/`toBlob` 三路一致）、WebAudio 四路 `get*Data`。
- **今天没有任何地方传入 `--stealth-seed`**（仓库 scripts/config/workflows 全 grep 为空）：
  所有安装、所有启动、所有页面用的都是同一缺省种子，即全用户同一套噪声。
- WebGL 伪装（`webgl_rendering_context_base.cc`，Adreno 642L）与 UA 品牌
  （`user_agent_utils.cc`）、`navigator.webdriver=false` 目前是固定值，不随种子变。

一期改动（方案 b：启动器按环境传开关，C++ 零改动）：

- 种子来源从“固定缺省”改为“环境派生”：启动器按环境 id 算出种子，
  经 `--stealth-seed` 传入；三处 `StealthSeed()` 与转发白名单都不用动。
- 切入点（已核实）：`chrome/android/java/src/org/chromium/chrome/browser/base/SplitCompatApplication.java`
  的 `onCreate`，`CommandLineInitUtil.initCommandLine(...)` 之后、native 初始化之前追加开关；
  数据目录后缀同文件行 72（`PRIVATE_DATA_DIRECTORY_SUFFIX = "chrome"`）与行 226
  （`PathUtils.setPrivateDataDirectorySuffix(...)`）——环境 = 数据目录后缀，选环境必须在
  native 初始化与 Profile 创建之前完成，切换环境 = 换后缀 + 重启进程，不存在进程内热切换。
- 种子派生：环境新建时生成一次随机 uint32 并固化（存进环境元数据），之后不再变——
  即使环境改名种子也不变。**种子丢失后重生成是危险操作**：同一套 Cookie + 突变指纹
  极易被识别为同一人换伪装；种子必须随环境数据一起持久化。
- **种子与环境 ID 规范**：环境 id 用随机 UUID（新建时生成，永不复用）；种子是
  32 位无符号整数，十进制字符串形式经 `--stealth-seed` 传入（`StringToUint` 解析），
  以十进制字符串存在环境元数据里；运行中禁止重新派生；改名不影响 id 与种子；
  删除环境时同时删数据目录与元数据（删目录失败则拒绝删除并报错，不留半删状态）。
- **切换原子性**：环境清单带版本号；切换流程 = 完整校验目标环境（目录存在、
  元数据可读、种子有效）→ 写目标环境 → 落盘完成标记 → 原子更新 current env；
  切换中途崩溃时，下次启动按“完成标记”决定进旧环境还是新环境，绝不进半切状态；
  种子丢失或目录缺失时拒绝启动该环境并报错，不静默重生成。
- 后续（v2）：WebGL renderer 字符串、UA 细节、时区/语言/分辨率也按种子选
  预设；v1 只做 Canvas + 音频噪声的按环境种子（已有机制），其余保持固定值。

### 2.3 代理：每环境独立

- 每环境一套代理配置，存在环境元数据里；启动时写入该环境的 PrefService
  代理设置（`proxy` prefs），或经命令行 `--proxy-server` 传入（待核实哪条路在安卓上可行）。
- 预设：usque-app 的 SOCKS5（已核实：默认监听 `127.0.0.1:1080` 与 `[::1]:1080`；
  HTTP 代理默认 `127.0.0.1:8080` 与 `[::1]:8080`；来源
  GeorgeXie2333/usque-app `crates/usque-core/src/config/mod.rs:1288-1298` 的
  `Default for ProxySettings`，README 称三者默认开启、监听 loopback）；
  同时支持手动填 SOCKS5/HTTP 代理地址。
- 隧道本身不在浏览器里：usque-app（或 v2rayNG/Clash Meta）在手机上独立运行，
  浏览器只管把每个环境的流量指到对应的本地代理端口。
- 诚实提醒：WARP 出口是共享的 Cloudflare 地址段，过验证效果好，但多账号
  防关联上不是彻底隔离；严格隔离需每环境配不同住宅代理（后续升级项）。

### 2.4 环境管理界面

- v1：启动器里的简单列表（新建/改名/删/选当前环境/换指纹=重新随机种子/
  填代理地址）。不做花哨的 UI，先保证“选环境 → 进浏览器”这条主路通。
- v2：指纹预设选择（常用机型）、环境备注、导入/导出。

### 2.5 文件布局（本仓库）

```
docs/fingerprint-python-design.md   # 本文档
android_stealth.patch               # 现有；一期如需改 C++ 种子机制就改这里
patches/fingerprint/                # 二期新增补丁（如需要独立于 stealth 的补丁时）
  series
  <name>.patch
patches/cpython/                    # 三期新增补丁与 GN 改动（见 §3）
  series
  <name>.patch
scripts/build_android.sh            # 如需应用新补丁集，在此按顺序接入
.github/workflows/verify.yml        # 同步更新：新补丁集也要进 verify 的 patch 阶段
```

注意：verify.yml 目前只应用 `android_stealth.patch` + ungoogled series；
新增补丁集必须同时改 verify.yml 与 build_android.sh，否则 CI 验不到。

### 2.6 实现顺序与 v1 范围

1. **v1（本阶段）**：环境数据目录选择（启动器 Activity 选环境 →
   `SharedPreferences` 记当前环境 → 启动时注入 `--user-data-dir`、
   `--disk-cache-dir` 与 `--stealth-seed`）＋ 按环境种子出噪声（C++ 零改动，
   复用现有开关机制）。最小可验证：新增 Java 文件 + 改 1 个现有 Java 注入点
   （`ChromeBrowserInitializer.preInflationStartupDone`）+ Manifest 注册；
   新文件需进 `chrome/android/chrome_java_sources.gni` 的 sources 列表
   （`gn gen` 可验；注意不是 `java_sources.gni`，已核实
   `ChromeTabbedActivity.java` 列在 `chrome_java_sources.gni` 里）。
   LAUNCHER 在 Manifest 里是 `activity-alias com.google.android.apps.chrome.Main`
  （指向 `ChromeTabbedActivity`），v1 改它的 `targetActivity` 指到 picker
   （一行），同时注册 picker Activity。v1 不做浏览器内切换环境：
   切环境需杀进程后重进启动器（后续版本加应用内入口，用
   `ApplicationLifetime.terminate(true)` 重启）。
2. **v2**：环境管理界面完整版（新建/改名/删/换指纹/换代理 UI）。
3. **v3**：每环境代理配置落盘＋预设；指纹预设（WebGL/UA/时区/语言按种子选）。

### 2.7 风险点

- **数据目录路径**：`--user-data-dir` 已核实原生生效（§2.1），但两点未在真机实测：
  子目录若在应用私有目录之外是否过 SELinux（v1 坚持放私有目录之下避开），
  以及 `OverrideAndCreateIfNeeded` 对多层不存在子目录的创建行为（代码传 `create=true`，
  真机验证时确认）。
- **种子注入时机**：`--stealth-seed` 必须在 native 初始化前进入命令行；
  若 Java 侧切入点在数据目录确定之后，需调整启动顺序。
- **verify 验不到编译**：verify.yml 只跑 `gn gen`，C++/Java 语义错误
  只有 build-hop 编译链能发现；v1 尽量只改补丁上下文内已验证的机制，
  不引入新 GN 目标。
- **渲染进程读不到 Profile 路径**：若种子派生改走“运行时读 Profile 路径哈希”，
  渲染进程沙箱可能读不到——v1 坚持用命令行开关传种子，避开这条路。
- **切换环境丢状态**：重启进程切换环境时，未保存的页面状态会丢；
  v1 接受这个代价（桌面指纹浏览器切环境同样是重启）。
- **备份与恢复**：系统备份恢复不得把环境元数据与数据目录混着恢复
  （恢复一半会导致种子与目录不一致）；v1 要求备份时排除环境目录，
  或恢复后做一致性校验，校验不过则拒绝启动并报错。
- **删除残留与升级迁移**：删除环境必须删干净数据目录与缓存目录
  （`--disk-cache-dir` 指向的目录也要删）；应用升级时环境清单格式
  变化要有版本号与迁移路径，旧版本清单读不出时拒绝启动并报错，
  不静默重建（重建等于丢种子）。
- **种子与目录双重校验**：启动时同时校验元数据里的种子与数据目录
  是否成对存在；任一缺失都拒绝启动该环境，不自动补（自动补等于指纹漂移）。

## 3. 三期：原生 CPython

### 3.1 嵌入方式（Chaquopy 式）

- 预编译 `libpython`（armeabi-v7a / arm64-v8a）＋精简 stdlib（zipimport），
  作为第三方预编译库嵌进 GN 构建，约 20–30MB 体积增长。
- 不从源码编 CPython（Chromium 构建系统里编 CPython 源码是巨坑）；
  预编译产物放仓库 `third_party/python/` 或 CI 下载（待定：仓库体积 vs 可复现性）。
- **版本冻结（Copilot 评审要求）**：动手前先冻结 CPython 版本与精确构建配置
  （NDK 版本、libc++、API level、每 ABI 的依赖清单），产出可复现构建脚本；
  先做最小验证原型，确认 `_io`、导入机制、线程、sqlite、zlib 等核心模块
  与动态扩展在 Android 上可工作，再定义 GN 包装目标。不要先写 GN 再发现
  扩展模块跑不起来。
- GN：新增 `python` 目标，`chrome_public_apk` 依赖它；JNI 桥接代码用 C++ 写，
  经 `//base/android/jni_generator` 生成 JNI 绑定（Chromium 标准路子）。
  每 ABI 用明确的 prebuilt `shared_library`/`loadable_module` 或 `jni_libs`
  打包目标，记录依赖与符号表校验；`gn gen` 通过后仍需一次真实
  `chrome_public_apk` 链接/打包证据才算验证。
- **stdlib 路径（Copilot 评审要求）**：最小验收里固定临时解压目录、stdlib
  zip 包路径、可写字节码缓存目录与平台导入路径（`PYTHONHOME`/`PYTHONPATH`/
  `sys.path`/locale）；验证四类场景：标准库、纯 Python 第三方包、原生扩展、
  文件导入。

### 3.2 JNI 桥接与两个用途

- **自动化桥接**：Python 侧 API（`browser` 模块）：列标签页、开/关标签页、
  导航、执行 JS 并取返回值、读当前 URL/标题。实现上经 JNI 调
  `chrome/android` 的 Tab/TabModel 层；JS 执行走 `WebContents.evaluateJavaScript`
  类机制（具体类名待实现时对树核实）。**接口边界先定后做**：先规定选择语义
  （当前主窗口 / 指定 tab ID / 指定 window），再从现有 tab model 与
  `WebContents` 接口实现最小动作集；多窗口、Custom Tab、多 Activity 下的
  行为要写进边界说明并补并发切换/窗口销毁测试。
- **独立控制台**：浏览器内一个 Activity/Fragment，带输入框与输出区，
  跑任意 Python 代码（REPL 式），与页面无关；stdout/stderr 重定向到界面。
  **权限与崩溃边界**：v1 控制台是高权限嵌入式控制台，与浏览器同进程——
  Python 原生代码崩溃会拖垮浏览器进程，这一点要明说并接受（个人工具）；
  至少实现超时、内存限制、线程清理与受控 stdout/stderr 生命周期；
  独立进程控制台作为后续加固项。
- 线程模型：Python 解释器跑在独立线程（GIL），自动化调用经 JNI 回调到 UI 线程；
  解释器初始化一次，控制台与桥接共用。**可执行模型**：解释器仅在浏览器进程
  初始化一次；所有 Python 长任务进工作线程；只有自动化桥接回调经 `PostTask`
  回到 UI 线程；每次跨语言调用用 `PyGILState_Ensure/Release` 与保存/恢复
  线程状态封装，禁止重复初始化解释器。

### 3.3 文件布局（本仓库，三期）

```
patches/cpython/
  series
  <name>.patch              # GN 目标 + JNI 桥接 + Java 侧控制台/桥接入口
third_party/python/         # 预编译 libpython + stdlib（或 CI 下载脚本）
scripts/fetch_python.sh     # 如走 CI 下载，拉预编译产物的脚本
```

### 3.4 实现顺序

1. 先让 `libpython` 编进 APK 且能 `Py_Initialize`（最小验证：启动时初始化一次，
   打一条 log；verify 只能验 gn gen，编译证据在 build-hop）。
2. 独立控制台（不依赖浏览器内部 API，风险低）。
3. 自动化桥接（依赖 Tab/WebContents 的 JNI，风险高，放后面）。

### 3.5 风险点

- **体积**：APK 增加 20–30MB；`chrome_public_apk` 本已 ~500MB，相对可接受，
  但需确认 Play/侧载无额外限制（侧载无限制）。
- **NDK ABI**：预编译 libpython 必须与 Chromium 的 NDK 目标 ABI 一致
  （arm64-v8a 为主；armeabi-v7a 按需）。
- **GIL 与 UI 线程**：Python 长时间运行会占 GIL；自动化调用需异步回调，
  不能阻塞 UI 线程。
- **verify 验不到**：GN 目标新增后 `gn gen` 能验引用正确性，但 JNI 生成、
  链接错误只有编译能发现；三期必须走一次 build-hop 才算验证。

## 4. 验证策略

- **verify.yml**（本阶段唯一 CI 门）：补丁能打上（`git apply --check`）、
  无空白错误、domain substitution 干跑、`gn gen` 两个 args 文件通过。
  它不编译，所以 C++/Java 语义错误它发现不了——汇报时必须明说这一点。
- **build-hop 编译链**（下一阶段）：二期 v1 与三期真正验证编译与打包的唯一证据；
  本阶段不启动（8 小时+，等设计与 v1 补丁在 verify 通过后再议）。
- **真机测试**（最终）：环境隔离（两个环境分别登录同一网站不串号）、
  指纹稳定性（同一环境多次打开 Canvas 指纹值不变，不同环境不同）、
  代理生效（每环境出口 IP 不同）、CPython 控制台能跑 `print(1+1)`。
  本地 VM 跑不了模拟器，真机测试只能在用户手机上做。
  **v1 验收用例（Copilot 评审补充）**：冷启动日志里确认实际生效的数据目录
  与注入的种子值；两个环境 Cookie 互不串；同一环境多次启动 Canvas/Audio
  指纹值不变；切换重启后种子不漂移；HTTP 缓存目录按环境分开
  （`--disk-cache-dir` 生效）；删除环境后数据与缓存目录都不残留。

## 5. 核实记录

（核查员结论填入后，这里记每条关键结论的证据来源与日期。）

- [x] 安卓 Profile 模型：默认单 Profile（只加载 `Default`），桌面多 Profile API 大量被 `!IS_ANDROID` 守卫；树内有默认关闭的 `kAndroidMultiprofile` flag 作备选（2026-11-21，亲自核对 `profile_manager.cc`、`startup_data.cc`、`chrome_feature_list.cc:747`）
- [x] `--user-data-dir` 在安卓上原生生效：`InitializeUserDataDir` 无 `IS_ANDROID` 守卫，全树唯一覆盖 `DIR_USER_DATA` 处；缺省为应用私有目录（2026-11-21，亲自核对 `chrome_main_delegate.cc:654-706`、`chrome_paths_android.cc:92-94`）
- [x] Java 侧追加开关的切入点：`CommandLine.appendSwitchWithValue`，须在 `LibraryLoader.ensureInitialized()` 之前；范例 `ChromeBrowserInitializer.preInflationStartupDone()`（2026-11-21，亲自核对 `LibraryLoader.java:689-717`、`ChromeBrowserInitializer.java`）
- [x] usque-app 默认端口：SOCKS5 `127.0.0.1:1080`/`[::1]:1080`，HTTP `127.0.0.1:8080`/`[::1]:8080`（2026-11-21，经 `gh api` 亲自核对 `crates/usque-core/src/config/mod.rs:1288-1298`）
- [x] 缓存隔离缺口与修复：Default profile 缓存直接放共享 `DIR_CACHE`（`chrome_paths_android.cc:25-90`）；修复用 `--disk-cache-dir`（开关 `chrome_switches.h:285` → pref `chrome_command_line_pref_store.cc:71` → 网络服务 `profile_network_context_service.cc:1388`），链路已亲自核对
- [x] 重启机制：`ApplicationLifetime.terminate(true)` 在 4 处以上调用点存在（`ProcessInitializationHandler.java:312` 等）；`BrowserRestartActivity` 杀主进程后发 MAIN intent 重启（2026-11-21，亲自核对）
- [x] 注入时序：`handlePreNativeStartupAndLoadLibraries` 内 `preInflationStartupDone()`（行 116）严格先于 `startChromeBrowserProcessesSync`（行 247）→ `ensureInitialized`（2026-11-21，亲自核对 `ChromeBrowserInitializer.java`）
- [x] Java sources 清单：`ChromeTabbedActivity.java` 列在 `chrome/android/chrome_java_sources.gni`（不是 `java_sources.gni`）；LAUNCHER 是 Manifest 里的 `activity-alias com.google.android.apps.chrome.Main`（2026-11-21，亲自核对）
- [x] Copilot 评审（2026-11-21，1.36 credits）：14 条发现，11 条采纳进文档（缓存隔离、重启机制、CPython 版本冻结、原子性、隔离范围、GIL 模型、控制台边界、stdlib 路径、打包细节、备份/删除/迁移风险、验收用例、种子规范、Tab 边界）；1 条已过期（v1 启动器入口，评审时文档还是骨架版）；评审中关于 `setPrivateDataDirectorySuffix` 的表述不适用（本文档机制是 `--user-data-dir`，已核实）
- [x] stealth 补丁现状：`--stealth-seed` 开关已存在，7 个 C++ 文件，无 Java；三处 `StealthSeed()` 缺省值 `0x5EEDC10A`，转发白名单在 `render_process_host_impl.cc:4080`；今天无任何地方传该开关（2026-11-21，亲自核对 `android_stealth.patch` 与本地树）
- [x] 启动器切入点：`SplitCompatApplication.java` 行 72（后缀常量 `"chrome"`）、`onCreate` 内 `initCommandLine` 后、`setPrivateDataDirectorySuffix` 行 226（2026-11-21，亲自核对本地树）
- [x] CPython 打包机制：预编译 `.so` 走 `android_apk` 的 `loadable_modules`（路径列表，不必是编译产物，`internal_rules.gni:2844` 直接作 `--native-libs` 输入）；`chrome_public_apk` 透传 `loadable_modules`/`asset_deps`/`deps`（`chrome_public_apk_tmpl.gni:339-340,396-397,477-479`）；stdlib zip 走 `android_assets`＋`renaming_destinations` 定资产路径；只包 4 个真库＋57 个扩展模块，`libpython3.so` 与 3 个 `*_chaquopy.so` 是无人 NEEDED 的垫片（2026-11-21，readelf 逐个核对 65 个 `.so`）
