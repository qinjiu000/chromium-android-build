# chromium-android-build

Build Chromium for Android (arm64, `chrome_public_apk`) on free GitHub-hosted
runners, with the `android_stealth.patch` applied — a fingerprint-stealth patch
series for the Android build (same approach as the desktop three-phase patches:
de-headless UA, `navigator.webdriver` always false, WebGL/Canvas fingerprint
noise).

## Why a hop chain

A full Chromium Android build takes ~5.5–7 hours on a 4-vCPU runner. A single
GitHub Actions job is capped at 6 hours, and the old single-job workflow in
this repo was cancelled at the cap three times without producing an APK.

This repo therefore builds as a **resumable hop chain**, following the publicly
verified iNWEB-Browser chain (Chromium 154, 14 hops, `chrome_public_apk`
completed 2026-09-22 on free `ubuntu-24.04` runners — the only publicly
verified full Android Chromium build on free runners):

- One dispatch = one hop (~350 min max). Each hop re-fetches the pinned tree
  (~15 min), resumes the previous hop's `out/` build state, compiles inside a
  265-minute box, then either uploads the finished APK or packages the new
  build state for the next hop.
- State travels as **artifacts** (tar.zstd split into ≤4 GiB parts + sha256
  manifest), never `actions/cache` — the 10 GB/repo cache cap cannot hold a
  Chromium build (tree ~30 GB, meaningful ccache 20–50 GB).
- **Plain ninja only** — autoninja/siso incremental state does not survive the
  cross-runner `out/` transfer (iNWEB hops 4–7 wasted ~14 h on this).
- Source mtimes are normalized to 2020-01-01 (excluding `out/`), twice: once
  after the fetch, once after the patch is applied — otherwise ninja sees
  every input as changed and re-executes the graph.
- **Debug build** (`is_debug=true`, `symbol_level=1`): the only config with a
  public free-runner success. No official (ThinLTO/PGO) Android build on free
  runners has ever been publicly verified.

## How to run the chain

The chain runs itself inside GitHub — dispatch hop 1 and walk away:

1. **Hop 1 (fresh):** Actions → *Build hop (resumable Chromium Android)* →
   Run workflow. Leave `resume-from` and `resume-run-id` empty.
2. Each hop's last step dispatches its own successor with `GITHUB_TOKEN`
   (`workflow_dispatch` is a documented exception to "`GITHUB_TOKEN`
   events don't trigger workflows"). The next hop resumes from the state
   artifacts the previous hop uploaded. No external dispatcher, no
   babysitting.
3. The chain stops by itself when a hop uploads `hop-apk-<n>` (that run's
   Artifacts section holds the APK), when a hop fails or packages no state
   (a human must look before anything resumes), or at the `max-hops` safety
   cap (default 20). Set `auto-chain=false` to dispatch hops manually the
   old way — each hop's summary prints the next hop's inputs
   (`resume-from: hop-state-<run_number>`, the run id, and the next
   `hop-index`).
4. State artifacts expire after 5 days; the self-chaining dispatch happens
   within minutes of each hop finishing, so expiry is only a risk if the
   chain stops on a failure. Public-repo Actions minutes are free —
   wall-clock time is the constraint, not minutes. (iNWEB's 154 chain:
   14 hops, ~45 h runner time, ~21 h genuine compile.)

## Verify workflow (no compile)

`Verify (no compile)` runs the cheap stages only — fetch the pinned tree,
verify the tag, apply the patch — in ~35–60 min. It runs automatically on
pushes that touch the patch, the scripts, or `config/chromium/`, and can also
be dispatched manually. Its PASS says the chain's inputs are sound; it says
nothing about the compile itself.

## Files

| Path | Purpose |
| --- | --- |
| `android_stealth.patch` | Stealth patch series, targets Chromium **159.0.8149.1** exactly |
| `config/chromium/BASELINE` | Pinned Chromium tag (`CHROMIUM_TAG=159.0.8149.1`) |
| `config/chromium/args-development.gn` | GN args: debug build, arm64, `chrome_public_apk` |
| `scripts/fetch_chromium.sh` | Pinned depot_tools + `gclient sync` directly at the tag |
| `scripts/build_android.sh` | Apply patch → normalize mtimes → `gn gen` → plain ninja |
| `.github/workflows/build-hop.yml` | The resumable hop chain |
| `.github/workflows/verify.yml` | Fetch + tag + patch check, no compile |

## Reference

- iNWEB-Browser chain: <https://github.com/iNWEB-Official/iNWEB-Browser>
  (workflow `b001-build-hop.yml`, chain ledger
  `docs/verification/B001-BUILD-CHAIN.md`)
