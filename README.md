<div align="center">

# 🔧 ROCmFix

### Find out what your AMD GPU needs for local AI, apply the right ROCm override, and benchmark Vulkan vs HIP.

[![Version](https://img.shields.io/badge/version-0.1.8-blue)](https://github.com/xanpavle/rocmfix/releases)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE.txt)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-orange)]()
[![GPU](https://img.shields.io/badge/GPU-AMD%20Radeon-red?logo=amd)]()

**One file. Zero dependencies. No pip, no venv. Just run it.**

</div>

---

## ❓ What is this?

Running local AI (Ollama, LM Studio, llama.cpp, PyTorch, ComfyUI) on an AMD GPU often fails with errors like:

```
HSA_STATUS_ERROR_INVALID_ISA
hipErrorNoBinaryForGpu
rocBLAS error: Cannot read TensileLibrary.dat ... for GPU arch : gfx1031
no compatible amdgpu devices detected
```

…or it silently falls back to the CPU. The usual fix is `HSA_OVERRIDE_GFX_VERSION`, which makes ROCm run your card with the kernels of a supported sibling. The hard part is knowing **whether your card needs it and which value to use**.

**ROCmFix reads your GPU's real gfx target straight from the driver**, works out the right answer from rules, applies it safely (with undo), and checks the result.

### ✨ Features
* **Real gfx detection:** reads the gfx target from the amdgpu driver on Linux (`/sys/class/kfd`) and from `hipInfo` on Windows, so it works even for GPUs that aren't in the database.
* **Rule-based overrides:** e.g. `gfx1031/1032/1034 → 10.3.0`, `gfx1103 (780M) → 11.0.0`. It also tells you when **no** override is needed, or when an override you already set is unnecessary.
* **Auto-apply & undo:** writes to your shell config (bash, zsh, fish) or to your Windows user environment. On Linux it can also set the override on the **Ollama systemd service**, which doesn't read `~/.bashrc`. `rocmfix undo` puts everything back.
* **`rocmfix doctor`:** checks GPU, gfx target, drivers, ROCm/HIP, Vulkan, and where the override is (and isn't) set.
* **`rocmfix bench`:** a real Vulkan vs ROCm comparison. Each backend runs on its own Ollama server, and Ollama's own log confirms which backend ran. One warm-up, then the median of 3 runs.
* **Live GPU database:** synced daily from this repo, used for names, notes and known issues.
* **Markdown report:** `rocmfix export` writes a system report you can paste into Reddit/Discord/GitHub help threads.

---

## 🚀 Quick Start

**Windows PowerShell:**
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py" -OutFile "rocmfix.py"
python rocmfix.py
```

**Linux:**
```bash
curl -O https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py
python3 rocmfix.py
```

---

## 📋 Commands

| Command | What it does |
|---|---|
| `rocmfix` | Detect GPUs and gfx targets, recommend and optionally apply an override |
| `rocmfix doctor` | Full health check: GPU, gfx, drivers, ROCm/HIP, Vulkan, override locations |
| `rocmfix bench` | Benchmark Vulkan vs ROCm (`--runtime ollama\|lmstudio\|llamacpp`) |
| `rocmfix test` | Smoke test the current override (does ROCm/HIP see the GPU?) |
| `rocmfix verify` | Smoke test a specific override value you type in |
| `rocmfix undo` | Revert the last override ROCmFix applied (shell config, Windows env, systemd) |
| `rocmfix list` | Show every GPU in the database with its gfx target and status |
| `rocmfix gpus --online` | Same as `list`, plus the online database version and age |
| `rocmfix sync` | Force-fetch the latest GPU database |
| `rocmfix export` | Write a Markdown system report for help posts |
| `rocmfix install-hip` | Open AMD's HIP SDK download page (Windows) |
| `rocmfix install-rocm` | Show ROCm install commands for your distro (Linux) |
| `rocmfix optimize` | Windows: raise the GPU timeout (TDR) so long AI kernels don't reset the driver. Shows the change and asks first; `--revert` undoes it. Linux: checks `render`/`video` groups |
| `rocmfix contribute` | Generate a database entry for a new GPU |
| `rocmfix telemetry` | Enable/disable anonymous reports, or view a sample payload |
| `rocmfix update` | Update ROCmFix to the latest release |
| `rocmfix install` | Install `rocmfix` as a global command |

---

## 🎮 What does my GPU need?

ROCmFix works this out automatically. Here's the gist for **ROCm 7.x** (current Ollama requires ROCm 7):

| GPU | gfx | Needs |
|---|---|---|
| RX 9070 / 9070 XT / 9060 XT, AI PRO R9700 | gfx1201 / gfx1200 | Nothing, native |
| RX 7900 XTX / XT / GRE, W7900 / W7800 | gfx1100 | Nothing, native |
| RX 7800 XT / 7700 XT / 7600, W7700 | gfx1101 / gfx1102 | Nothing on ROCm 7 (`11.0.0` on older ROCm) |
| RX 6800 / 6800 XT / 6900 XT / 6950 XT | gfx1030 | Nothing, native |
| RX 6700 XT / 6600 / 6500 XT | gfx1031–1034 | `HSA_OVERRIDE_GFX_VERSION=10.3.0` |
| Radeon 780M / 760M / 740M (Phoenix iGPU) | gfx1103 | `HSA_OVERRIDE_GFX_VERSION=11.0.0` (Vulkan is often faster) |
| Radeon 8060S (Strix Halo) / 890M (Strix Point) | gfx1151 / gfx1150 | Nothing on ROCm 7 |
| RX 5700 XT / 5500 XT (RDNA1) | gfx1010 / gfx1012 | No working override, use Vulkan |
| Radeon VII / MI50, Vega 56/64 | gfx906 / gfx900 | Dropped from current ROCm, use Vulkan or older ROCm |

**Card not listed?** Run `rocmfix` anyway. It reads the gfx target from your driver. If the card isn't in the database, it gives you a pre-filled GitHub issue link to add it.

### Windows notes
* **Ollama for Windows** ships its own ROCm runtime and **does read `HSA_OVERRIDE_GFX_VERSION`** (verified with Ollama 0.40). After applying or removing an override, quit Ollama from the tray and start it again.
* The **HIP SDK** and other apps may ignore the variable. If one does, use its **Vulkan** backend.
* An override on a card that's already supported doesn't help: it forces the card onto a sibling's kernels. `rocmfix doctor` warns you about this.

### Linux notes
* Ollama usually runs as a **systemd service**, and services don't read `~/.bashrc`. When ROCmFix applies an override, it offers to add it to `/etc/systemd/system/ollama.service.d/rocmfix.conf` too (needs sudo; `rocmfix undo` removes it).
* If ROCm sees no GPU, make sure you're in the `render` and `video` groups (`rocmfix optimize` checks).

---

## 🔥 HIP vs Vulkan: which should I use?

| | Vulkan | HIP / ROCm |
|---|---|---|
| **Setup** | ✅ Zero config | ⚠️ Needs ROCm/HIP and sometimes an override |
| **GPU coverage** | ✅ Almost every AMD GPU | ⚠️ Supported targets only (or override) |
| **iGPUs** | ✅ Often faster | ⚠️ Mixed |
| **Prompt processing (prefill)** | ⚠️ Often slower | ✅ Often faster on supported cards |
| **PyTorch / ComfyUI** | ❌ Not supported | ✅ Required |

**Don't guess. Measure:** `rocmfix bench`

* **Ollama** (0.12.11+ for Vulkan): ROCmFix runs a separate Ollama server per backend (on port 11435, so your normal server isn't touched). Ollama's own log confirms which backend actually ran, and any run that fell back to something else isn't counted.
* **LM Studio** switches runtimes only through the interactive `lms runtime select`, so ROCmFix benchmarks the runtime that's active now. Switch runtimes, run `rocmfix bench` again, and it compares the two runs on the same model.
* **llama.cpp server**: benchmarks the server running at `http://127.0.0.1:8080` (or `ROCMFIX_LLAMA_CPP_URL`), using the server's own timings.

---

## 📊 Privacy & Telemetry

Telemetry is **opt-in** and helps grow the GPU database and the benchmark stats. With it on, ROCmFix sends your GPU model, gfx target, driver, OS, ROCm version and benchmark numbers. It **never** sends model names, server URLs, file paths, prompts, usernames or emails. Your IP is not stored with reports. Run `rocmfix telemetry` and press `S` to see the exact payload. Full details: [PRIVACY.md](PRIVACY.md).

---

## 🆕 What's new in 0.1.8

* **Fixed:** `rocmfix bench` crashed in 0.1.7 (missing helper functions).
* **Real gfx detection** from the driver, with rule-based overrides. The database now only adds names and notes.
* **Bench is now a real comparison:** it uses Ollama's actual backend switches (`OLLAMA_VULKAN`, `ROCR_VISIBLE_DEVICES`, `GGML_VK_VISIBLE_DEVICES`) and checks which backend ran. It also adds a warm-up, median of 3 runs, and true token counts.
* **Database updated for ROCm 7:** the RX 7800 XT / 7700 XT / 7600 and RX 9060 XT are now native. Added Strix Halo, Strix Point, the 780M/760M/740M, RX 5700 XT/5500 XT, Radeon VII/MI50, Vega 56/64 and RX 6950 XT. The database now lives in this repo as [`gpus.json`](gpus.json).
* **Linux:** can add the override to the Ollama systemd service.
* **Safer undo:** never deletes your shell config, and restores any override line you had before.
* **`optimize`:** shows what it changes, asks first, and can be reverted. It no longer touches HAGS (the old code turned it *off*).
* **Privacy:** model names and server URLs are no longer sent, and PRIVACY.md now matches what's actually collected.
* **Warns** when an override is set on a GPU that doesn't need one.

---

## 🤝 Contributing

New GPU or a better override? Open a [GPU report](https://github.com/xanpavle/rocmfix/issues/new?template=gpu-report.yml) or send a PR editing [`gpus.json`](gpus.json). `rocmfix contribute` generates the entry for you.

## 📄 License

MIT. See [LICENSE.txt](LICENSE.txt).

<div align="center">
<b>If this saved you hours of Reddit digging, drop a ⭐</b><br>
Made for the local AI community by <a href="https://github.com/xanpavle">xanpavle</a>
</div>
