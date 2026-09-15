<div align="center">

# 🔧 ROCmFix

### Auto-detect your AMD GPU and get the right ROCm override in seconds.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-orange)]()
[![GPU](https://img.shields.io/badge/GPU-AMD%20RDNA-red?logo=amd)]()
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero-brightgreen)]()

**One file. Zero installs. No pip. No venv. Just run it.**

</div>

---

## ❓ What is this?

If you've ever tried to run local AI (llama.cpp, PyTorch, Stable Diffusion) on an AMD GPU and hit cryptic errors like:

```
HSA_STATUS_ERROR_INVALID_ISA
hipErrorNoBinaryForGpu
No compatible GPU agent found
```

...then you probably needed to set `HSA_OVERRIDE_GFX_VERSION` but didn't know which value to use.

**ROCmFix detects your exact AMD GPU, looks it up in a community database, and tells you the exact command to paste into your terminal.** That's it.

---

## 🚀 Quick Start

### Download (one command)

**Windows PowerShell:**
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py" -OutFile "rocmfix.py"
```

**Linux / macOS:**
```bash
curl -O https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py
```

**Or just** [click here](https://raw.githubusercontent.com/xanpavle/rocmfix/main/rocmfix.py), right-click → Save As.

### Run

```bash
python rocmfix.py
```

That's it. No `pip install`, no virtual environment, no build step.

---

## 📸 What it looks like

```
  ╔══════════════════════════════════════╗
  ║           ROCmFix v0.1.0             ║
  ║   AMD GPU override helper for ROCm   ║
  ╚══════════════════════════════════════╝

System Info:
  OS:       windows
  Shell:    cmd
  ROCm/HIP: Not detected

Detected 1 AMD GPU(s):

  [1] AMD Radeon RX 7800 XT
      PCI ID:  7470
      Driver:  32.0.31041.1004
      Status:  ⚠ Needs override
      Real gfx target: gfx1101
      Override value:  11.0.0

      Run this in your terminal (cmd):

      CMD (this session):
        set HSA_OVERRIDE_GFX_VERSION=11.0.0

      CMD (permanent):
        setx HSA_OVERRIDE_GFX_VERSION 11.0.0

      Then verify with: python rocmfix.py test
```

---

## 📋 Commands

| Command | What it does |
|---|---|
| `python rocmfix.py` | Detect your GPU and show the override you need |
| `python rocmfix.py test` | Verify your current override actually works |
| `python rocmfix.py list` | Show all GPUs in the database |
| `python rocmfix.py contribute` | Interactively add a new GPU entry |

---

## 🎮 Supported GPUs

| PCI ID | GPU | Arch | Override | Status |
|--------|-----|------|----------|--------|
| `744c` | RX 7900 XTX | RDNA3 | — | ✅ Native |
| `744e` | RX 7900 XT | RDNA3 | — | ✅ Native |
| `747e` | RX 7900 GRE / 7800 XT variant | RDNA3 | — | ✅ Native* |
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
| `1638` | Ryzen 5000 iGPU | Vega | — | 🚫 iGPU |

*\*Some 7800 XT board variants share the 7900 GRE PCI ID. If HIP fails, try override `11.0.0`.*

**Don't see your card?** Run `python rocmfix.py` — it will generate a pre-filled GitHub issue link so you can contribute your GPU in one click.

---

## 🔥 HIP vs Vulkan — Which should I use?

If you're running **llama.cpp** on a consumer AMD card (especially on Windows), you have two GPU backends:

| | Vulkan | HIP / ROCm |
|---|---|---|
| **Setup** | ✅ Zero config | ⚠️ Needs HIP SDK + overrides |
| **Windows** | ✅ Works out of the box | ⚠️ HIP SDK still maturing |
| **Linux** | ✅ Good | ✅ Better on datacenter cards |
| **Consumer RDNA3 perf** | ✅ Often faster | ⚠️ Sometimes slower |
| **PyTorch support** | ❌ Limited | ✅ Full support |
| **Stable Diffusion** | ⚠️ Via DirectML | ✅ Native via ROCm |

### TL;DR

- **llama.cpp on Windows?** → Use the **Vulkan** build. No HIP SDK needed. Just set the override and go.
- **PyTorch / Stable Diffusion?** → Install the **HIP SDK** and use the override ROCmFix gives you.
- **Linux?** → **ROCm** is more mature there. Use the override + ROCm packages.

### Vulkan quick start (llama.cpp)
```bash
# Download the Vulkan build of llama.cpp from:
# https://github.com/ggml-org/llama.cpp/releases
# Look for: llama-bXXXX-bin-win-vulkan-x64.zip

# Set the override
set HSA_OVERRIDE_GFX_VERSION=11.0.0

# Run with GPU offload
llama-cli.exe -m model.gguf -ngl 99 -p "Hello"
```

---

## 🛠️ How it works

1. **Windows:** Reads the Windows Registry (`HKLM\SYSTEM\...\Class\{4d36e968...}`) to find all AMD display adapters and their PCI device IDs. Falls back to `wmic` if registry fails.
2. **Linux:** Runs `lspci -nn -d 1002::` to enumerate AMD GPUs.
3. Looks up each PCI ID in the built-in community database.
4. Detects your shell (CMD / PowerShell / Bash) and prints the exact copy-paste command.
5. The `test` command runs `rocminfo` (Linux) or `hipInfo.exe` (Windows) to verify the override actually makes your GPU visible to ROCm.

---

## 🤝 Contributing

This tool is only as good as its GPU database. You can help in three ways:

### 1. Report an unknown GPU (easiest)
Run `python rocmfix.py` on your AMD system. If your GPU shows as "UNKNOWN", click the generated GitHub issue link. It's pre-filled with your hardware info.

### 2. Add a GPU entry
```bash
python rocmfix.py contribute
```
This walks you through creating a database entry. Open a PR with the output.

### 3. Test overrides
If you found a working `HSA_OVERRIDE_GFX_VERSION` value for your card that isn't in the database yet, [open an issue](https://github.com/xanpavle/rocmfix/issues) with:
- Your GPU name
- PCI ID (from `python rocmfix.py`)
- The override value that worked
- What you tested it with (llama.cpp, PyTorch, etc.)

---

## 📦 Requirements

- **Python 3.10+** (that's it)
- **AMD GPU** with Adrenalin drivers installed
- No pip packages. No virtual environment. No compilation.

---

## 🗺️ Roadmap

- [ ] Auto-detect and recommend Vulkan vs HIP per GPU
- [ ] One-click HIP SDK installer for Windows
- [ ] llama.cpp backend benchmarker (Vulkan vs HIP speed comparison)
- [ ] Auto-apply override to system environment
- [ ] GUI version for non-technical users
- [ ] RDNA4 (RX 9070 series) support as cards release

---

## 📄 License

MIT — do whatever you want with it.

---

<div align="center">

**If this saved you 30 minutes of Reddit digging, drop a ⭐**

Made for the local AI community by [xanpavle](https://github.com/xanpavle)

</div>