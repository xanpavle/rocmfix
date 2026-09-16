<div align="center">

# 🔧 ROCmFix

### Auto-detect your AMD GPU and get the right ROCm override in seconds.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-orange)]()
[![GPU](https://img.shields.io/badge/GPU-AMD%20RDNA-red?logo=amd)]()

**One file. Zero installs. No pip. No venv. Just run it.**

</div>

---

## ❓ What is this?

If you've ever tried to run local AI (llama.cpp, PyTorch, Stable Diffusion) on an AMD GPU and hit cryptic errors like `HSA_STATUS_ERROR_INVALID_ISA` or `No compatible GPU agent found`, you probably needed to set `HSA_OVERRIDE_GFX_VERSION`. 

**ROCmFix detects your exact AMD GPU, looks it up in a community database, and automatically applies the correct shell environment override for you.**

### ✨ Features
* **Auto-Apply:** Automatically modifies CMD, PowerShell, Bash, Zsh, or Fish config to apply the override.
* **Smart Detection:** Uses Windows Registry and Linux `lspci` for foolproof GPU detection.
* **Undo System:** Safely revert any auto-applied changes with `rocmfix undo`.
* **Telemetry (Opt-in):** Help grow the database by anonymously sharing your GPU detection results.
* **RDNA4 Ready:** Day-one support for RX 9000 series GPUs.

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
| `python rocmfix.py` | Detect GPU, suggest override, and optionally auto-apply |
| `python rocmfix.py test` | Smoke test current override to see if ROCm/HIP detects it |
| `python rocmfix.py undo` | Safely revert the last auto-applied override |
| `python rocmfix.py list` | Show all GPUs currently supported in the database |
| `python rocmfix.py telemetry`| Manage your anonymous data sharing settings |
| `python rocmfix.py install` | Install `rocmfix` as a global command on your system |

---

## 🎮 Supported GPUs

| PCI ID | GPU | Arch | Override | Status |
|--------|-----|------|----------|--------|
| `7550` | RX 9070 XT / 9070 / GRE | RDNA4 | — | ✅ Native |
| `7590` | RX 9060 XT / 9060 / 9050 | RDNA4 | `12.0.1` | ⚠️ Override |
| `744c` | RX 7900 XTX | RDNA3 | — | ✅ Native |
| `744e` | RX 7900 XT | RDNA3 | — | ✅ Native |
| `747e` | RX 7900 GRE / 7800 XT var. | RDNA3 | — | ✅ Native* |
| `7470` | RX 7800 XT | RDNA3 | `11.0.0` | ⚠️ Override |
| `7471` | RX 7700 XT | RDNA3 | `11.0.0` | ⚠️ Override |
| `7480` | RX 7600 | RDNA3 | `11.0.0` | ⚠️ Override |
| `7483` | RX 7600 XT | RDNA3 | `11.0.0` | ⚠️ Override |
| `73af` | RX 6900 XT | RDNA2 | — | ✅ Native |
| `73bf` | RX 6800 XT / 6800 | RDNA2 | — | ✅ Native |
| `73df` | RX 6700 XT | RDNA2 | `10.3.0` | ⚠️ Override |
| `73ff` | RX 6600 XT / 6600 | RDNA2 | `10.3.0` | ⚠️ Override |
| `743f` | RX 6500 XT | RDNA2 | `10.3.0` | ⚠️ Override |
| `164e` | Ryzen 7000 iGPU | RDNA2 | — | 🚫 iGPU |

*\*Some 7800 XT board variants share the 7900 GRE PCI ID. If HIP fails, try override `11.0.0`.*

**Don't see your card?** Run `python rocmfix.py` — it will generate a pre-filled GitHub issue link so you can contribute your GPU!

---

## 📊 Privacy & Telemetry

ROCmFix includes an **opt-in** telemetry system to help grow the community database. 
If you choose to enable it, ROCmFix securely sends your GPU model, PCI ID, OS, Driver version, and Test pass/fail status to a private collector.

**We never collect:** Usernames, IPs, file paths, personal data, or AI model data.
Read the full privacy details in [PRIVACY.md](PRIVACY.md).

---

## 🔥 HIP vs Vulkan — Which should I use?

If you're running **llama.cpp** on a consumer AMD card (especially on Windows), you have two GPU backends available:

| | Vulkan | HIP / ROCm |
|---|---|---|
| **Setup** | ✅ Zero config | ⚠️ Needs HIP SDK + overrides |
| **Windows** | ✅ Works out of the box | ⚠️ HIP SDK still maturing |
| **Linux** | ✅ Good | ✅ Better on datacenter cards |
| **Consumer RDNA3 perf** | ✅ Often faster | ⚠️ Sometimes slower |
| **PyTorch support** | ❌ Limited | ✅ Full support |

### TL;DR
- **llama.cpp on Windows?** → Use the **Vulkan** build. No HIP SDK needed. Just set the override and go.
- **PyTorch / Stable Diffusion?** → Install the **HIP SDK** and use the override ROCmFix gives you.
- **Linux?** → **ROCm** is more mature there. Use the override + ROCm packages.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

<div align="center">
<b>If this saved you 30 minutes of Reddit digging, drop a ⭐</b><br>
Made for the local AI community by <a href="https://github.com/xanpavle">xanpavle</a>
</div>