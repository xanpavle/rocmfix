<div align="center">

# 🔧 ROCmFix

### Auto-detect your AMD GPU, fix ROCm overrides, and benchmark your AI backends in seconds.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-orange)]()
[![GPU](https://img.shields.io/badge/GPU-AMD%20RDNA-red?logo=amd)]()

**One file. Zero installs. No pip. No venv. Just run it.**

</div>

---

## ❓ What is this?

Running local AI (llama.cpp, Ollama, LM Studio, PyTorch) on AMD GPUs often leads to cryptic errors like `HSA_STATUS_ERROR_INVALID_ISA` or silent fallbacks to CPU. Usually, you just need to set the `HSA_OVERRIDE_GFX_VERSION` environment variable.

**ROCmFix** detects your exact AMD GPU, checks a live community database, automatically applies the correct shell environment override, and runs health diagnostics to ensure your setup is ready.

### ✨ Features
* **Auto-Apply & Undo:** Automatically modifies CMD, PowerShell, Bash, Zsh, or Fish configs to apply overrides safely.
* **`rocmfix doctor`:** Scans your hardware, Adrenalin drivers, HIP SDK, and Vulkan API to diagnose issues.
* **`rocmfix bench`:** A 10-second backend race! Temporarily isolates Ollama or LM Studio, runs a generation test using Vulkan, then HIP, and tells you which is faster on your PC.
* **`rocmfix install-hip`:** Missing the HIP SDK on Windows? This safely downloads and launches the official AMD installer for you.
* **Live Database & Self-Updater:** Fetches new GPUs from the cloud daily, and updates itself via `rocmfix update`.
* **Export System Report:** Generate a Markdown file of your setup for Reddit/Discord help posts.

---

## 🚀 Quick Start

### Download (One Command)

**Windows PowerShell:**
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py" -OutFile "rocmfix.py"
```

**Linux / macOS:**
```bash
curl -O https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py
```

### Run
```bash
python rocmfix.py
```

---

## 📋 Commands

| Command | What it does |
|---|---|
| `rocmfix` | Detect GPU, suggest override, and auto-apply |
| `rocmfix doctor` | Full health check of drivers, HIP, and Vulkan |
| `rocmfix bench` | Benchmark Vulkan vs HIP/ROCm in LM Studio or Ollama |
| `rocmfix install-hip`| Download and install the AMD HIP SDK (Windows) |
| `rocmfix export` | Generate a Markdown system report for troubleshooting |
| `rocmfix update` | Update ROCmFix to the latest version from GitHub |
| `rocmfix sync` | Force-fetch the latest GPU database from the community server |
| `rocmfix test` | Smoke test current override to see if ROCm/HIP detects it |
| `rocmfix undo` | Safely revert the last auto-applied override |
| `rocmfix list` | Show all 17+ GPUs currently supported in the database |
| `rocmfix telemetry`| Manage your anonymous benchmark sharing settings |
| `rocmfix install` | Install `rocmfix` as a global command on your system |

---

## 🎮 Supported GPUs

ROCmFix supports **RDNA4** (RX 9070/9060 series), **RDNA3** (RX 7000 series), **RDNA2** (RX 6000 series), and Integrated Graphics. 

*New GPUs are synced automatically from our live database without needing to update the app!*

**Don't see your card?** Run `python rocmfix.py` — it will generate a pre-filled GitHub issue link so you can contribute your GPU!

---

## 🔥 HIP vs Vulkan — Which should I use?

If you're running **llama.cpp / LM Studio / Ollama** on a consumer AMD card (especially on Windows):

| | Vulkan | HIP / ROCm |
|---|---|---|
| **Setup** | ✅ Zero config | ⚠️ Needs HIP SDK + overrides |
| **Windows** | ✅ Works out of the box | ⚠️ HIP SDK still maturing |
| **Linux** | ✅ Good | ✅ Better |
| **Consumer RDNA3 perf** | ✅ Often faster | ⚠️ Sometimes slower |
| **PyTorch support** | ❌ Limited | ✅ Full support |

### TL;DR
- **Running LLMs on Windows?** → Try **Vulkan** first. Run `rocmfix bench` to verify.
- **PyTorch / Stable Diffusion?** → Run `rocmfix install-hip`, then apply the override ROCmFix gives you.

---

## 📊 Privacy & Telemetry

ROCmFix includes an **opt-in** telemetry system to help grow the community database. 
If enabled, ROCmFix securely sends your GPU model, driver version, OS, and benchmark results to a private collector. **We never collect usernames, IPs, file paths, or AI prompts.** Read the full details in `PRIVACY.md`.

---

## 📄 License

This project is licensed under the MIT License - see the `LICENSE` file for details.

<div align="center">
<b>If this saved you hours of Reddit digging, drop a ⭐</b><br>
Made for the local AI community by <a href="https://github.com/xanpavle">xanpavle</a>
</div>