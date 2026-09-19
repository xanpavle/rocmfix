#!/usr/bin/env python3
"""
ROCmFix v0.1.5 — Cross-platform AMD GPU override detector and tester for ROCm/HIP.
Adds: Linux driver detection fix, Linux permission diagnostics, 'optimize' system tuner, 'install-rocm'.
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
from datetime import datetime, timezone, timedelta

__version__ = "0.1.5"
GITHUB_REPO = "xanpavle/rocmfix"

TELEMETRY_ENDPOINT = "https://rocmfix-data.onrender.com/submit"
DATABASE_ENDPOINT = "https://rocmfix-data.onrender.com/gpus.json"
UPDATE_CHECK_ENDPOINT = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DOWNLOAD_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/rocmfix.py"
TELEMETRY_TIMEOUT = 60

CONFIG_DIR = Path.home() / ".rocmfix"
CONFIG_FILE = CONFIG_DIR / "config.json"
OUTBOX_DIR = CONFIG_DIR / "outbox"
BACKUP_DIR = CONFIG_DIR / "backups"
DB_CACHE_FILE = CONFIG_DIR / "db_cache.json"
DB_CACHE_TTL_HOURS = 24
UPDATE_CHECK_TTL_HOURS = 168  # 7 days

# ──────────────────────────────────────────────────────────────────────
# FALLBACK DATABASE (used offline or if fetch fails)
# ──────────────────────────────────────────────────────────────────────

FALLBACK_DATABASE = {
    "7550": {"name": "RX 9070 XT / 9070 / 9070 GRE (Navi 48)", "arch": "RDNA4", "gfx_target": "gfx1201", "override": None, "supported": True, "rec_backend": "vulkan", "known_issues": [], "notes": "Natively supported in ROCm 6.4+."},
    "7551": {"name": "Radeon AI PRO R9700 (Navi 48 Pro)", "arch": "RDNA4", "gfx_target": "gfx1201", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Enterprise variant of Navi 48."},
    "7590": {"name": "RX 9060 XT / 9060 / 9050 (Navi 44)", "arch": "RDNA4", "gfx_target": "gfx1200", "override": "12.0.1", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1201."},
    "744c": {"name": "RX 7900 XTX", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "744e": {"name": "RX 7900 XT", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "747e": {"name": "RX 7900 GRE / RX 7800 XT (variant)", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "vulkan", "known_issues": [], "notes": "Try override 11.0.0 if HIP fails."},
    "7470": {"name": "RX 7800 XT", "arch": "RDNA3", "gfx_target": "gfx1101", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100."},
    "7471": {"name": "RX 7700 XT", "arch": "RDNA3", "gfx_target": "gfx1101", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Same override as 7800 XT."},
    "7480": {"name": "RX 7600", "arch": "RDNA3", "gfx_target": "gfx1102", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100."},
    "7483": {"name": "RX 7600 XT", "arch": "RDNA3", "gfx_target": "gfx1102", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100."},
    "73af": {"name": "RX 6900 XT", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73bf": {"name": "RX 6800 XT / 6800", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73df": {"name": "RX 6700 XT", "arch": "RDNA2", "gfx_target": "gfx1031", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "73ff": {"name": "RX 6600 XT / 6600", "arch": "RDNA2", "gfx_target": "gfx1032", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "743f": {"name": "RX 6500 XT", "arch": "RDNA2", "gfx_target": "gfx1034", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "164e": {"name": "Radeon Graphics (Ryzen 7000 iGPU)", "arch": "RDNA2", "gfx_target": "gfx1036", "override": None, "supported": False, "rec_backend": "cpu", "known_issues": [], "notes": "iGPU. Ignore for AI."},
    "1638": {"name": "Radeon Graphics (Ryzen 5000 iGPU)", "arch": "Vega", "gfx_target": "gfx90c", "override": None, "supported": False, "rec_backend": "cpu", "known_issues": [], "notes": "iGPU. Ignore for AI."},
}

# GPU_DATABASE is populated dynamically at runtime
GPU_DATABASE = dict(FALLBACK_DATABASE)

# ──────────────────────────────────────────────────────────────────────
# COLOR / TERMINAL
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
# CONFIG
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
        "last_update_check": None,
        "last_db_sync": None,
    }

def save_config(cfg: dict):
    _ensure_config_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

# ──────────────────────────────────────────────────────────────────────
# LIVE DB SYNC
# ──────────────────────────────────────────────────────────────────────

def sync_gpu_database(force: bool = False) -> str:
    global GPU_DATABASE
    _ensure_config_dirs()
    cfg = load_config()

    if not force and DB_CACHE_FILE.exists():
        last_sync_str = cfg.get("last_db_sync")
        if last_sync_str:
            try:
                last = datetime.fromisoformat(last_sync_str)
                if datetime.now(timezone.utc) - last < timedelta(hours=DB_CACHE_TTL_HOURS):
                    try:
                        cached = json.loads(DB_CACHE_FILE.read_text())
                        GPU_DATABASE = cached.get("gpus", FALLBACK_DATABASE)
                        return "cached"
                    except Exception:
                        pass
            except Exception:
                pass

    try:
        req = urllib.request.Request(DATABASE_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if "gpus" in data and isinstance(data["gpus"], dict):
                DB_CACHE_FILE.write_text(json.dumps(data))
                cfg["last_db_sync"] = datetime.now(timezone.utc).isoformat()
                save_config(cfg)
                GPU_DATABASE = data["gpus"]
                return "fetched"
    except Exception:
        pass

    if DB_CACHE_FILE.exists():
        try:
            cached = json.loads(DB_CACHE_FILE.read_text())
            GPU_DATABASE = cached.get("gpus", FALLBACK_DATABASE)
            return "cached"
        except Exception:
            pass

    GPU_DATABASE = dict(FALLBACK_DATABASE)
    return "fallback"

# ──────────────────────────────────────────────────────────────────────
# UPDATE CHECKER
# ──────────────────────────────────────────────────────────────────────

def _version_tuple(v: str) -> tuple:
    v = v.lstrip("v")
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0, 0, 0)

def check_for_updates_silent() -> str | None:
    cfg = load_config()
    last_check_str = cfg.get("last_update_check")
    if last_check_str:
        try:
            last = datetime.fromisoformat(last_check_str)
            if datetime.now(timezone.utc) - last < timedelta(hours=UPDATE_CHECK_TTL_HOURS):
                return None
        except Exception:
            pass

    try:
        req = urllib.request.Request(UPDATE_CHECK_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
            cfg["last_update_check"] = datetime.now(timezone.utc).isoformat()
            save_config(cfg)
            if latest and _version_tuple(latest) > _version_tuple(__version__):
                return latest
    except Exception:
        pass
    return None

# ──────────────────────────────────────────────────────────────────────
# DETECTION
# ──────────────────────────────────────────────────────────────────────

def _run(cmd, shell=False):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, shell=shell, timeout=10,
            encoding="utf-8", errors="replace"
        )
        return result.stdout
    except Exception:
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

def _get_linux_driver_version() -> str:
    """Robust Linux amdgpu driver / kernel version detector."""
    # 1. Sysfs module version
    ver_file = Path("/sys/module/amdgpu/version")
    if ver_file.exists():
        try:
            v = ver_file.read_text().strip()
            if v: return v
        except OSError: pass

    # 2. Modinfo amdgpu
    mod_ver = _run(["modinfo", "-F", "version", "amdgpu"]).strip()
    if mod_ver and "not found" not in mod_ver.lower() and "error" not in mod_ver.lower():
        return mod_ver

    # 3. Kernel version fallback (amdgpu is in-tree in Linux kernel)
    kernel_ver = _run(["uname", "-r"]).strip()
    if kernel_ver:
        return f"Kernel {kernel_ver}"

    return "unknown"

def _detect_linux() -> list[dict]:
    gpus = []
    output = _run(["lspci", "-nn", "-d", "1002::"])
    pattern = re.compile(r"\[1002:([0-9a-fA-F]{4})\]")
    driver_ver = _get_linux_driver_version()

    for line in output.splitlines():
        match = pattern.search(line)
        if match and any(kw in line for kw in ["VGA", "Display", "3D"]):
            pci_id = match.group(1).lower()
            name_match = re.search(r"\]:\s*(.+?)\s*\[1002:", line)
            name = name_match.group(1) if name_match else "Unknown AMD GPU"
            gpus.append({"pci_id": pci_id, "name": name, "driver_version": driver_ver, "raw_pnp": line.strip()})
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
    if not drv_ver or drv_ver == "unknown": return None
    if drv_ver.startswith("Kernel"): return {"status": "ok", "msg": f"{drv_ver} (Linux In-Tree Driver)"}
    try:
        major = int(drv_ver.split('.')[0])
        if major >= 32: return {"status": "ok", "msg": "Adrenalin 24.x+ (Good)"}
        elif major == 31: return {"status": "warn", "msg": "Adrenalin 23.x (Update recommended)"}
        else: return {"status": "bad", "msg": f"Very old driver ({major}.x)."}
    except Exception:
        return None

# ──────────────────────────────────────────────────────────────────────
# SMOKE TESTING
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
                return {"success": False, "message": "rocminfo failed.", "details": (res.stderr or res.stdout)[:300]}
            gpu_count = res.stdout.count("Device Type:                     GPU")
            if gpu_count == 0:
                # Check user group permissions
                groups = _run(["groups"]).strip()
                missing = []
                if "render" not in groups: missing.append("render")
                if "video" not in groups: missing.append("video")
                if missing:
                    details = f"Missing user groups: {', '.join(missing)}. Run: sudo usermod -aG render,video $USER"
                else:
                    details = "Needs HSA_OVERRIDE_GFX_VERSION set or ROCm service restart."
                return {"success": False, "message": "0 GPU agents found by rocminfo.", "details": details}
            return {"success": True, "message": f"ROCm detected {gpu_count} GPU agent(s).", "details": f"override={override or 'not set'}"}
        except FileNotFoundError:
            return {"success": False, "message": "rocminfo not found.", "details": "Run 'rocmfix install-rocm'"}
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}
    elif os_name == "windows":
        hip_path = os.environ.get("HIP_PATH", "")
        if not hip_path:
            return {"success": False, "message": "HIP_PATH not set.", "details": "Run 'rocmfix install-hip'"}
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
# AUTO-APPLY
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
# TELEMETRY
# ──────────────────────────────────────────────────────────────────────

def build_telemetry_payload(gpu: dict, info: dict, rocm_ver, smoke_result=None, benchmark=None) -> dict:
    cfg = load_config()
    payload = {
        "schema_version": 2, "user_id": cfg["user_id"], "rocmfix_version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(), "os": detect_os(), "shell": detect_shell(),
        "gpu": {"name": gpu.get("name"), "pci_id": gpu.get("pci_id"), "driver_version": gpu.get("driver_version")},
        "database": {"in_database": info is not None, "override_recommended": info.get("override") if info else None, "supported_native": info.get("supported") if info else None},
        "rocm_version": rocm_ver, "smoke_test": smoke_result if smoke_result else None
    }
    if benchmark:
        payload["benchmark"] = benchmark
    return payload

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
                file.unlink(); sent += 1
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
    print("  and benchmarks with the community database.\n")
    print(f"  {C.BOLD}What gets sent:{C.RESET} GPU model, PCI ID, driver, OS, override, bench results")
    print(f"  {C.BOLD}What does NOT get sent:{C.RESET} Username, IP, file paths, personal data\n")
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
# GLOBAL INSTALLER & HELPERS
# ──────────────────────────────────────────────────────────────────────

def is_installed_globally() -> bool: return shutil.which("rocmfix") is not None

def install_globally() -> bool:
    os_name, script_path, script_dir = detect_os(), Path(__file__).resolve(), Path(__file__).resolve().parent
    print(f"\n{C.BOLD}{C.CYAN}⚙️  Register 'rocmfix' as a global command?{C.RESET}")
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
            print(f"\n  {C.GREEN}✓ Registered!{C.RESET}\n")
            return True
        except Exception as e: print(f"{C.RED}Failed: {e}{C.RESET}"); return False
    else:
        try:
            local_bin = Path.home() / ".local" / "bin"
            local_bin.mkdir(parents=True, exist_ok=True)
            dest = local_bin / "rocmfix"
            shutil.copy2(script_path, dest)
            dest.chmod(0o755)
            print(f"\n  {C.GREEN}✓ Installed to {dest}{C.RESET}")
            return True
        except Exception as e: print(f"{C.RED}Failed: {e}{C.RESET}"); return False

def format_env_command(override: str) -> str:
    shell = detect_shell()
    if detect_os() == "windows":
        return f'  $env:HSA_OVERRIDE_GFX_VERSION="{override}"' if shell == "powershell" else f"  set HSA_OVERRIDE_GFX_VERSION={override}"
    return f"  set -gx HSA_OVERRIDE_GFX_VERSION {override}" if shell == "fish" else f"  export HSA_OVERRIDE_GFX_VERSION={override}"

def get_issue_url(gpu, rocm_ver):
    body = f"## Unknown GPU\n\n- **Name:** {gpu.get('name')}\n- **PCI ID:** `{gpu.get('pci_id')}`\n- **Driver:** {gpu.get('driver_version')}\n- **OS:** {detect_os()}\n"
    params = urllib.parse.urlencode({"title": f"[GPU Report] {gpu.get('name','?')} ({gpu.get('pci_id','?')})", "body": body, "labels": "unknown-gpu"})
    return f"https://github.com/{GITHUB_REPO}/issues/new?{params}"

def print_header():
    print(f"{C.BOLD}{C.CYAN}\n  ╔══════════════════════════════════════╗")
    print(f"  ║           ROCmFix v{__version__.ljust(18)}║")
    print(f"  ║   AMD GPU override helper for ROCm   ║")
    print(f"  ╚══════════════════════════════════════╝\n{C.RESET}")

def maybe_print_update_banner():
    latest = check_for_updates_silent()
    if latest:
        print(f"  {C.YELLOW}[!] Update available: v{latest} (you have v{__version__}){C.RESET}")
        print(f"  {C.YELLOW}    Run 'rocmfix update' to install.{C.RESET}\n")

# ──────────────────────────────────────────────────────────────────────
# BENCH HELPERS
# ──────────────────────────────────────────────────────────────────────

def find_lm_studio_models() -> list[str]:
    home = Path.home()
    candidates = [
        home / ".cache" / "lm-studio" / "models",
        home / ".lmstudio" / "models",
        home / "AppData" / "Roaming" / "LMStudio" / "models",
    ]
    models = []
    for base in candidates:
        if base.exists():
            for gguf in base.rglob("*.gguf"):
                models.append(str(gguf))
    return models

def bench_via_ollama(gpu_override: str | None) -> dict:
    if "ollama version" not in _run(["ollama", "--version"]):
        return {"error": "ollama not found"}

    models_out = _run(["ollama", "list"])
    models = [line.split()[0] for line in models_out.splitlines()[1:] if line.strip()]
    if not models:
        print(f"  {C.YELLOW}Pulling qwen2.5:0.5b (~400MB)...{C.RESET}")
        subprocess.run(["ollama", "pull", "qwen2.5:0.5b"])
        models = ["qwen2.5:0.5b"]

    model = models[0]
    prompt = "Write a 50 word story about a robot learning to paint."
    print(f"  Model: {model}\n")
    results = {"runtime": "ollama", "model": model, "vulkan_toks": 0, "hip_toks": 0}

    for backend in ["vulkan", "rocm"]:
        print(f"  Testing {C.BOLD}{backend.upper()}{C.RESET}...")
        if detect_os() == "windows":
            _run(["taskkill", "/F", "/IM", "ollama_app.exe"])
            _run(["taskkill", "/F", "/IM", "ollama.exe"])
        else:
            _run(["pkill", "-9", "ollama"])
        time.sleep(1)

        env = os.environ.copy()
        env["OLLAMA_GPU_BACKEND"] = backend
        if gpu_override:
            env["HSA_OVERRIDE_GFX_VERSION"] = gpu_override

        proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(4)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                tok_s = round(data.get("eval_count", 0) / (data.get("eval_duration", 1) / 1e9), 2)
                results[f"{'vulkan' if backend=='vulkan' else 'hip'}_toks"] = tok_s
                print(f"    {C.GREEN}→ {tok_s} tok/s{C.RESET}")
        except Exception as e:
            print(f"    {C.RED}→ Failed: {e}{C.RESET}")
        try: proc.kill()
        except: pass

    vk, hp = results["vulkan_toks"], results["hip_toks"]
    results["winner"] = "vulkan" if vk > hp else "hip" if hp > vk else "tie"
    return results

def bench_via_lmstudio(gpu_override: str | None) -> dict:
    lms_out = _run(["lms", "version"])
    if not lms_out.strip():
        if not _run(["lms", "--help"]).strip() and shutil.which("lms") is None:
            return {"error": "LM Studio CLI (lms) not found. Open LM Studio → install CLI / run 'lms bootstrap'."}

    model_id = None
    model_name = None

    ls_out = _run(["lms", "ls"])
    if not ls_out.strip():
        ls_out = _run(["lms", "ls", "--json"])

    for line in ls_out.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("you have") or "params" in line.lower():
            continue
        parts = line.split()
        if parts:
            candidate = parts[0]
            if candidate.lower() in ("identifier", "model", "name", "---", "path"):
                continue
            model_id = candidate
            model_name = candidate
            break

    if not model_id:
        models = find_lm_studio_models()
        if not models:
            return {"error": "No LM Studio models found. Download a GGUF in LM Studio first."}
        model_id = models[0]
        model_name = Path(models[0]).stem

    print(f"  Model: {model_name}\n")
    results = {
        "runtime": "lm_studio",
        "model": model_name,
        "vulkan_toks": 0.0,
        "hip_toks": 0.0,
        "winner": "tie",
    }

    prompt_payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Write a 40 word story about a robot learning to paint."}],
        "max_tokens": 64,
        "stream": False,
        "temperature": 0.2,
    }

    def _lms_api(method: str, path: str, body: dict | None = None, timeout: int = 180):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:1234{path}",
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer lm-studio",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}

    def _wait_model_loaded(want: str, seconds: int = 90) -> bool:
        deadline = time.time() + seconds
        want_l = want.lower()
        while time.time() < deadline:
            try:
                data = _lms_api("GET", "/v1/models", None, timeout=10)
                items = data.get("data") or []
                if not items:
                    time.sleep(1.5)
                    continue
                for it in items:
                    mid = str(it.get("id", "")).lower()
                    if want_l in mid or mid in want_l or Path(want).stem.lower() in mid:
                        return True
                return True
            except Exception:
                time.sleep(1.5)
        return False

    def _unload_all(env: dict | None = None):
        e = env or os.environ.copy()
        cmds = [
            ["lms", "unload", "--all"],
            ["lms", "unload", "-a"],
            ["lms", "unload"],
        ]
        if model_id:
            cmds.insert(0, ["lms", "unload", str(model_id)])
            cmds.insert(1, ["lms", "unload", model_name])
        for cmd in cmds:
            try:
                subprocess.run(
                    cmd,
                    env=e,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
            except Exception:
                continue
        time.sleep(2)

    def _ensure_server(env: dict):
        _run(["lms", "server", "stop"])
        time.sleep(1)
        try:
            subprocess.Popen(
                ["lms", "server", "start"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000 if detect_os() == "windows" else 0,
            )
        except Exception:
            subprocess.Popen(["lms", "server", "start"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            try:
                _lms_api("GET", "/v1/models", None, timeout=3)
                return True
            except Exception:
                time.sleep(1)
        return False

    def _load_model(env: dict) -> bool:
        load_cmds = [
            ["lms", "load", model_id],
            ["lms", "load", model_id, "-y"],
            ["lms", "load", str(model_id)],
        ]
        for cmd in load_cmds:
            try:
                subprocess.run(
                    cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
            except Exception:
                continue
            if _wait_model_loaded(str(model_id), seconds=60):
                return True
        return False

    for backend in ["vulkan", "rocm"]:
        print(f"  Testing {C.BOLD}{backend.upper()}{C.RESET}...")
        env = os.environ.copy()
        env["OLLAMA_GPU_BACKEND"] = backend
        if backend == "vulkan":
            env["GGML_VK_VISIBLE_DEVICES"] = "0"
        if gpu_override:
            env["HSA_OVERRIDE_GFX_VERSION"] = gpu_override

        print(f"    {C.GRAY}Unloading any previous model...{C.RESET}")
        _unload_all(env)

        if not _ensure_server(env):
            print(f"    {C.RED}→ Server failed to start{C.RESET}")
            continue

        print(f"    {C.GRAY}Loading model (this can take a minute)...{C.RESET}")
        if not _load_model(env):
            print(f"    {C.RED}→ Could not load model via 'lms load'{C.RESET}")
            print(f"    {C.YELLOW}Tip: In LM Studio Developer tab, load the model once, keep server on, retry.{C.RESET}")
            continue

        chat_model = model_name
        try:
            listed = _lms_api("GET", "/v1/models")
            ids = [x.get("id") for x in (listed.get("data") or []) if x.get("id")]
            if ids:
                stem = Path(str(model_id)).stem.lower()
                pick = None
                for i in ids:
                    if stem in str(i).lower() or str(i).lower() in stem:
                        pick = i
                        break
                chat_model = pick or ids[0]
                prompt_payload["model"] = chat_model
        except Exception:
            prompt_payload["model"] = chat_model

        try:
            start = time.time()
            data = _lms_api("POST", "/v1/chat/completions", prompt_payload, timeout=300)
            elapsed = max(time.time() - start, 0.001)
            usage = data.get("usage") or {}
            completion_tokens = usage.get("completion_tokens") or usage.get("total_tokens") or 0
            if not completion_tokens:
                content = ""
                try: content = data["choices"][0]["message"]["content"]
                except Exception: content = ""
                completion_tokens = max(len(content.split()), 1)
            tok_s = round(completion_tokens / elapsed, 2)
            key = "vulkan_toks" if backend == "vulkan" else "hip_toks"
            results[key] = tok_s
            print(f"    {C.GREEN}→ {tok_s} tok/s{C.RESET}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            print(f"    {C.RED}→ HTTP {e.code}: {body[:180] or e.reason}{C.RESET}")
        except Exception as e:
            print(f"    {C.RED}→ Failed: {e}{C.RESET}")

        print(f"    {C.GRAY}Unloading model...{C.RESET}")
        _unload_all(env)

    print(f"  {C.GRAY}Final cleanup...{C.RESET}")
    _unload_all()

    try: _run(["lms", "server", "stop"])
    except Exception: pass

    vk, hp = results["vulkan_toks"], results["hip_toks"]
    results["winner"] = "vulkan" if vk > hp else "hip" if hp > vk else "tie"
    return results

# ──────────────────────────────────────────────────────────────────────
# CLI COMMANDS
# ──────────────────────────────────────────────────────────────────────

def cmd_detect(args):
    print_header()
    cfg = load_config()

    sync_result = sync_gpu_database()
    if sync_result == "fetched":
        print(f"  {C.GRAY}[DB synced from server ({len(GPU_DATABASE)} GPUs)]{C.RESET}")
    elif sync_result == "cached":
        print(f"  {C.GRAY}[Using cached DB ({len(GPU_DATABASE)} GPUs)]{C.RESET}")

    maybe_print_update_banner()

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
        if info.get("known_issues"):
            print(f"      {C.RED}Known issues:{C.RESET}")
            for issue in info["known_issues"]: print(f"        - {issue}")

        print(f"\n      {C.BOLD}Apply this override?{C.RESET}")
        print(f"        [{C.GREEN}A{C.RESET}] Apply automatically")
        print(f"        [{C.CYAN}S{C.RESET}] Show me the command")
        print(f"        [{C.GRAY}N{C.RESET}] Skip")
        try: choice = input("      > ").strip().lower()
        except: choice = "n"

        if choice == "a":
            res = apply_override(info["override"])
            if res["success"]:
                print(f"\n      {C.GREEN}✓ Override applied!{C.RESET}")
                print(f"      {C.YELLOW}⚠ Open a NEW terminal for it to take effect.{C.RESET}")
            else:
                print(f"\n      {C.RED}✗ Failed: {res.get('error')}{C.RESET}\n{format_env_command(info['override'])}")
        elif choice == "s":
            print(f"\n      {C.BOLD}Run in {shell}:{C.RESET}\n{format_env_command(info['override'])}")
        else: print(f"      {C.GRAY}Skipped.{C.RESET}")

def cmd_doctor(args):
    print_header()
    sync_gpu_database()
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
            print(f"    {C.CYAN}Run: rocmfix install-hip{C.RESET}")
    else:
        rocm = detect_rocm_version()
        if rocm: print(f"  {C.GREEN}✓ ROCm Installed:{C.RESET} v{rocm}")
        else:
            print(f"  {C.RED}✗ ROCm NOT FOUND{C.RESET}")
            print(f"    {C.CYAN}Run: rocmfix install-rocm{C.RESET}")

    print(f"\n{C.BOLD}3. Vulkan Engine{C.RESET}")
    vk = _run(["vulkaninfo", "--summary"])
    if "Vulkan Instance Version" in vk or "devices" in vk.lower(): print(f"  {C.GREEN}✓ Vulkan API ready{C.RESET}")
    else: print(f"  {C.YELLOW}⚠ Vulkan not responding{C.RESET}")
    print(f"\n{C.BOLD}4. Environment Override{C.RESET}")
    ov = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    if ov: print(f"  {C.GREEN}✓ Set to:{C.RESET} {ov}")
    else: print(f"  {C.GRAY}• Not currently set.{C.RESET}")
    print(f"\n{C.BOLD}Recommendation:{C.RESET}")
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        if info:
            print(f"  For {g['name']}: Use {C.BOLD}{info.get('rec_backend','vulkan').upper()}{C.RESET} backend.")
            if info.get('override'): print(f"  Requires HSA_OVERRIDE_GFX_VERSION={info['override']} for HIP.")
    print()

def cmd_install_hip(args):
    print_header()
    if detect_os() != "windows":
        print(f"  {C.RED}This command is only for Windows.{C.RESET}")
        return
    print(f"{C.BOLD}{C.CYAN}📥 Windows HIP SDK Auto-Installer{C.RESET}\n")
    if os.environ.get("HIP_PATH"):
        print(f"  {C.GREEN}✓ HIP SDK already installed at {os.environ.get('HIP_PATH')}{C.RESET}")
        return
    candidates = [
        "https://download.amd.com/developer/eula/rocm-hub/HIP-SDK-Inst-Win-v6.1.2.exe",
        "https://download.amd.com/developer/eula/rocm-hub/HIP-SDK-Inst-Win-v6.1.0.exe",
        "https://download.amd.com/developer/eula/rocm-hub/HIP-SDK-Inst-Win-v6.2.0.exe",
    ]
    hub = "https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html"
    installer_path = Path(os.environ.get("TEMP", ".")) / "HIP-SDK-Installer.exe"
    print("  This will download the official AMD HIP SDK (~1.2 GB).\n")
    try:
        if input("  Proceed with download? [Y/n]: ").strip().lower() == 'n': return
    except: return
    print(f"\n  {C.GRAY}Connecting to AMD...{C.RESET}")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.amd.com/", "Accept": "*/*"}
    ok = False
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
            ok = True
            print(f"\n\n  {C.GREEN}✓ Download complete!{C.RESET}")
            break
        except Exception:
            continue
    if ok:
        try:
            os.startfile(installer_path)
            print(f"  {C.YELLOW}⚠ After installer finishes, RESTART YOUR PC.{C.RESET}\n")
        except Exception as e:
            print(f"  {C.RED}Failed to launch: {e}{C.RESET}")
    else:
        print(f"\n  {C.YELLOW}⚠ AMD blocked direct download. Opening browser...{C.RESET}\n")
        try: webbrowser.open(hub)
        except: pass
        print(f"  URL: {hub}\n")

def cmd_install_rocm(args):
    print_header()
    if detect_os() != "linux":
        print(f"  {C.RED}This command is for Linux only. On Windows, use 'rocmfix install-hip'.{C.RESET}\n")
        return

    print(f"  {C.BOLD}{C.CYAN}🐧 Linux ROCm & User Permissions Setup{C.RESET}\n")

    os_release = Path("/etc/os-release")
    distro = "unknown"
    if os_release.exists():
        content = os_release.read_text().lower()
        if "ubuntu" in content or "debian" in content: distro = "ubuntu"
        elif "fedora" in content or "rhel" in content: distro = "fedora"
        elif "arch" in content: distro = "arch"

    print(f"  Detected Linux Distribution: {C.BOLD}{distro.title()}{C.RESET}\n")

    if distro == "ubuntu":
        print("  1. Install ROCm packages:")
        print(f"     {C.CYAN}sudo apt update && sudo apt install -y rocm-hip-sdk rocminfo{C.RESET}\n")
    elif distro == "fedora":
        print("  1. Install ROCm packages:")
        print(f"     {C.CYAN}sudo dnf install -y rocm-hip rocminfo{C.RESET}\n")
    elif distro == "arch":
        print("  1. Install ROCm packages:")
        print(f"     {C.CYAN}sudo pacman -S --needed rocm-hip-sdk rocminfo{C.RESET}\n")

    print("  2. Add your user to GPU permission groups:")
    print(f"     {C.CYAN}sudo usermod -aG render,video $USER{C.RESET}\n")
    print(f"  {C.YELLOW}⚠ Log out and log back in for group permissions to take effect.{C.RESET}\n")

def cmd_optimize(args):
    print_header()
    os_name = detect_os()
    print(f"  {C.BOLD}{C.CYAN}⚡ System Deep Optimizer ({os_name.title()}){C.RESET}\n")

    if os_name == "windows":
        print(f"  {C.BOLD}Checking Windows Registry TDR, HAGS, and ULPS settings...{C.RESET}\n")
        try:
            import winreg
            tdr_key = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
            
            # Read TDR
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key, 0, winreg.KEY_READ) as k:
                    try: tdr_val, _ = winreg.QueryValueEx(k, "TdrDelay")
                    except FileNotFoundError: tdr_val = None
            except Exception: tdr_val = None

            print(f"  • TDR Delay (GPU Timeout): {tdr_val if tdr_val is not None else 'Default (2 seconds)'}")
            if tdr_val is None or tdr_val < 60:
                print(f"    {C.YELLOW}⚠ Low TDR delay causes Windows to crash GPUs during long LLM tasks.{C.RESET}")

            # Read HAGS
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key, 0, winreg.KEY_READ) as k:
                    try: hags_val, _ = winreg.QueryValueEx(k, "HwSchMode")
                    except FileNotFoundError: hags_val = None
            except Exception: hags_val = None

            print(f"  • HAGS (Hardware Scheduling): {'Enabled (2)' if hags_val == 2 else 'Disabled (1)' if hags_val == 1 else 'Not set'}")
            if hags_val == 2:
                print(f"    {C.YELLOW}⚠ HAGS causes random VRAM crashes on AMD GPUs in local AI.{C.RESET}")

            print(f"\n  {C.BOLD}Apply Recommended Optimizations?{C.RESET}")
            print("    - Set TdrDelay = 60s (Fixes GPU timeout crashes)")
            print("    - Set TdrDdiDelay = 60s")
            print("    - Disable EnableUlps (Fixes GPU sleep crashes)")
            print("    - Disable HAGS (Fixes VRAM allocation crashes)\n")

            ans = input("  Apply optimizations? [Y/n]: ").strip().lower()
            if ans != 'n':
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key, 0, winreg.KEY_ALL_ACCESS) as k:
                    winreg.SetValueEx(k, "TdrDelay", 0, winreg.REG_DWORD, 60)
                    winreg.SetValueEx(k, "TdrDdiDelay", 0, winreg.REG_DWORD, 60)
                    winreg.SetValueEx(k, "HwSchMode", 0, winreg.REG_DWORD, 1)

                base_class = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
                ulps_count = 0
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_class, 0, winreg.KEY_READ) as class_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(class_key, i)
                            i += 1
                            if sub == "Properties": continue
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base_class}\\{sub}", 0, winreg.KEY_ALL_ACCESS) as sk:
                                    winreg.SetValueEx(sk, "EnableUlps", 0, winreg.REG_DWORD, 0)
                                    ulps_count += 1
                            except Exception: pass
                        except OSError: break

                print(f"\n  {C.GREEN}✓ Optimizations applied successfully!{C.RESET}")
                print(f"    - TdrDelay set to 60s")
                print(f"    - HAGS set to Disabled")
                print(f"    - EnableUlps disabled across {ulps_count} key(s)")
                print(f"\n  {C.YELLOW}⚠ Please RESTART YOUR PC for Windows changes to take full effect.{C.RESET}\n")

        except PermissionError:
            print(f"\n  {C.RED}✗ Permission Denied: Run Command Prompt as ADMINISTRATOR to apply optimizations.{C.RESET}\n")
        except Exception as e:
            print(f"\n  {C.RED}✗ Failed to apply optimizations: {e}{C.RESET}\n")

    elif os_name == "linux":
        print(f"  {C.BOLD}Checking Linux User Permissions...{C.RESET}\n")
        groups = _run(["groups"]).strip()
        missing = []
        if "render" not in groups: missing.append("render")
        if "video" not in groups: missing.append("video")

        if missing:
            print(f"  {C.YELLOW}⚠ Missing user group permissions: {', '.join(missing)}{C.RESET}")
            print(f"    Your user cannot access AMD GPU device nodes directly.")
            print(f"\n  Fix command:")
            print(f"    {C.CYAN}sudo usermod -aG render,video $USER{C.RESET}\n")
        else:
            print(f"  {C.GREEN}✓ User permissions ok (in render and video groups){C.RESET}\n")

def cmd_bench(args):
    print_header()
    sync_gpu_database()
    print(f"{C.BOLD}{C.CYAN}🏎️  Backend Benchmarker{C.RESET}\n")

    has_ollama = "ollama version" in _run(["ollama", "--version"])
    has_lms = bool(_run(["lms", "version"]))
    lms_models = find_lm_studio_models()

    print(f"{C.BOLD}Detected AI runtimes:{C.RESET}")
    if has_ollama: print(f"  {C.GREEN}✓ Ollama{C.RESET}")
    else: print(f"  {C.GRAY}✗ Ollama (not installed){C.RESET}")
    if has_lms and lms_models: print(f"  {C.GREEN}✓ LM Studio{C.RESET} ({len(lms_models)} models)")
    else: print(f"  {C.GRAY}✗ LM Studio (not installed or no models){C.RESET}")

    if not has_ollama and not (has_lms and lms_models):
        print(f"\n  {C.RED}No usable runtime found.{C.RESET}")
        print("  Install Ollama (https://ollama.com) or LM Studio (https://lmstudio.ai)")
        return

    options = []
    if has_ollama: options.append(("ollama", "Ollama"))
    if has_lms and lms_models: options.append(("lm_studio", "LM Studio"))

    if len(options) == 1:
        choice = options[0][0]
        print(f"\n  Using {options[0][1]}")
    else:
        print(f"\n  Which runtime?")
        for i, (_, name) in enumerate(options, 1):
            print(f"    [{i}] {name}")
        try:
            sel = input("  > ").strip()
            choice = options[int(sel)-1][0]
        except: return

    gpus = detect_gpus()
    gpu_override = None
    target_gpu = None
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        if info and info.get("override"):
            gpu_override = info["override"]
            target_gpu = g
            break
    if not target_gpu and gpus:
        target_gpu = gpus[0]

    if choice == "ollama":
        results = bench_via_ollama(gpu_override)
    else:
        results = bench_via_lmstudio(gpu_override)

    if "error" in results:
        print(f"\n  {C.RED}✗ {results['error']}{C.RESET}")
        return

    vk, hp = results["vulkan_toks"], results["hip_toks"]
    print(f"\n{C.BOLD}🏆 Results:{C.RESET}")
    print(f"  Vulkan: {vk} tok/s")
    print(f"  HIP/ROCm: {hp} tok/s")
    if vk > hp and vk > 0:
        print(f"\n  {C.GREEN}Winner: VULKAN (+{int((vk/hp-1)*100 if hp else 0)}%){C.RESET}")
    elif hp > vk and hp > 0:
        print(f"\n  {C.GREEN}Winner: HIP/ROCm (+{int((hp/vk-1)*100 if vk else 0)}%){C.RESET}")
    else:
        print(f"\n  {C.YELLOW}No clear winner.{C.RESET}")

    cfg = load_config()
    if cfg.get("telemetry_enabled") and target_gpu:
        print(f"\n  {C.GRAY}Sending anonymous benchmark to community DB...{C.RESET}")
        info = GPU_DATABASE.get(target_gpu["pci_id"])
        payload = build_telemetry_payload(target_gpu, info, detect_rocm_version(), benchmark=results)
        send_telemetry(payload)
        print(f"  {C.GREEN}✓ Sent!{C.RESET}")

def cmd_update(args):
    print_header()
    print(f"  {C.BOLD}Checking for updates...{C.RESET}")
    try:
        req = urllib.request.Request(UPDATE_CHECK_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
    except Exception as e:
        print(f"  {C.RED}✗ Failed to reach GitHub: {e}{C.RESET}")
        return

    print(f"  Current: v{__version__}")
    print(f"  Latest:  v{latest}\n")

    if _version_tuple(latest) <= _version_tuple(__version__):
        print(f"  {C.GREEN}✓ You are on the latest version!{C.RESET}")
        return

    print(f"  {C.YELLOW}Update available!{C.RESET}")
    try:
        if input("  Download and install? [Y/n]: ").strip().lower() == 'n': return
    except: return

    script_path = Path(__file__).resolve()
    backup_path = BACKUP_DIR / f"rocmfix.py.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    _ensure_config_dirs()

    print(f"\n  {C.GRAY}Backing up current version...{C.RESET}")
    shutil.copy2(script_path, backup_path)
    print(f"  {C.GRAY}Backup: {backup_path}{C.RESET}")

    print(f"  {C.GRAY}Downloading v{latest}...{C.RESET}")
    try:
        req = urllib.request.Request(UPDATE_DOWNLOAD_URL, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            new_content = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  {C.RED}✗ Download failed: {e}{C.RESET}")
        return

    if detect_os() == "windows":
        new_path = script_path.with_suffix(".py.new")
        new_path.write_text(new_content, encoding="utf-8")
        swap_bat = script_path.parent / "_rocmfix_swap.bat"
        swap_bat.write_text(
            f'@echo off\n'
            f'timeout /t 2 /nobreak >nul\n'
            f'move /y "{new_path}" "{script_path}"\n'
            f'del "%~f0"\n'
        )
        subprocess.Popen([str(swap_bat)], shell=True, creationflags=0x08000000)
        print(f"\n  {C.GREEN}✓ Updated to v{latest}!{C.RESET}")
        print(f"  {C.YELLOW}⚠ Restart your terminal to use the new version.{C.RESET}\n")
    else:
        script_path.write_text(new_content, encoding="utf-8")
        print(f"\n  {C.GREEN}✓ Updated to v{latest}!{C.RESET}\n")

def cmd_export(args):
    print_header()
    sync_gpu_database()
    print(f"  {C.BOLD}Generating system report...{C.RESET}\n")

    gpus = detect_gpus()
    rocm_ver = detect_rocm_version()
    ov = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    vk = _run(["vulkaninfo", "--summary"])
    vulkan_ok = "Vulkan Instance Version" in vk or "devices" in vk.lower()

    lines = [
        f"# ROCmFix System Report",
        f"",
        f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **ROCmFix version:** v{__version__}",
        f"- **OS:** {detect_os()}",
        f"- **Shell:** {detect_shell()}",
        f"",
        f"## GPUs",
        f"",
        f"| Name | PCI ID | Driver | Status |",
        f"|---|---|---|---|",
    ]
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        status = "Unknown"
        if info:
            if info.get("supported"): status = "✅ Native"
            elif info.get("override"): status = f"⚠️ Needs override {info['override']}"
            else: status = "🚫 iGPU / not for AI"
        lines.append(f"| {g['name']} | `{g['pci_id']}` | {g['driver_version']} | {status} |")

    lines += [
        f"",
        f"## AI Stack",
        f"",
        f"- **ROCm/HIP:** {'✅ ' + rocm_ver if rocm_ver else '❌ Not installed'}",
        f"- **Vulkan:** {'✅ Working' if vulkan_ok else '❌ Not responding'}",
        f"- **HSA_OVERRIDE_GFX_VERSION:** {ov or 'Not set'}",
        f"",
        f"## Recommendations",
        f"",
    ]
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        if info:
            lines.append(f"- **{g['name']}:** Use {info.get('rec_backend','vulkan').upper()} backend")
            if info.get('override'):
                lines.append(f"  - HSA_OVERRIDE_GFX_VERSION={info['override']} for HIP")

    lines += [
        f"",
        f"---",
        f"_Generated by ROCmFix v{__version__} — https://github.com/{GITHUB_REPO}_",
    ]

    desktop = Path.home() / "Desktop"
    out_dir = desktop if desktop.exists() else Path.cwd()
    filename = f"rocmfix-report-{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path = out_dir / filename
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"  {C.GREEN}✓ Report saved to:{C.RESET}")
    print(f"  {out_path}\n")
    print(f"  {C.GRAY}Attach this to GitHub issues or Reddit posts when asking for help.{C.RESET}\n")

def cmd_test(args):
    print_header()
    sync_gpu_database()
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
    sync_gpu_database()
    print(f"  {C.BOLD}Known GPUs ({len(GPU_DATABASE)}):{C.RESET}\n")
    print(f"  {'PCI':<6} {'Name':<40} {'Arch':<8} {'Override':<10} Status")
    print(f"  {'-'*6} {'-'*40} {'-'*8} {'-'*10} {'-'*6}")
    for pid, info in GPU_DATABASE.items():
        st = f"{C.GREEN}Native{C.RESET}" if info.get("supported") else f"{C.YELLOW}Override{C.RESET}"
        ov = info.get("override") or "-"
        print(f"  {pid:<6} {info['name']:<40} {info['arch']:<8} {ov:<10} {st}")

def cmd_undo(args):
    print_header()
    result = undo_last_override()
    if result["success"]:
        print(f"  {C.GREEN}✓ Reverted.{C.RESET}")
        print(f"  {C.YELLOW}⚠ Open a NEW terminal for changes.{C.RESET}")
    else: print(f"  {C.RED}✗ {result['error']}{C.RESET}")

def cmd_telemetry(args):
    print_header()
    cfg = load_config()
    print(f"  {C.BOLD}Status:{C.RESET} {C.GREEN if cfg.get('telemetry_enabled') else C.RED}{'ENABLED' if cfg.get('telemetry_enabled') else 'DISABLED'}{C.RESET}\n")
    print(f"  Queued reports: {len(list(OUTBOX_DIR.glob('*.json'))) if OUTBOX_DIR.exists() else 0}")
    print(f"  Endpoint: {TELEMETRY_ENDPOINT}\n")
    print(f"  [E] Enable  [D] Disable  [F] Flush  [S] Sample  [Q] Quit")
    try: choice = input("  > ").strip().lower()
    except: return
    if choice == "e": cfg["telemetry_enabled"] = True; save_config(cfg); print(f"  {C.GREEN}✓ Enabled{C.RESET}")
    elif choice == "d": cfg["telemetry_enabled"] = False; save_config(cfg); print(f"  {C.YELLOW}Disabled{C.RESET}")
    elif choice == "f": sent, failed = flush_outbox(); print(f"  Sent: {sent}, Failed: {failed}")
    elif choice == "s":
        gpus = detect_gpus()
        if gpus: show_telemetry_sample(gpus[0], GPU_DATABASE.get(gpus[0]["pci_id"]), detect_rocm_version())

def cmd_verify(args):
    print_header()
    sync_gpu_database()
    pci_id = input("  PCI ID: ").strip().lower()
    override = input("  Override: ").strip()
    info = GPU_DATABASE.get(pci_id)
    if info: print(f"\n  {C.YELLOW}Already in DB:{C.RESET} {info['name']} (override: {info.get('override')})")
    result = run_smoke_test(override or None)
    if result["success"]: print(f"  {C.GREEN}✓ PASS: {result['message']}{C.RESET}")
    else: print(f"  {C.RED}✗ FAIL: {result['message']}{C.RESET}")

def cmd_contribute(args):
    print_header()
    pci_id = input("  PCI ID: ").strip().lower()
    name = input("  Name: ").strip()
    arch = input("  Arch: ").strip()
    gfx = input("  gfx target: ").strip()
    ov = input("  Override (blank=none): ").strip()
    notes = input("  Notes: ").strip()
    print(f'\n    "{pci_id}": {{')
    print(f'        "name": "{name}", "arch": "{arch}",')
    print(f'        "gfx_target": "{gfx}",')
    print(f'        "override": {"\"" + ov + "\"" if ov else "null"},')
    print(f'        "supported": {str(ov == "").lower()},')
    print(f'        "known_issues": [], "notes": "{notes}", "rec_backend": "vulkan"\n    }},')

def cmd_sync(args):
    print_header()
    print(f"  {C.BOLD}Force-syncing GPU database...{C.RESET}\n")
    result = sync_gpu_database(force=True)
    if result == "fetched":
        print(f"  {C.GREEN}✓ Fetched fresh DB ({len(GPU_DATABASE)} GPUs){C.RESET}")
    elif result == "cached":
        print(f"  {C.YELLOW}⚠ Server unreachable, using cached DB{C.RESET}")
    else:
        print(f"  {C.RED}✗ Fell back to hardcoded DB{C.RESET}")

def main():
    enable_ansi()
    parser = argparse.ArgumentParser(prog="rocmfix", description="AMD GPU override helper")
    parser.add_argument("--version", action="version", version=f"ROCmFix {__version__}")
    parser.add_argument("command", nargs="?", default="detect",
                        choices=["detect", "test", "list", "contribute", "install", "verify",
                                 "undo", "telemetry", "doctor", "install-hip", "install-rocm",
                                 "bench", "update", "export", "sync", "optimize"])
    args = parser.parse_args()

    dispatch = {
        "detect": cmd_detect, "test": cmd_test, "list": cmd_list, "verify": cmd_verify,
        "contribute": cmd_contribute, "install": lambda a: install_globally(),
        "undo": cmd_undo, "telemetry": cmd_telemetry, "doctor": cmd_doctor,
        "install-hip": cmd_install_hip, "install-rocm": cmd_install_rocm, "bench": cmd_bench,
        "update": cmd_update, "export": cmd_export, "sync": cmd_sync, "optimize": cmd_optimize,
    }
    dispatch[args.command](args)

if __name__ == "__main__":
    main()
