#!/usr/bin/env python3
"""
ROCmFix v0.1.3 — Cross-platform AMD GPU override detector and tester for ROCm/HIP.
Includes full diagnostics (Doctor), HIP Auto-Installer, and Vulkan vs HIP Benchmarker.
"""

import os
import sys
import platform
import re
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import json
import argparse
import shutil
import uuid
import time
import webbrowser
from pathlib import Path
from datetime import datetime, timezone

__version__ = "0.1.3"
GITHUB_REPO = "xanpavle/rocmfix"

# ── Telemetry configuration ───────────────────────────────────────────
TELEMETRY_ENDPOINT = "https://rocmfix-data.onrender.com/submit"
TELEMETRY_TIMEOUT = 60  # Render free cold start can be slow

# ── User config paths ─────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".rocmfix"
CONFIG_FILE = CONFIG_DIR / "config.json"
OUTBOX_DIR = CONFIG_DIR / "outbox"
BACKUP_DIR = CONFIG_DIR / "backups"

# ──────────────────────────────────────────────────────────────────────
# 1. DATABASE
# ──────────────────────────────────────────────────────────────────────

GPU_DATABASE = {
    # ── RDNA 4 ──────────────────────────────
    "7550": {
        "name": "RX 9070 XT / 9070 / 9070 GRE (Navi 48)", "arch": "RDNA4", "gfx_target": "gfx1201",
        "override": None, "supported": True, "rec_backend": "vulkan",
        "known_issues": ["Requires ROCm 6.4+ or Adrenalin 25.x+ for native support", "Older ROCm may need HSA_OVERRIDE_GFX_VERSION=12.0.0"],
        "notes": "Natively supported in ROCm 6.4+. If on older ROCm, try override 12.0.0."
    },
    "7551": {
        "name": "Radeon AI PRO R9700 (Navi 48 Pro)", "arch": "RDNA4", "gfx_target": "gfx1201",
        "override": None, "supported": True, "rec_backend": "hip", "known_issues": [],
        "notes": "Enterprise variant of Navi 48. Natively supported in ROCm 6.4+."
    },
    "7590": {
        "name": "RX 9060 XT / 9060 / 9050 (Navi 44)", "arch": "RDNA4", "gfx_target": "gfx1200",
        "override": "12.0.1", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["gfx1200 support in ROCm is newer than gfx1201", "Vulkan backend recommended for now"],
        "notes": "Override to gfx1201 (12.0.1). Vulkan may be more stable currently."
    },
    # ── RDNA 3 ──────────────────────────────────────────────
    "744c": {
        "name": "RX 7900 XTX", "arch": "RDNA3", "gfx_target": "gfx1100",
        "override": None, "supported": True, "rec_backend": "hip", "known_issues": [],
        "notes": "Natively supported. No override needed."
    },
    "744e": {
        "name": "RX 7900 XT", "arch": "RDNA3", "gfx_target": "gfx1100",
        "override": None, "supported": True, "rec_backend": "hip", "known_issues": [],
        "notes": "Natively supported. No override needed."
    },
    "747e": {
        "name": "RX 7900 GRE / RX 7800 XT (variant)", "arch": "RDNA3", "gfx_target": "gfx1100",
        "override": None, "supported": True, "rec_backend": "vulkan",
        "known_issues": ["Some 7800 XT AIB models share this PCI ID but are gfx1101 internally", "If llama.cpp fails with HIP errors, try override 11.0.0"],
        "notes": "Usually natively supported. If your card is actually a 7800 XT variant and HIP fails, set override to 11.0.0."
    },
    "7470": {
        "name": "RX 7800 XT", "arch": "RDNA3", "gfx_target": "gfx1101",
        "override": "11.0.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["FP16 atomic operations may produce incorrect results", "Flash Attention 2 may fail on certain model sizes"],
        "notes": "Override to gfx1100. Works well for llama.cpp and most GGUF inference."
    },
    "7471": {
        "name": "RX 7700 XT", "arch": "RDNA3", "gfx_target": "gfx1101",
        "override": "11.0.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["FP16 atomic operations may produce incorrect results", "Some custom HIP kernels using wave32 mode may crash"],
        "notes": "Same override as 7800 XT. 12GB VRAM limits larger models."
    },
    "7480": {
        "name": "RX 7600", "arch": "RDNA3", "gfx_target": "gfx1102",
        "override": "11.0.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["FP16 atomics unreliable", "8GB VRAM severely limits model size", "Vulkan backend may outperform HIP on this card"],
        "notes": "Override to gfx1100. Consider Vulkan backend for better perf."
    },
    "7483": {
        "name": "RX 7600 XT", "arch": "RDNA3", "gfx_target": "gfx1102",
        "override": "11.0.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["FP16 atomics unreliable", "Vulkan backend may outperform HIP on this card"],
        "notes": "Override to gfx1100. 16GB VRAM is nice for the price."
    },
    # ── RDNA 2 ──────────────────────────────────────────────
    "73af": {
        "name": "RX 6900 XT", "arch": "RDNA2", "gfx_target": "gfx1030",
        "override": None, "supported": True, "rec_backend": "hip", "known_issues": [],
        "notes": "Natively supported. Best RDNA2 card for ROCm."
    },
    "73bf": {
        "name": "RX 6800 XT / 6800", "arch": "RDNA2", "gfx_target": "gfx1030",
        "override": None, "supported": True, "rec_backend": "hip", "known_issues": [],
        "notes": "Natively supported."
    },
    "73df": {
        "name": "RX 6700 XT", "arch": "RDNA2", "gfx_target": "gfx1031",
        "override": "10.3.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["Matrix core operations may be slower than native gfx1030", "Some BLAS operations produce NaN with FP16"],
        "notes": "Override to gfx1030. 12GB VRAM is decent for 7B-13B models."
    },
    "73ff": {
        "name": "RX 6600 XT / 6600", "arch": "RDNA2", "gfx_target": "gfx1032",
        "override": "10.3.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["Significantly slower than gfx1030 native cards", "8GB VRAM limits model size", "Vulkan often faster than HIP on this chip"],
        "notes": "Override to gfx1030. Strongly consider Vulkan backend."
    },
    "743f": {
        "name": "RX 6500 XT", "arch": "RDNA2", "gfx_target": "gfx1034",
        "override": "10.3.0", "supported": False, "rec_backend": "vulkan",
        "known_issues": ["4GB VRAM barely usable for LLM inference", "PCIe x4 bus severely limits CPU to GPU transfer"],
        "notes": "Override to gfx1030. Honestly barely worth it for AI."
    },
    # ── INTEGRATED GPUs ─────────────────────────────────────
    "164e": {
        "name": "Radeon Graphics (Ryzen 7000 iGPU)", "arch": "RDNA2", "gfx_target": "gfx1036",
        "override": None, "supported": False, "rec_backend": "cpu",
        "known_issues": ["Integrated GPU — shares system RAM, no dedicated VRAM", "Not usable for ROCm/HIP inference"],
        "notes": "Built-in GPU in Ryzen 7000 CPUs. Ignore for AI — use your dedicated GPU."
    },
    "1638": {
        "name": "Radeon Graphics (Ryzen 5000 iGPU)", "arch": "Vega", "gfx_target": "gfx90c",
        "override": None, "supported": False, "rec_backend": "cpu",
        "known_issues": ["Integrated GPU — not usable for ROCm inference"],
        "notes": "Built-in Vega iGPU in Ryzen 5000G APUs. Ignore for AI."
    },
}

# ──────────────────────────────────────────────────────────────────────
# 2. COLOR / TERMINAL UTILITIES
# ──────────────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"

def enable_ansi():
    if platform.system().lower() == "windows":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetConsoleMode(k32.GetStdHandle(-11), 7)
        except Exception:
            for attr in dir(C):
                if not attr.startswith("_") and attr != "RESET":
                    setattr(C, attr, "")
            C.RESET = ""

# ──────────────────────────────────────────────────────────────────────
# 3. CONFIG MANAGEMENT
# ──────────────────────────────────────────────────────────────────────

def _ensure_config_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    _ensure_config_dirs()
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "user_id": str(uuid.uuid4()),
        "telemetry_enabled": None,
        "first_run": True,
        "installed_globally": False,
        "applied_overrides": [],
    }

def save_config(cfg: dict):
    _ensure_config_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

# ──────────────────────────────────────────────────────────────────────
# 4. DETECTION LOGIC
# ──────────────────────────────────────────────────────────────────────

def _run(cmd, shell=False):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=shell, timeout=10)
        return result.stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""

def detect_os() -> str:
    system = platform.system().lower()
    if system == "windows": return "windows"
    if system == "linux": return "linux"
    if system == "darwin": return "macos"
    return "unknown"

def detect_shell() -> str:
    os_name = detect_os()
    if os_name == "windows":
        if os.environ.get("PROMPT") and not os.environ.get("PSExecutionPolicyPreference"):
            return "cmd"
        if os.environ.get("PSModulePath"):
            return "powershell"
        return "cmd"
    shell_env = os.environ.get("SHELL", "")
    if "fish" in shell_env: return "fish"
    if "zsh" in shell_env: return "zsh"
    return "bash"

def _detect_windows_registry() -> list[dict]:
    gpus = []
    try: import winreg
    except ImportError: return gpus
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as class_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(class_key, i)
                    i += 1
                    if subkey_name == "Properties": continue
                    with winreg.OpenKey(class_key, subkey_name) as subkey:
                        try: desc = winreg.QueryValueEx(subkey, "DriverDesc")[0]
                        except FileNotFoundError: continue
                        if not any(v in desc.lower() for v in ["amd", "radeon", "ati"]): continue
                        pci_id, raw_pnp = "unknown", desc
                        try:
                            matching_id = winreg.QueryValueEx(subkey, "MatchingDeviceId")[0]
                            raw_pnp = matching_id
                            match = re.search(r"VEN_1002&DEV_([0-9A-Fa-f]{4})", matching_id)
                            if match: pci_id = match.group(1).lower()
                        except FileNotFoundError: pass
                        driver = "unknown"
                        try: driver = winreg.QueryValueEx(subkey, "DriverVersion")[0]
                        except FileNotFoundError: pass
                        gpus.append({"pci_id": pci_id, "name": desc, "driver_version": driver, "raw_pnp": raw_pnp})
                except OSError: break
    except OSError: pass
    return gpus

def _detect_linux() -> list[dict]:
    gpus = []
    output = _run(["lspci", "-nn", "-d", "1002::"])
    pattern = re.compile(r"\[1002:([0-9a-fA-F]{4})\]")
    for line in output.splitlines():
        match = pattern.search(line)
        if match and any(kw in line for kw in ["VGA", "Display", "3D"]):
            pci_id = match.group(1).lower()
            name_match = re.search(r"\]:\s*(.+?)\s*\[1002:", line)
            name = name_match.group(1) if name_match else "Unknown AMD GPU"
            driver = "unknown"
            ver_file = Path("/sys/module/amdgpu/version")
            if ver_file.exists():
                try: driver = ver_file.read_text().strip()
                except OSError: pass
            gpus.append({"pci_id": pci_id, "name": name, "driver_version": driver, "raw_pnp": line.strip()})
    return gpus

def detect_gpus() -> list[dict]:
    if detect_os() == "windows": return _detect_windows_registry()
    if detect_os() == "linux": return _detect_linux()
    return []

def detect_rocm_version():
    os_name = detect_os()
    if os_name == "linux":
        version_file = Path("/opt/rocm/.info/version")
        if version_file.exists():
            try: return version_file.read_text().strip()
            except OSError: pass
        output = _run(["rocminfo"])
        match = re.search(r"ROCm Version:\s*([\d.]+)", output)
        if match: return match.group(1)
    elif os_name == "windows":
        hip_path = os.environ.get("HIP_PATH", "")
        if hip_path:
            match = re.search(r"[\\\/](\d+\.\d+)[\\\/]?$", hip_path.rstrip("\\/"))
            if match: return match.group(1)
    return None

def analyze_driver(drv_ver: str) -> dict | None:
    """Analyzes Windows Adrenalin driver versions."""
    if not drv_ver or drv_ver == "unknown": return None
    try:
        major = int(drv_ver.split('.')[0])
        if major >= 32: return {"status": "ok", "msg": "Adrenalin 24.x+ (Good)"}
        elif major == 31: return {"status": "warn", "msg": "Adrenalin 23.x (Update recommended for ROCm 6)"}
        else: return {"status": "bad", "msg": f"Very old driver ({major}.x). ROCm/HIP will likely fail."}
    except Exception:
        return None

# ──────────────────────────────────────────────────────────────────────
# 5. SMOKE TESTING
# ──────────────────────────────────────────────────────────────────────

def run_smoke_test(override):
    os_name = detect_os()
    env = os.environ.copy()
    if override: env["HSA_OVERRIDE_GFX_VERSION"] = override
    else: env.pop("HSA_OVERRIDE_GFX_VERSION", None)

    if os_name == "linux":
        try:
            res = subprocess.run(["rocminfo"], capture_output=True, text=True, env=env, timeout=10)
            if res.returncode != 0 or "Agent" not in res.stdout:
                return {"success": False, "message": "rocminfo failed to find a GPU agent.", "details": (res.stderr or res.stdout)[:300]}
            gpu_count = res.stdout.count("Device Type:                     GPU")
            if gpu_count == 0:
                return {"success": False, "message": "0 GPU agents found.", "details": ""}
            return {"success": True, "message": f"ROCm detected {gpu_count} GPU agent(s).", "details": f"override={override or 'not set'}"}
        except FileNotFoundError:
            return {"success": False, "message": "rocminfo not found. Is ROCm installed?", "details": ""}
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}

    elif os_name == "windows":
        hip_path = os.environ.get("HIP_PATH", "")
        if not hip_path:
            return {"success": False, "message": "HIP_PATH not set. Install the HIP SDK.", "details": "Run 'rocmfix install-hip'"}
        hipinfo = os.path.join(hip_path, "bin", "hipInfo.exe")
        if not os.path.exists(hipinfo):
            return {"success": False, "message": "hipInfo.exe not found.", "details": f"Looked in: {hip_path}\\bin\\"}
        try:
            res = subprocess.run([hipinfo], capture_output=True, text=True, env=env, timeout=10)
            if res.returncode != 0:
                return {"success": False, "message": "hipInfo returned error.", "details": (res.stderr or res.stdout)[:300]}
            if "device" not in res.stdout.lower():
                return {"success": False, "message": "No devices found.", "details": res.stdout[:300]}
            return {"success": True, "message": "HIP SDK detected your GPU.", "details": f"override={override or 'not set'}"}
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}

    return {"success": False, "message": "Unsupported OS", "details": ""}

# ──────────────────────────────────────────────────────────────────────
# 6. AUTO-APPLY OVERRIDE
# ──────────────────────────────────────────────────────────────────────

def _backup_file(path: Path) -> Path:
    _ensure_config_dirs()
    if not path.exists(): return None
    backup_path = BACKUP_DIR / f"{path.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    shutil.copy2(path, backup_path)
    return backup_path

def apply_override_windows(override: str) -> dict:
    try:
        import winreg, ctypes
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        try: old_value, _ = winreg.QueryValueEx(key, "HSA_OVERRIDE_GFX_VERSION")
        except FileNotFoundError: old_value = None
        winreg.SetValueEx(key, "HSA_OVERRIDE_GFX_VERSION", 0, winreg.REG_SZ, override)
        winreg.CloseKey(key)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
        return {"success": True, "old_value": old_value, "method": "windows_registry"}
    except Exception as e: return {"success": False, "error": str(e)}

def apply_override_unix(override: str, shell: str) -> dict:
    home = Path.home()
    if shell == "fish": config_path, line = home / ".config" / "fish" / "config.fish", f'set -gx HSA_OVERRIDE_GFX_VERSION {override}\n'
    elif shell == "zsh": config_path, line = home / ".zshrc", f'export HSA_OVERRIDE_GFX_VERSION={override}\n'
    else: config_path, line = home / ".bashrc", f'export HSA_OVERRIDE_GFX_VERSION={override}\n'
    marker = "# Added by ROCmFix"
    try:
        backup = _backup_file(config_path) if config_path.exists() else None
        existing = config_path.read_text() if config_path.exists() else ""
        cleaned = [l for l in existing.splitlines(keepends=True) if marker not in l and "HSA_OVERRIDE_GFX_VERSION" not in l]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            f.writelines(cleaned)
            if cleaned and not cleaned[-1].endswith("\n"): f.write("\n")
            f.write(f"\n{line.rstrip()}  {marker}\n")
        return {"success": True, "backup": str(backup) if backup else None, "file": str(config_path), "method": f"unix_{shell}"}
    except Exception as e: return {"success": False, "error": str(e)}

def apply_override(override: str) -> dict:
    result = apply_override_windows(override) if detect_os() == "windows" else apply_override_unix(override, detect_shell())
    if result.get("success"):
        cfg = load_config()
        cfg["applied_overrides"].append({"value": override, "timestamp": datetime.now(timezone.utc).isoformat(), "method": result.get("method"), "backup": result.get("backup")})
        save_config(cfg)
    return result

def undo_last_override() -> dict:
    cfg = load_config()
    if not cfg["applied_overrides"]: return {"success": False, "error": "No previous overrides to undo."}
    last = cfg["applied_overrides"][-1]
    
    if detect_os() == "windows":
        try:
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            if last.get("old_value"): winreg.SetValueEx(key, "HSA_OVERRIDE_GFX_VERSION", 0, winreg.REG_SZ, last["old_value"])
            else:
                try: winreg.DeleteValue(key, "HSA_OVERRIDE_GFX_VERSION")
                except FileNotFoundError: pass
            winreg.CloseKey(key)
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
            cfg["applied_overrides"].pop()
            save_config(cfg)
            return {"success": True, "method": "windows_registry"}
        except Exception as e: return {"success": False, "error": str(e)}
    else:
        backup = last.get("backup")
        if backup and Path(backup).exists():
            try:
                original_name = Path(backup).name.split(".")[0]
                target = Path.home() / f".{original_name}" if not original_name.startswith(".") else Path.home() / original_name
                shutil.copy2(backup, target)
                cfg["applied_overrides"].pop()
                save_config(cfg)
                return {"success": True, "restored_from": backup}
            except Exception as e: return {"success": False, "error": str(e)}
        return {"success": False, "error": "No backup file found."}

# ──────────────────────────────────────────────────────────────────────
# 7. TELEMETRY
# ──────────────────────────────────────────────────────────────────────

def build_telemetry_payload(gpu: dict, info: dict, rocm_ver, smoke_result=None) -> dict:
    cfg = load_config()
    return {
        "schema_version": 1, "user_id": cfg["user_id"], "rocmfix_version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(), "os": detect_os(), "shell": detect_shell(),
        "gpu": {"name": gpu.get("name"), "pci_id": gpu.get("pci_id"), "driver_version": gpu.get("driver_version")},
        "database": {"in_database": info is not None, "override_recommended": info.get("override") if info else None, "supported_native": info.get("supported") if info else None},
        "rocm_version": rocm_ver, "smoke_test": smoke_result if smoke_result else None
    }

def _queue_payload(payload: dict):
    _ensure_config_dirs()
    (OUTBOX_DIR / f"payload_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.json").write_text(json.dumps(payload))

def _send_payload(payload: dict) -> bool:
    if "YOURNAME" in TELEMETRY_ENDPOINT or "yourname" in TELEMETRY_ENDPOINT: return False
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(TELEMETRY_ENDPOINT, data=data, headers={"Content-Type": "application/json", "User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=TELEMETRY_TIMEOUT) as resp: return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError): return False

def flush_outbox():
    if not OUTBOX_DIR.exists(): return 0, 0
    sent, failed = 0, 0
    for file in sorted(OUTBOX_DIR.glob("*.json")):
        try:
            if _send_payload(json.loads(file.read_text())):
                file.unlink()
                sent += 1
            else: failed += 1
        except (json.JSONDecodeError, OSError): file.unlink()
    return sent, failed

def send_telemetry(payload: dict):
    cfg = load_config()
    if not cfg.get("telemetry_enabled"): return
    if not _send_payload(payload): _queue_payload(payload)
    try: flush_outbox()
    except Exception: pass

def show_telemetry_sample(gpu, info, rocm_ver):
    payload = build_telemetry_payload(gpu, info, rocm_ver)
    print(f"\n  {C.BOLD}Exact JSON that would be sent:{C.RESET}")
    print(f"  {C.GRAY}{'─' * 60}{C.RESET}")
    for line in json.dumps(payload, indent=2).splitlines(): print(f"    {line}")
    print(f"  {C.GRAY}{'─' * 60}{C.RESET}")
    print(f"  {C.GRAY}Endpoint: {TELEMETRY_ENDPOINT}{C.RESET}\n")

def prompt_telemetry_optin() -> bool:
    print(f"\n{C.BOLD}{C.CYAN}📊 Help improve ROCmFix?{C.RESET}\n")
    print("  ROCmFix can anonymously share your GPU detection results")
    print("  with the community database. This helps:")
    print(f"    {C.GREEN}•{C.RESET} Add new GPUs to the database faster")
    print(f"    {C.GREEN}•{C.RESET} Detect driver bugs and regressions")
    print(f"    {C.GREEN}•{C.RESET} Improve override recommendations\n")
    print(f"  {C.BOLD}What gets sent:{C.RESET} GPU model, PCI ID, driver, OS, override value")
    print(f"  {C.BOLD}What does NOT get sent:{C.RESET} Username, IP, file paths, personal data\n")
    print(f"  {C.GRAY}Data goes to a private server. See github.com/{GITHUB_REPO}/blob/main/PRIVACY.md{C.RESET}\n")
    print(f"    [{C.GREEN}Y{C.RESET}] Yes, share anonymous data")
    print(f"    [{C.RED}N{C.RESET}] No, keep everything local")
    print(f"    [{C.CYAN}?{C.RESET}] Show me exactly what gets sent\n")

    while True:
        try: ans = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt): return False
        if ans == "y": return True
        if ans == "n": return False
        if ans == "?":
            gpus = detect_gpus()
            if gpus: show_telemetry_sample(gpus[0], GPU_DATABASE.get(gpus[0]["pci_id"]), detect_rocm_version())
            print(f"    [{C.GREEN}Y{C.RESET}] Yes  [{C.RED}N{C.RESET}] No")

# ──────────────────────────────────────────────────────────────────────
# 8. GLOBAL INSTALLER & HELPERS
# ──────────────────────────────────────────────────────────────────────

def is_installed_globally() -> bool: return shutil.which("rocmfix") is not None

def install_globally() -> bool:
    os_name, script_path, script_dir = detect_os(), Path(__file__).resolve(), Path(__file__).resolve().parent
    print(f"\n{C.BOLD}{C.CYAN}⚙️  Register 'rocmfix' as a global command?{C.RESET}")
    print("  This lets you run 'rocmfix' from any folder.\n")
    try:
        if input("  Install globally? [Y/n]: ").strip().lower() == 'n': return False
    except: return False

    if os_name == "windows":
        try:
            (script_dir / "rocmfix.bat").write_text(f'@echo off\npython "{script_path}" %*\n')
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            try: path_val, _ = winreg.QueryValueEx(key, "Path")
            except: path_val = ""
            if str(script_dir) not in path_val:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, f"{path_val};{script_dir}" if path_val else str(script_dir))
                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
            winreg.CloseKey(key)
            print(f"\n  {C.GREEN}✓ Registered! Open a NEW terminal and run: rocmfix{C.RESET}\n")
            return True
        except Exception as e: print(f"{C.RED}Registry write failed: {e}{C.RESET}"); return False
    else:
        try:
            local_bin = Path.home() / ".local" / "bin"
            local_bin.mkdir(parents=True, exist_ok=True)
            dest = local_bin / "rocmfix"
            shutil.copy2(script_path, dest)
            dest.chmod(0o755)
            print(f"\n  {C.GREEN}✓ Installed to {dest}{C.RESET}")
            if str(local_bin) not in os.environ.get("PATH", ""): print(f"  {C.YELLOW}⚠ Add to PATH: export PATH=\"$HOME/.local/bin:$PATH\"{C.RESET}\n")
            return True
        except Exception as e: print(f"{C.RED}Copy failed: {e}{C.RESET}"); return False

def format_env_command(override: str) -> str:
    shell = detect_shell()
    if detect_os() == "windows":
        return f'  $env:HSA_OVERRIDE_GFX_VERSION="{override}"' if shell == "powershell" else f"  set HSA_OVERRIDE_GFX_VERSION={override}"
    return f"  set -gx HSA_OVERRIDE_GFX_VERSION {override}" if shell == "fish" else f"  export HSA_OVERRIDE_GFX_VERSION={override}"

def get_issue_url(gpu, rocm_ver):
    body = f"## Unknown GPU\n\n- **Name:** {gpu.get('name')}\n- **PCI ID:** `{gpu.get('pci_id')}`\n- **Driver:** {gpu.get('driver_version')}\n- **ROCm:** {rocm_ver or 'not detected'}\n- **OS:** {detect_os()}\n"
    params = urllib.parse.urlencode({"title": f"[GPU Report] Unknown AMD GPU: {gpu.get('name','?')} ({gpu.get('pci_id','?')})", "body": body, "labels": "unknown-gpu"})
    return f"https://github.com/{GITHUB_REPO}/issues/new?{params}"

def print_header():
    print(f"{C.BOLD}{C.CYAN}\n  ╔══════════════════════════════════════╗")
    print(f"  ║           ROCmFix v{__version__.ljust(18)}║")
    print(f"  ║   AMD GPU override helper for ROCm   ║")
    print(f"  ╚══════════════════════════════════════╝\n{C.RESET}")

# ──────────────────────────────────────────────────────────────────────
# 9. CLI COMMANDS
# ──────────────────────────────────────────────────────────────────────

def cmd_detect(args):
    print_header()
    cfg = load_config()
    if cfg.get("first_run") and not is_installed_globally():
        if install_globally(): cfg["installed_globally"] = True
    if cfg.get("telemetry_enabled") is None:
        cfg["telemetry_enabled"] = prompt_telemetry_optin()
    cfg["first_run"] = False
    save_config(cfg)

    if cfg["telemetry_enabled"]:
        try:
            sent, failed = flush_outbox()
            if sent > 0: print(f"  {C.GRAY}[Telemetry: sent {sent} queued reports]{C.RESET}\n")
        except: pass

    os_name, shell, rocm_ver = detect_os(), detect_shell(), detect_rocm_version()
    print(f"{C.BOLD}System Info:{C.RESET}")
    print(f"  OS:       {os_name}\n  Shell:    {shell}\n  ROCm/HIP: {rocm_ver or f'{C.YELLOW}Not detected{C.RESET}'}\n")

    gpus = detect_gpus()
    if not gpus:
        print(f"{C.RED}No AMD GPUs detected.{C.RESET}")
        sys.exit(1)

    print(f"{C.BOLD}Detected {len(gpus)} AMD GPU(s):{C.RESET}")
    for i, gpu in enumerate(gpus, 1):
        print(f"\n  [{i}] {C.BOLD}{gpu['name']}{C.RESET}")
        print(f"      PCI ID:  {gpu['pci_id']}\n      Driver:  {gpu['driver_version']}")

        info = GPU_DATABASE.get(gpu["pci_id"])
        if cfg["telemetry_enabled"]: send_telemetry(build_telemetry_payload(gpu, info, rocm_ver))

        if not info:
            print(f"      Status:  {C.YELLOW}UNKNOWN — not in database{C.RESET}")
            print(f"\n      Help us add it:\n      {C.BLUE}{get_issue_url(gpu, rocm_ver)}{C.RESET}")
            continue

        if info.get("override") is None and not info.get("supported"):
            print(f"      Status:  {C.YELLOW}Not useful for ROCm AI{C.RESET}\n      Notes:   {info['notes']}")
            continue

        if info["supported"]:
            print(f"      Status:  {C.GREEN}Natively supported{C.RESET}\n      Notes:   {info['notes']}")
            continue

        print(f"      Status:  {C.YELLOW}Needs override{C.RESET}")
        print(f"      gfx target: {info['gfx_target']} → override {info['override']}")
        if info["known_issues"]:
            print(f"      {C.RED}Known issues:{C.RESET}")
            for issue in info["known_issues"]: print(f"        - {issue}")

        print(f"\n      {C.BOLD}Apply this override?{C.RESET}")
        print(f"        [{C.GREEN}A{C.RESET}] Apply automatically (modifies your shell config)")
        print(f"        [{C.CYAN}S{C.RESET}] Show me the command to paste manually")
        print(f"        [{C.GRAY}N{C.RESET}] Skip")

        try: choice = input("      > ").strip().lower()
        except: choice = "n"

        if choice == "a":
            res = apply_override(info["override"])
            if res["success"]:
                print(f"\n      {C.GREEN}✓ Override applied!{C.RESET}")
                print(f"      {C.YELLOW}⚠ Open a NEW terminal for it to take effect.{C.RESET}")
            else:
                print(f"\n      {C.RED}✗ Failed: {res.get('error')}{C.RESET}\n      Fall back to manual command:\n{format_env_command(info['override'])}")
        elif choice == "s":
            print(f"\n      {C.BOLD}Run this in your {shell}:{C.RESET}\n{format_env_command(info['override'])}")
        else: print(f"      {C.GRAY}Skipped.{C.RESET}")

def cmd_doctor(args):
    print_header()
    print(f"{C.BOLD}{C.CYAN}🩺 ROCmFix System Doctor{C.RESET}\n")
    gpus = detect_gpus()
    
    print(f"{C.BOLD}1. Hardware & Drivers{C.RESET}")
    if not gpus: print(f"  {C.RED}✗ No AMD GPUs detected{C.RESET}")
    for g in gpus:
        print(f"  {C.GREEN}✓ GPU:{C.RESET} {g['name']} (PCI: {g['pci_id']})")
        drv_eval = analyze_driver(g['driver_version'])
        if drv_eval:
            color = C.GREEN if drv_eval["status"]=="ok" else C.YELLOW if drv_eval["status"]=="warn" else C.RED
            icon = "✓" if drv_eval["status"]=="ok" else "⚠" if drv_eval["status"]=="warn" else "✗"
            print(f"  {color}{icon} Driver:{C.RESET} {g['driver_version']} — {drv_eval['msg']}")
        else: print(f"  {C.GRAY}• Driver:{C.RESET} {g['driver_version']}")

    print(f"\n{C.BOLD}2. ROCm / HIP Engine{C.RESET}")
    if detect_os() == "windows":
        hip = os.environ.get("HIP_PATH")
        if hip and Path(hip).exists(): print(f"  {C.GREEN}✓ HIP SDK Installed:{C.RESET} {hip}")
        else:
            print(f"  {C.RED}✗ HIP SDK MISSING{C.RESET}")
            print(f"    {C.GRAY}AI programs will fall back to CPU or Vulkan.{C.RESET}")
            print(f"    {C.CYAN}Run: rocmfix install-hip{C.RESET} to install it automatically.")
    else:
        rocm = detect_rocm_version()
        if rocm: print(f"  {C.GREEN}✓ ROCm Installed:{C.RESET} v{rocm}")
        else: print(f"  {C.RED}✗ ROCm NOT FOUND{C.RESET}")

    print(f"\n{C.BOLD}3. Vulkan Engine{C.RESET}")
    vk = _run(["vulkaninfo", "--summary"])
    if "Vulkan Instance Version" in vk or "devices" in vk.lower(): print(f"  {C.GREEN}✓ Vulkan API ready{C.RESET}")
    else: print(f"  {C.YELLOW}⚠ Vulkan not responding (vulkaninfo failed){C.RESET}")

    print(f"\n{C.BOLD}4. Environment Override{C.RESET}")
    ov = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    if ov: print(f"  {C.GREEN}✓ Set to:{C.RESET} {ov}")
    else: print(f"  {C.GRAY}• Not currently set in this session.{C.RESET}")

    print(f"\n{C.BOLD}Recommendation:{C.RESET}")
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        if info:
            print(f"  For {g['name']}: Use {C.BOLD}{info['rec_backend'].upper()}{C.RESET} backend.")
            if info['override']: print(f"  Requires HSA_OVERRIDE_GFX_VERSION={info['override']} for HIP.")
    print()

def cmd_install_hip(args):
    print_header()
    if detect_os() != "windows":
        print(f"  {C.RED}This command is only for Windows.{C.RESET}")
        return
    print(f"{C.BOLD}{C.CYAN}📥 Windows HIP SDK Auto-Installer{C.RESET}\n")
    
    if os.environ.get("HIP_PATH"):
        print(f"  {C.GREEN}✓ HIP SDK is already installed at {os.environ.get('HIP_PATH')}{C.RESET}")
        return
    
    # List candidate AMD direct links
    candidates = [
        "https://download.amd.com/developer/eula/rocm-hub/HIP-SDK-Inst-Win-v6.1.2.exe",
        "https://download.amd.com/developer/eula/rocm-hub/HIP-SDK-Inst-Win-v6.1.0.exe",
        "https://download.amd.com/developer/eula/rocm-hub/HIP-SDK-Inst-Win-v6.2.0.exe",
    ]
    official_hub_page = "https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html"
    installer_path = Path(os.environ.get("TEMP", ".")) / "HIP-SDK-Installer.exe"

    print("  This will download the official AMD HIP SDK (~1.2 GB).")
    print("  It provides full ROCm/PyTorch capability on Windows.\n")
    
    try:
        ans = input("  Proceed with download? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans == 'n': return

    print(f"\n  {C.GRAY}Connecting to AMD servers...{C.RESET}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.amd.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }

    download_success = False
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp, open(installer_path, 'wb') as f:
                total = int(resp.headers.get('content-length', 0))
                downloaded = 0
                while chunk := resp.read(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int((downloaded / total) * 100)
                        sys.stdout.write(f"\r  Downloading: [{('='*(pct//2)).ljust(50)}] {pct}% ({downloaded//1024//1024} MB)")
                        sys.stdout.flush()
            download_success = True
            print(f"\n\n  {C.GREEN}✓ Download complete!{C.RESET}")
            break
        except Exception:
            continue

    if download_success:
        print(f"  {C.CYAN}Launching AMD Installer GUI... Please follow the prompts to install.{C.RESET}")
        try:
            os.startfile(installer_path)
            print(f"\n  {C.YELLOW}⚠ IMPORTANT: After the AMD installer finishes, RESTART YOUR PC.{C.RESET}\n")
        except Exception as e:
            print(f"  {C.RED}Failed to launch installer automatically: {e}{C.RESET}")
    else:
        print(f"\n  {C.YELLOW}⚠ Direct CDN download was blocked or updated by AMD.{C.RESET}")
        print(f"  {C.CYAN}Opening the official AMD HIP SDK download page in your browser...{C.RESET}\n")
        try:
            webbrowser.open(official_hub_page)
            print(f"  {C.GREEN}✓ Web browser opened.{C.RESET} Download the installer from AMD and run it.")
            print(f"  Direct Link: {official_hub_page}\n")
        except Exception as e:
            print(f"  Manual Download URL: {official_hub_page}\n")

def cmd_bench(args):
    print_header()
    print(f"{C.BOLD}{C.CYAN}🏎️  Vulkan vs HIP Quick Benchmark (via Ollama){C.RESET}\n")
    
    ollama_check = _run(["ollama", "--version"])
    if "ollama version" not in ollama_check:
        print(f"  {C.RED}✗ Ollama not found in PATH.{C.RESET}")
        print("  This 10-second benchmark requires Ollama. Install it from https://ollama.com")
        return

    models_out = _run(["ollama", "list"])
    models = [line.split()[0] for line in models_out.splitlines()[1:] if line.strip()]
    if not models:
        print(f"  {C.YELLOW}No models found in Ollama. Pulling 'qwen2.5:0.5b' (~400MB) for testing...{C.RESET}")
        subprocess.run(["ollama", "pull", "qwen2.5:0.5b"])
        models = ["qwen2.5:0.5b"]
    
    model = models[0]
    prompt = "Write a 50 word story about a robot learning to paint."
    print(f"  {C.GRAY}Using model: {model}{C.RESET}")
    print(f"  {C.GRAY}Testing prompt generation speed...{C.RESET}\n")

    results = {}
    for backend in ["vulkan", "rocm"]:
        print(f"  Testing {C.BOLD}{backend.upper()}{C.RESET} backend...")
        print(f"    {C.GRAY}(Restarting local Ollama server to force {backend}...){C.RESET}")
        if detect_os() == "windows":
            _run(["taskkill", "/F", "/IM", "ollama_app.exe"])
            _run(["taskkill", "/F", "/IM", "ollama.exe"])
        else:
            _run(["pkill", "-9", "ollama"])
            
        time.sleep(1)
        
        env = os.environ.copy()
        env["OLLAMA_GPU_BACKEND"] = backend
        gpus = detect_gpus()
        if gpus and GPU_DATABASE.get(gpus[0]["pci_id"], {}).get("override"):
            env["HSA_OVERRIDE_GFX_VERSION"] = GPU_DATABASE[gpus[0]["pci_id"]]["override"]

        server_proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(4) # Wait for server boot

        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", 
                data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
                headers={"Content-Type": "application/json"})
            start = time.time()
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
                tok_s = data.get("eval_count", 0) / (data.get("eval_duration", 1) / 1e9)
                results[backend] = round(tok_s, 2)
                print(f"    {C.GREEN}→ {results[backend]} tokens/sec{C.RESET}")
        except Exception as e:
            print(f"    {C.RED}→ Failed to run: {e}{C.RESET}")
            results[backend] = 0

        server_proc.kill()

    print(f"\n{C.BOLD}🏆 Benchmark Results:{C.RESET}")
    vk = results.get("vulkan", 0)
    rocm = results.get("rocm", 0)
    print(f"  Vulkan: {vk} tok/s")
    print(f"  HIP/ROCm: {rocm} tok/s")
    
    if vk > rocm and vk > 0:
        print(f"\n  {C.GREEN}Winner: VULKAN (+{int((vk/rocm - 1)*100 if rocm else 0)}%){C.RESET}")
        print("  Set OLLAMA_GPU_BACKEND=vulkan in your environment variables for best speed.")
    elif rocm > vk and rocm > 0:
        print(f"\n  {C.GREEN}Winner: HIP/ROCm (+{int((rocm/vk - 1)*100 if vk else 0)}%){C.RESET}")
    else:
        print(f"\n  {C.YELLOW}Could not determine a clear winner.{C.RESET}")

def cmd_test(args):
    print_header()
    current = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    print(f"  HSA_OVERRIDE_GFX_VERSION = {C.BOLD}{current or 'NOT SET'}{C.RESET}\n")
    print("  Running smoke test...\n")
    result = run_smoke_test(current)
    if result["success"]: print(f"  {C.GREEN}PASS: {result['message']}{C.RESET}")
    else:
        print(f"  {C.RED}FAIL: {result['message']}{C.RESET}")
        if result["details"]: print(f"  {C.GRAY}{result['details']}{C.RESET}")

    cfg = load_config()
    if cfg.get("telemetry_enabled"):
        gpus, rocm_ver = detect_gpus(), detect_rocm_version()
        for gpu in gpus: send_telemetry(build_telemetry_payload(gpu, GPU_DATABASE.get(gpu["pci_id"]), rocm_ver, result))

def cmd_list(args):
    print_header()
    print(f"  {C.BOLD}Known GPUs ({len(GPU_DATABASE)}):{C.RESET}\n")
    print(f"  {'PCI':<6} {'Name':<40} {'Arch':<8} {'Override':<10} Status")
    print(f"  {'-'*6} {'-'*40} {'-'*8} {'-'*10} {'-'*6}")
    for pid, info in GPU_DATABASE.items():
        st = f"{C.GREEN}Native{C.RESET}" if info["supported"] else f"{C.YELLOW}Override{C.RESET}"
        ov = info["override"] or "-"
        print(f"  {pid:<6} {info['name']:<40} {info['arch']:<8} {ov:<10} {st}")

def cmd_undo(args):
    print_header()
    print(f"  {C.BOLD}Undoing last override...{C.RESET}\n")
    result = undo_last_override()
    if result["success"]:
        print(f"  {C.GREEN}✓ Reverted.{C.RESET}")
        if result.get("restored_from"): print(f"  {C.GRAY}Restored from: {result['restored_from']}{C.RESET}")
        print(f"  {C.YELLOW}⚠ Open a NEW terminal for changes to take effect.{C.RESET}")
    else: print(f"  {C.RED}✗ {result['error']}{C.RESET}")

def cmd_telemetry(args):
    print_header()
    cfg = load_config()
    print(f"  {C.BOLD}Telemetry status:{C.RESET} {C.GREEN if cfg.get('telemetry_enabled') else C.RED}{'ENABLED' if cfg.get('telemetry_enabled') else 'DISABLED'}{C.RESET}\n")
    print(f"  Queued reports: {len(list(OUTBOX_DIR.glob('*.json'))) if OUTBOX_DIR.exists() else 0}")
    print(f"  Endpoint: {TELEMETRY_ENDPOINT}\n")
    print(f"  {C.BOLD}Options:{C.RESET}")
    print("    [E] Enable telemetry\n    [D] Disable telemetry\n    [F] Force flush queue now\n    [S] Show sample payload\n    [Q] Quit")
    try: choice = input("\n  > ").strip().lower()
    except: return
    if choice == "e": cfg["telemetry_enabled"] = True; save_config(cfg); print(f"  {C.GREEN}✓ Enabled{C.RESET}")
    elif choice == "d": cfg["telemetry_enabled"] = False; save_config(cfg); print(f"  {C.YELLOW}Disabled{C.RESET}")
    elif choice == "f": sent, failed = flush_outbox(); print(f"  Sent: {sent}, Failed: {failed}")
    elif choice == "s":
        gpus = detect_gpus()
        if gpus: show_telemetry_sample(gpus[0], GPU_DATABASE.get(gpus[0]["pci_id"]), detect_rocm_version())

def cmd_verify(args):
    print_header()
    print("  Verify a specific PCI ID + override.\n")
    pci_id = input("  PCI ID (4 hex): ").strip().lower()
    override = input("  Override (e.g. 11.0.0): ").strip()
    info = GPU_DATABASE.get(pci_id)
    if info: print(f"\n  {C.YELLOW}Already in DB:{C.RESET} {info['name']} (override: {info['override']})")
    print(f"\n  Running smoke test with override={override}...\n")
    result = run_smoke_test(override or None)
    if result["success"]: print(f"  {C.GREEN}✓ PASS: {result['message']}{C.RESET}")
    else: print(f"  {C.RED}✗ FAIL: {result['message']}{C.RESET}")

def cmd_contribute(args):
    print_header()
    pci_id = input("  PCI ID: ").strip().lower()
    name = input("  GPU Name: ").strip()
    arch = input("  Arch (RDNA4/RDNA3/RDNA2/Vega): ").strip()
    gfx = input("  gfx target: ").strip()
    ov = input("  Override (blank=none): ").strip()
    notes = input("  Notes: ").strip()
    print(f'\n    "{pci_id}": {{')
    print(f'        "name": "{name}", "arch": "{arch}",')
    print(f'        "gfx_target": "{gfx}",')
    print(f'        "override": {"\"" + ov + "\"" if ov else "None"},')
    print(f'        "supported": {ov == ""},')
    print(f'        "known_issues": [], "notes": "{notes}", "rec_backend": "vulkan"\n    }},')

def main():
    enable_ansi()
    parser = argparse.ArgumentParser(prog="rocmfix", description="AMD GPU override helper")
    parser.add_argument("--version", action="version", version=f"ROCmFix {__version__}")
    parser.add_argument("command", nargs="?", default="detect",
                        choices=["detect", "test", "list", "contribute", "install", "verify", "undo", "telemetry", "doctor", "install-hip", "bench"])
    args = parser.parse_args()

    dispatch = {
        "detect": cmd_detect, "test": cmd_test, "list": cmd_list, "verify": cmd_verify,
        "contribute": cmd_contribute, "install": lambda a: install_globally(),
        "undo": cmd_undo, "telemetry": cmd_telemetry, "doctor": cmd_doctor,
        "install-hip": cmd_install_hip, "bench": cmd_bench
    }
    dispatch[args.command](args)

if __name__ == "__main__":
    main()
