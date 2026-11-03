#!/usr/bin/env python3
"""
ROCmFix v0.1.8 — Cross-platform AMD GPU override detector for ROCm/HIP.
Single-file, zero external dependencies.
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
import signal
import socket
import webbrowser
import glob
import ctypes
from pathlib import Path
from datetime import datetime, timezone, timedelta

__version__ = "0.1.8"
GITHUB_REPO = "xanpavle/rocmfix"

TELEMETRY_ENDPOINT = "https://rocmfix-data.onrender.com/submit"
# The GPU database lives in this repo (gpus.json). GitHub raw is fast and never cold-starts;
# the collector copy is a backup for when GitHub is unreachable.
DATABASE_ENDPOINTS = [
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/gpus.json",
    "https://rocmfix-data.onrender.com/gpus.json",
]
DATABASE_ENDPOINT = DATABASE_ENDPOINTS[0]
UPDATE_CHECK_ENDPOINT = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DOWNLOAD_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/rocmfix.py"
TELEMETRY_TIMEOUT = 15

CONFIG_DIR = Path.home() / ".rocmfix"
CONFIG_FILE = CONFIG_DIR / "config.json"
OUTBOX_DIR = CONFIG_DIR / "outbox"
BACKUP_DIR = CONFIG_DIR / "backups"
DB_CACHE_FILE = CONFIG_DIR / "db_cache.json"
BENCH_HISTORY_FILE = CONFIG_DIR / "bench_history.json"
DB_CACHE_TTL_HOURS = 24
UPDATE_CHECK_TTL_HOURS = 168

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
GFX_RE = re.compile(r"^gfx(\d{1,2})([0-9a-f])([0-9a-f])$")

# CPU-fallback thresholds for small (<=9B) models: decode tok/s below these suggests CPU fallback
CPU_FALLBACK_HEURISTIC = {
    "low_vram":     {"vram_gb_max": 8,  "min_expected_toks": 15},
    "mid_vram":     {"vram_gb_max": 12, "min_expected_toks": 25},
    "high_vram":    {"vram_gb_max": 16, "min_expected_toks": 35},
    "flagship":     {"vram_gb_max": 999,"min_expected_toks": 45},
}

# ──────────────────────────────────────────────────────────────────────
# 1. GFX RULES + FALLBACK DATABASE
# ──────────────────────────────────────────────────────────────────────
# The real gfx target is read from the driver whenever possible and the override is worked out
# from these rules. The database only supplies names, notes, known issues and exceptions.

# Targets ROCm 7.x ships kernels for (AMD's Linux matrix; Ollama requires ROCm 7 on Linux).
NATIVE_GFX = {"gfx908", "gfx90a", "gfx942", "gfx950",
              "gfx1030", "gfx1100", "gfx1101", "gfx1102",
              "gfx1150", "gfx1151", "gfx1200", "gfx1201"}
# Native only in recent ROCm. On older ROCm the family override is the fallback.
RECENT_NATIVE_FALLBACK = {"gfx1101": "11.0.0", "gfx1102": "11.0.0",
                          "gfx1150": "11.0.0", "gfx1151": "11.0.0"}
# Dropped from current ROCm releases: native on older ROCm (6.x and earlier), otherwise use Vulkan.
LEGACY_GFX = {"gfx900", "gfx906"}
# Family rules: unsupported targets borrow the ISA of a supported sibling.
FAMILY_OVERRIDES = [
    (re.compile(r"^gfx103[1-6]$"), "10.3.0"),   # RDNA2 Navi 22/23/24 + Rembrandt/Raphael iGPUs
    (re.compile(r"^gfx1103$"),     "11.0.0"),   # RDNA3 Phoenix iGPU (780M/760M)
]
# Targets Ollama for Windows ships rocBLAS kernels for (its bundled rocm_v7_1, Ollama 0.40).
# Its bundled ROCm runtime honors HSA_OVERRIDE_GFX_VERSION on Windows too.
WINDOWS_HIP_GFX = {"gfx906", "gfx1030", "gfx1100", "gfx1101", "gfx1102",
                   "gfx1150", "gfx1151", "gfx1200", "gfx1201"}

FALLBACK_DATABASE = {
    "7550": {"name": "RX 9070 XT / 9070 / 9070 GRE (Navi 48)", "arch": "RDNA4", "gfx_target": "gfx1201", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Native in ROCm 6.4+ / 7.x."},
    "7551": {"name": "Radeon AI PRO R9700 (Navi 48 Pro)", "arch": "RDNA4", "gfx_target": "gfx1201", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Workstation Navi 48."},
    "7590": {"name": "RX 9060 XT / 9060 (Navi 44)", "arch": "RDNA4", "gfx_target": "gfx1200", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Native in ROCm 7.x."},
    "7448": {"name": "Radeon PRO W7900 / W7800 (Navi 31)", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Workstation Navi 31."},
    "744c": {"name": "RX 7900 XTX / XT / GRE (Navi 31)", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "747e": {"name": "RX 7800 XT / 7700 XT (Navi 32)", "arch": "RDNA3", "gfx_target": "gfx1101", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Native in ROCm 7.x. Older ROCm: override 11.0.0."},
    "7470": {"name": "Radeon PRO W7700 (Navi 32)", "arch": "RDNA3", "gfx_target": "gfx1101", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Workstation Navi 32."},
    "7480": {"name": "RX 7600 / 7600 XT (Navi 33)", "arch": "RDNA3", "gfx_target": "gfx1102", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Native in ROCm 7.x. Older ROCm: override 11.0.0."},
    "7483": {"name": "RX 7600M / 7600M XT (Navi 33)", "arch": "RDNA3", "gfx_target": "gfx1102", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Native in ROCm 7.x. Older ROCm: override 11.0.0."},
    "73a5": {"name": "RX 6950 XT (Navi 21)", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73af": {"name": "RX 6900 XT (Navi 21)", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73bf": {"name": "RX 6800 / 6800 XT / 6900 XT (Navi 21)", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73df": {"name": "RX 6700 XT / 6750 XT (Navi 22)", "arch": "RDNA2", "gfx_target": "gfx1031", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "73ef": {"name": "RX 6650 XT / 6600 XT / 6700S (Navi 23)", "arch": "RDNA2", "gfx_target": "gfx1032", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "73ff": {"name": "RX 6600 XT / 6600 (Navi 23)", "arch": "RDNA2", "gfx_target": "gfx1032", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "743f": {"name": "RX 6500 XT / 6400 (Navi 24)", "arch": "RDNA2", "gfx_target": "gfx1034", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": ["4 GB VRAM: only small models fit."], "notes": "Override to gfx1030."},
    "731f": {"name": "RX 5700 XT / 5700 / 5600 XT (Navi 10)", "arch": "RDNA1", "gfx_target": "gfx1010", "override": None, "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "No ROCm kernels and no reliable override. Use Vulkan."},
    "7340": {"name": "RX 5500 XT / 5500 (Navi 14)", "arch": "RDNA1", "gfx_target": "gfx1012", "override": None, "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "No ROCm kernels and no reliable override. Use Vulkan."},
    "66af": {"name": "Radeon VII / Instinct MI50 / MI60 (Vega 20)", "arch": "Vega", "gfx_target": "gfx906", "override": None, "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Dropped from current ROCm. Native on ROCm 6.x and older, or use Vulkan."},
    "687f": {"name": "RX Vega 56 / 64 (Vega 10)", "arch": "Vega", "gfx_target": "gfx900", "override": None, "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Dropped from current ROCm. Use Vulkan."},
    "1586": {"name": "Radeon 8060S / 8050S (Strix Halo, Ryzen AI Max)", "arch": "RDNA3.5", "gfx_target": "gfx1151", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Native in ROCm 7.x. Give the GPU a large VRAM carve-out in BIOS."},
    "150e": {"name": "Radeon 890M / 880M (Strix Point)", "arch": "RDNA3.5", "gfx_target": "gfx1150", "override": None, "supported": True, "rec_backend": "vulkan", "known_issues": [], "notes": "Native in ROCm 7.x. Vulkan is often faster on iGPUs."},
    "1114": {"name": "Radeon 840M / 860M Graphics (Krackan)", "arch": "RDNA3.5", "gfx_target": "gfx1152", "override": None, "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Not in the ROCm matrix. Use Vulkan."},
    "15bf": {"name": "Radeon 780M / 760M (Phoenix)", "arch": "RDNA3", "gfx_target": "gfx1103", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100 for ROCm. Vulkan is often faster on iGPUs."},
    "15c8": {"name": "Radeon 740M (Phoenix 2)", "arch": "RDNA3", "gfx_target": "gfx1103", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100 for ROCm. Vulkan is often faster on iGPUs."},
    "164e": {"name": "Radeon Graphics (Ryzen 7000/9000 desktop iGPU)", "arch": "RDNA2", "gfx_target": "gfx1036", "override": None, "supported": False, "rec_backend": "cpu", "known_issues": [], "notes": "2 CU iGPU — too small for AI. Use your CPU or dGPU."},
    "1638": {"name": "Radeon Graphics (Ryzen 5000 iGPU)", "arch": "Vega", "gfx_target": "gfx90c", "override": None, "supported": False, "rec_backend": "cpu", "known_issues": [], "notes": "iGPU — ignore for AI."},
    "15d8": {"name": "Radeon Vega (Picasso/Raven 2 iGPU)", "arch": "Vega", "gfx_target": "gfx90c", "override": None, "supported": False, "rec_backend": "cpu", "known_issues": [], "notes": "iGPU — ignore for AI."}
}

GPU_DATABASE = dict(FALLBACK_DATABASE)

# ──────────────────────────────────────────────────────────────────────
# 2. COLOR / TERMINAL UTILITIES & HEADERS
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
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        for a in dir(C):
            if not a.startswith("_"): setattr(C, a, "")
        return
    if platform.system().lower() == "windows":
        try:
            k32 = ctypes.windll.kernel32
            k32.SetConsoleMode(k32.GetStdHandle(-11), 7)
        except Exception:
            for a in dir(C):
                if not a.startswith("_") and a != "RESET": setattr(C, a, "")
            C.RESET = ""

def print_header():
    print(f"{C.BOLD}{C.CYAN}\n  ╔══════════════════════════════════════╗")
    print(f"  ║           ROCmFix v{__version__.ljust(18)}║")
    print(f"  ║   AMD GPU override helper for ROCm   ║")
    print(f"  ╚══════════════════════════════════════╝\n{C.RESET}")

# ──────────────────────────────────────────────────────────────────────
# 3. SUBPROCESS UTILITIES
# ──────────────────────────────────────────────────────────────────────

def _run(cmd, shell=False, timeout=10):
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, shell=shell,
            timeout=timeout, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL
        )
        return r.stdout
    except Exception:
        return ""

def safe_run(cmd, shell=False, env=None, timeout=30) -> tuple[str, str, int]:
    try:
        e = env if env is not None else os.environ.copy()
        res = subprocess.run(
            cmd, capture_output=True, text=True, shell=shell,
            timeout=timeout, encoding="utf-8", errors="replace",
            env=e, stdin=subprocess.DEVNULL
        )
        return res.stdout.strip(), res.stderr.strip(), res.returncode
    except Exception as e:
        return "", str(e), -1

def wait_for_port(host: str, port: int, timeout: float = 15.0, poll_interval: float = 0.5) -> bool:
    """Asynchronous port helper: polls a TCP socket until online or expired."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (OSError, socket.timeout):
            time.sleep(poll_interval)
    return False

# ──────────────────────────────────────────────────────────────────────
# 4. CONFIG MANAGEMENT
# ──────────────────────────────────────────────────────────────────────

def _ensure_config_dirs():
    for d in [CONFIG_DIR, OUTBOX_DIR, BACKUP_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    _ensure_config_dirs()
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            try:
                CONFIG_FILE.rename(CONFIG_FILE.with_suffix(".corrupt.json"))
            except OSError:
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
# 5. LIVE DB SYNC & SANITIZATION
# ──────────────────────────────────────────────────────────────────────

def _normalize_gpu_entry(entry: dict) -> dict | None:
    if not isinstance(entry, dict): return None
    name = str(entry.get("name") or "").strip()
    if not name: return None
    ov = entry.get("override")
    if isinstance(ov, str) and not VERSION_RE.fullmatch(ov.strip()):
        ov = None
    if ov is not None and not isinstance(ov, str):
        ov = None
    return {
        "name": name,
        "arch": str(entry.get("arch") or "unknown"),
        "gfx_target": str(entry.get("gfx_target") or "unknown"),
        "override": ov,
        "supported": bool(entry.get("supported", False)),
        "rec_backend": str(entry.get("rec_backend") or "vulkan"),
        "known_issues": list(entry.get("known_issues") or []),
        "notes": str(entry.get("notes") or ""),
    }

def _validate_db(raw: dict) -> dict:
    gpus = (raw or {}).get("gpus") or {}
    out = {}
    for pid, entry in gpus.items():
        if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-fA-F]{4}", pid):
            continue
        norm = _normalize_gpu_entry(entry)
        if norm:
            out[pid.lower()] = norm
    return out

def sync_gpu_database(force: bool = False) -> str:
    global GPU_DATABASE
    _ensure_config_dirs()
    cfg = load_config()

    if not force and DB_CACHE_FILE.exists():
        last = cfg.get("last_db_sync")
        if last:
            try:
                if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=DB_CACHE_TTL_HOURS):
                    try:
                        raw = json.loads(DB_CACHE_FILE.read_text())
                        merged = dict(FALLBACK_DATABASE)
                        merged.update(_validate_db(raw))
                        GPU_DATABASE = merged
                        return "cached"
                    except Exception:
                        pass
            except Exception:
                pass

    for endpoint in DATABASE_ENDPOINTS:
        try:
            req = urllib.request.Request(endpoint, headers={"User-Agent": f"ROCmFix/{__version__}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read())
                fetched = _validate_db(raw)
                if fetched:
                    DB_CACHE_FILE.write_text(json.dumps({"version": raw.get("version"), "gpus": fetched}))
                    cfg["last_db_sync"] = datetime.now(timezone.utc).isoformat()
                    save_config(cfg)
                    merged = dict(FALLBACK_DATABASE)
                    merged.update(fetched)
                    GPU_DATABASE = merged
                    return "fetched"
        except Exception:
            continue

    if DB_CACHE_FILE.exists():
        try:
            raw = json.loads(DB_CACHE_FILE.read_text())
            merged = dict(FALLBACK_DATABASE)
            merged.update(_validate_db(raw))
            GPU_DATABASE = merged
            return "cached"
        except Exception:
            pass

    GPU_DATABASE = dict(FALLBACK_DATABASE)
    return "fallback"

def fetch_db_metadata_online() -> dict:
    """Fetch online database status without local file system modifications."""
    try:
        req = urllib.request.Request(DATABASE_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())
            version_str = str(raw.get("version") or "unknown")
            gpu_count = len(raw.get("gpus") or {})
            age_str = "unknown"
            try:
                db_date = datetime.strptime(version_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_old = (datetime.now(timezone.utc) - db_date).days
                age_str = f"{days_old} days old"
            except Exception:
                pass
            return {"status": "ok", "version": version_str, "gpu_count": gpu_count, "age": age_str}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# ──────────────────────────────────────────────────────────────────────
# 6. UPDATE CHECKER
# ──────────────────────────────────────────────────────────────────────

def _version_tuple(v: str) -> tuple:
    v = v.lstrip("v")
    try: return tuple(int(x) for x in v.split("."))
    except Exception: return (0, 0, 0)

def check_for_updates_silent() -> str | None:
    cfg = load_config()
    last = cfg.get("last_update_check")
    if last:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=UPDATE_CHECK_TTL_HOURS):
                return None
        except Exception: pass
    try:
        req = urllib.request.Request(UPDATE_CHECK_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latest = str(data.get("tag_name", "")).lstrip("v")
            cfg["last_update_check"] = datetime.now(timezone.utc).isoformat()
            save_config(cfg)
            if latest and _version_tuple(latest) > _version_tuple(__version__):
                return latest
    except Exception:
        pass
    return None

def maybe_print_update_banner():
    latest = check_for_updates_silent()
    if latest:
        print(f"  {C.YELLOW}[!] Update available: v{latest} (you have v{__version__}){C.RESET}")
        print(f"  {C.YELLOW}    Run 'rocmfix update' to install.{C.RESET}\n")

# ──────────────────────────────────────────────────────────────────────
# 7. HARDWARE & SYSTEM DETECTION
# ──────────────────────────────────────────────────────────────────────

def detect_os() -> str:
    s = platform.system().lower()
    return "windows" if s == "windows" else "linux" if s == "linux" else "macos" if s == "darwin" else "unknown"

def detect_shell() -> str:
    if detect_os() == "windows":
        if os.environ.get("PROMPT") and not os.environ.get("PSExecutionPolicyPreference"):
            return "cmd"
        if os.environ.get("PSModulePath"):
            return "powershell"
        return "cmd"
    s = os.environ.get("SHELL", "")
    if "fish" in s: return "fish"
    if "zsh" in s: return "zsh"
    return "bash"

def _detect_windows_registry() -> list[dict]:
    gpus = []
    try:
        import winreg
    except ImportError:
        return gpus
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as class_key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(class_key, i)
                    i += 1
                    if sub == "Properties": continue
                    try:
                        with winreg.OpenKey(class_key, sub) as sk:
                            try: desc = winreg.QueryValueEx(sk, "DriverDesc")[0]
                            except FileNotFoundError: continue
                            if not any(v in desc.lower() for v in ["amd", "radeon", "ati"]):
                                continue
                            pci_id, raw_pnp = "unknown", desc
                            try:
                                mid = winreg.QueryValueEx(sk, "MatchingDeviceId")[0]
                                raw_pnp = mid
                                m = re.search(r"VEN_1002&DEV_([0-9A-Fa-f]{4})", mid, re.IGNORECASE)
                                if m: pci_id = m.group(1).lower()
                            except FileNotFoundError: pass
                            driver = "unknown"
                            try: driver = winreg.QueryValueEx(sk, "DriverVersion")[0]
                            except FileNotFoundError: pass
                            gpus.append({"pci_id": pci_id, "name": desc, "driver_version": driver, "raw_pnp": raw_pnp})
                    except OSError:
                        continue
                except OSError:
                    break
    except OSError:
        pass
    return gpus

VENDOR_PREFIX_RE = re.compile(
    r"^(?:Advanced Micro Devices,\s*Inc\.?\s*\[AMD/ATI\]\s*|\[AMD\]\s*|ATI Technologies Inc\s*\[AMD/ATI\]\s*|AMD\s+)",
    re.IGNORECASE
)

def _clean_lspci_name(name: str) -> str:
    stripped = VENDOR_PREFIX_RE.sub("", name).strip()
    return stripped if stripped else name

def _get_linux_driver_version() -> str:
    vf = Path("/sys/module/amdgpu/version")
    if vf.exists():
        try:
            v = vf.read_text().strip()
            if v: return v
        except OSError: pass
    mod_ver = _run(["modinfo", "-F", "version", "amdgpu"]).strip()
    if mod_ver and "not found" not in mod_ver.lower() and "error" not in mod_ver.lower():
        return mod_ver
    kv = _run(["uname", "-r"]).strip()
    if kv:
        return f"Kernel {kv}"
    return "unknown"

def _detect_linux_sysfs(driver_ver: str) -> list[dict]:
    """Fallback for systems without lspci (containers, minimal installs)."""
    gpus = []
    for dev in sorted(glob.glob("/sys/bus/pci/devices/*")):
        try:
            if Path(dev, "vendor").read_text().strip().lower() != "0x1002": continue
            if not Path(dev, "class").read_text().strip().lower().startswith("0x03"): continue
            pci = Path(dev, "device").read_text().strip().lower().replace("0x", "")
            info = GPU_DATABASE.get(pci)
            gpus.append({"pci_id": pci, "name": info["name"] if info else f"AMD GPU [{pci}]",
                         "driver_version": driver_ver, "raw_pnp": os.path.basename(dev)})
        except OSError:
            continue
    return gpus

def _detect_linux() -> list[dict]:
    gpus = []
    if not shutil.which("lspci"):
        return _detect_linux_sysfs(_get_linux_driver_version())
    output = _run(["lspci", "-nn", "-d", "1002::"])
    pat = re.compile(r"\[1002:([0-9a-fA-F]{4})\]")
    driver_ver = _get_linux_driver_version()
    for line in output.splitlines():
        m = pat.search(line)
        if m and any(k in line for k in ["VGA", "Display", "3D"]):
            pci = m.group(1).lower()
            nm = re.search(r"\]:\s*(.+?)\s*\[1002:", line)
            name = _clean_lspci_name(nm.group(1)) if nm else "Unknown AMD GPU"
            gpus.append({"pci_id": pci, "name": name, "driver_version": driver_ver, "raw_pnp": line.strip()})
    return gpus

def detect_gpus() -> list[dict]:
    if detect_os() == "windows": return attach_gfx_targets(_detect_windows_registry())
    if detect_os() == "linux": return attach_gfx_targets(_detect_linux())
    return []

def detect_rocm_version():
    if detect_os() == "linux":
        for p in glob.glob("/opt/rocm*/.info/version"):
            try:
                v = Path(p).read_text().strip()
                if v: return v
            except OSError: pass
        out = _run(["rocminfo"])
        m = re.search(r"ROCm Version:\s*([\d.]+)", out)
        if m: return m.group(1)
        hipcc = _run(["hipcc", "--version"])
        m = re.search(r"HIP version:\s*([\d.]+)", hipcc)
        if m: return m.group(1)
        if out and "Agent" in out:
            return "installed (version unknown)"
        return None
    if detect_os() == "windows":
        hp = os.environ.get("HIP_PATH", "")
        if hp:
            m = re.search(r"[\\\/](\d+\.\d+)[\\\/]?$", hp.rstrip("\\/"))
            if m: return m.group(1)
    return None

def analyze_driver(drv_ver: str):
    if not drv_ver or drv_ver == "unknown": return None
    if drv_ver.startswith("Kernel") or drv_ver.startswith("installed"):
        return {"status": "ok", "msg": f"{drv_ver} (Linux/ROCm)"}
    try:
        major = int(drv_ver.split(".")[0])
        if major >= 32: return {"status": "ok", "msg": "Adrenalin 24.x+ (Good)"}
        if major == 31: return {"status": "warn", "msg": "Adrenalin 23.x (Update recommended)"}
        return {"status": "warn", "msg": f"Older driver ({major}.x)"}
    except Exception:
        return None

def _estimate_vram_tier(gpu_name: str) -> tuple[int, str]:
    """Analyze GPU model branding to estimate general VRAM size tier."""
    n = (gpu_name or "").lower()
    if any(k in n for k in ["7900 xtx", "7900xtx", "w7900"]): return (24, "flagship")
    if any(k in n for k in ["7900 xt", "9070 xt", "9070", "6900", "6800", "w7800", "w7700"]): return (16, "high_vram")
    if any(k in n for k in ["7800 xt", "7700 xt", "6700 xt"]): return (12, "mid_vram")
    return (8, "low_vram")

# ─── 7b. REAL GFX TARGET DETECTION + OVERRIDE RULES ─────────────────

def gfx_from_target_version(v: int) -> str | None:
    """KFD encodes gfx targets as major*10000 + minor*100 + stepping (e.g. 110001 -> gfx1101)."""
    if not v or v <= 0: return None
    major, minor, step = v // 10000, (v // 100) % 100, v % 100
    if minor > 15 or step > 15: return None
    return f"gfx{major}{minor:x}{step:x}"

def override_for_gfx(gfx: str) -> str | None:
    """gfx1030 -> 10.3.0, gfx90c -> 9.0.12 (the x.y.z form HSA_OVERRIDE_GFX_VERSION expects)."""
    m = GFX_RE.fullmatch(gfx or "")
    if not m: return None
    return f"{int(m.group(1))}.{int(m.group(2), 16)}.{int(m.group(3), 16)}"

def _norm_gpu_name(name: str) -> str:
    n = re.sub(r"\(.*?\)|amd|radeon|graphics|\bthe\b", " ", (name or "").lower())
    return " ".join(n.split())

def _detect_gfx_linux() -> dict[str, str]:
    """PCI device id -> gfx target, read from the amdgpu KFD topology (no ROCm needed)."""
    out = {}
    for props in glob.glob("/sys/class/kfd/kfd/topology/nodes/*/properties"):
        try:
            kv = {}
            for line in Path(props).read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("-").isdigit():
                    kv[parts[0]] = int(parts[1])
            if kv.get("vendor_id") != 0x1002: continue
            gfx = gfx_from_target_version(kv.get("gfx_target_version", 0))
            if gfx: out[f"{kv.get('device_id', 0):04x}"] = gfx
        except (OSError, ValueError):
            continue
    return out

def _detect_gfx_windows() -> list[dict]:
    """[{name, gfx}] from the HIP SDK's hipInfo.exe."""
    hip_path = os.environ.get("HIP_PATH", "")
    exe = os.path.join(hip_path, "bin", "hipInfo.exe") if hip_path else ""
    if not exe or not os.path.exists(exe): return []
    out, cur_name = [], None
    for line in _run([exe], timeout=20).splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "name": cur_name = v
        elif k == "gcnarchname":
            gfx = v.split(":")[0].strip().lower()
            if GFX_RE.fullmatch(gfx): out.append({"name": cur_name or "", "gfx": gfx})
    return out

def attach_gfx_targets(gpus: list[dict]) -> list[dict]:
    """Adds gfx_target + gfx_source to each detected GPU. Driver data beats the database."""
    os_name = detect_os()
    if os_name == "linux":
        found = _detect_gfx_linux()
        for g in gpus:
            if g["pci_id"] in found:
                g["gfx_target"], g["gfx_source"] = found[g["pci_id"]], "kfd"
    elif os_name == "windows":
        hip = _detect_gfx_windows()
        for g in gpus:
            gn = _norm_gpu_name(g["name"])
            for h in hip:
                hn = _norm_gpu_name(h["name"])
                if gn and hn and (gn == hn or gn in hn or hn in gn):
                    g["gfx_target"], g["gfx_source"] = h["gfx"], "hipinfo"
                    break
            else:
                if len(hip) == 1 and len(gpus) == 1:
                    g["gfx_target"], g["gfx_source"] = hip[0]["gfx"], "hipinfo"
    for g in gpus:
        if not g.get("gfx_target"):
            info = GPU_DATABASE.get(g["pci_id"]) or {}
            gfx = info.get("gfx_target")
            g["gfx_target"] = gfx if gfx and GFX_RE.fullmatch(gfx) else None
            g["gfx_source"] = "database" if g["gfx_target"] else None
    return gpus

def _rocm_major(rocm_ver: str | None) -> int | None:
    m = re.match(r"(\d+)\.", rocm_ver or "")
    return int(m.group(1)) if m else None

def recommend(gpu: dict, info: dict | None, rocm_ver: str | None, os_name: str | None = None) -> dict:
    """
    Works out what this GPU needs. status is one of:
      native   - ROCm/HIP supports it as-is
      override - needs HSA_OVERRIDE_GFX_VERSION (Linux only)
      legacy   - only older ROCm supports it
      vulkan   - no working ROCm path, use Vulkan
      igpu     - too small to be useful for AI
      unknown  - gfx target unknown
    """
    os_name = os_name or detect_os()
    gfx = gpu.get("gfx_target") or (info or {}).get("gfx_target")
    if gfx and not GFX_RE.fullmatch(gfx): gfx = None
    rec = {"status": "unknown", "gfx": gfx, "gfx_source": gpu.get("gfx_source"),
           "override": None, "rec_backend": (info or {}).get("rec_backend") or "vulkan", "notes": []}
    if info and info.get("notes"): rec["notes"].append(info["notes"])

    if info and info.get("rec_backend") == "cpu":
        rec["status"] = "igpu"
    elif not gfx:
        if info and info.get("override"):
            rec.update(status="override", override=info["override"])
        elif info and info.get("supported"):
            rec["status"] = "native"
    elif os_name == "windows" and gfx in WINDOWS_HIP_GFX:
        rec["status"] = "native"
    elif gfx in NATIVE_GFX:
        rec["status"] = "native"
        major = _rocm_major(rocm_ver)
        fb = RECENT_NATIVE_FALLBACK.get(gfx)
        if fb and os_name == "linux" and major is not None and major < 7:
            rec.update(status="override", override=fb)
            rec["notes"].append(f"ROCm {rocm_ver} predates native {gfx} support. Upgrading to ROCm 7 removes the need for an override.")
    elif gfx in LEGACY_GFX:
        rec.update(status="legacy", rec_backend="vulkan")
    else:
        for pat, ov in FAMILY_OVERRIDES:
            if pat.fullmatch(gfx):
                rec.update(status="override", override=ov)
                break
        else:
            rec.update(status="vulkan", rec_backend="vulkan")

    return rec

def vulkan_smoke_test_ctypes() -> dict:
    """Uses ctypes to dynamically link and enumerate physical devices from Vulkan runtimes."""
    try:
        os_name = detect_os()
        if os_name == "windows":
            vk = ctypes.CDLL("vulkan-1.dll")
        elif os_name == "linux":
            try:
                vk = ctypes.CDLL("libvulkan.so.1")
            except OSError:
                vk = ctypes.CDLL("libvulkan.so")
        else:
            return {"success": False, "device_count": 0, "message": "Unsupported OS"}
    except OSError as e:
        return {"success": False, "device_count": 0, "message": f"Vulkan API loader missing: {e}"}

    class VkApplicationInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_int),
            ("pNext", ctypes.c_void_p),
            ("pApplicationName", ctypes.c_char_p),
            ("applicationVersion", ctypes.c_uint32),
            ("pEngineName", ctypes.c_char_p),
            ("engineVersion", ctypes.c_uint32),
            ("apiVersion", ctypes.c_uint32),
        ]

    class VkInstanceCreateInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_int),
            ("pNext", ctypes.c_void_p),
            ("flags", ctypes.c_uint32),
            ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
            ("enabledLayerCount", ctypes.c_uint32),
            ("ppEnabledLayerNames", ctypes.c_void_p),
            ("enabledExtensionCount", ctypes.c_uint32),
            ("ppEnabledExtensionNames", ctypes.c_void_p),
        ]

    VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
    VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1
    VK_API_VERSION_1_0 = (1 << 22)

    try:
        app_info = VkApplicationInfo(
            sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
            pNext=None,
            pApplicationName=b"rocmfix",
            applicationVersion=0,
            pEngineName=b"rocmfix",
            engineVersion=0,
            apiVersion=VK_API_VERSION_1_0,
        )
        create_info = VkInstanceCreateInfo(
            sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
            pNext=None,
            flags=0,
            pApplicationInfo=ctypes.pointer(app_info),
            enabledLayerCount=0,
            ppEnabledLayerNames=None,
            enabledExtensionCount=0,
            ppEnabledExtensionNames=None,
        )
        instance = ctypes.c_void_p()
        res = vk.vkCreateInstance(ctypes.byref(create_info), None, ctypes.byref(instance))
        if res != 0:
            return {"success": False, "device_count": 0, "message": f"vkCreateInstance failed (code {res})"}

        count = ctypes.c_uint32(0)
        vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None)
        device_count = count.value
        vk.vkDestroyInstance(instance, None)

        if device_count == 0:
            return {"success": False, "device_count": 0, "message": "Vulkan runtimes loaded but found 0 physical devices"}
        return {"success": True, "device_count": device_count, "message": f"Vulkan detected {device_count} device(s)"}
    except Exception as e:
        return {"success": False, "device_count": 0, "message": f"Dynamic API test failed: {e}"}

# ──────────────────────────────────────────────────────────────────────
# 8. SMOKE TESTING
# ──────────────────────────────────────────────────────────────────────

GPU_AGENT_RE = re.compile(r"Device Type:\s+GPU\b")

def run_smoke_test(override):
    os_name = detect_os()
    env = os.environ.copy()
    if override: env["HSA_OVERRIDE_GFX_VERSION"] = override
    else: env.pop("HSA_OVERRIDE_GFX_VERSION", None)

    if os_name == "linux":
        try:
            res = subprocess.run(["rocminfo"], capture_output=True, text=True,
                                 env=env, timeout=15, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return {"success": False, "message": "rocminfo not found.", "details": "Run 'rocmfix install-rocm'"}
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}
        if res.returncode != 0 or "Agent" not in res.stdout:
            return {"success": False, "message": "rocminfo failed.", "details": (res.stderr or res.stdout)[:300]}
        gpu_count = len(GPU_AGENT_RE.findall(res.stdout))
        if gpu_count == 0:
            kfd = Path("/dev/kfd")
            rnodes = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []
            if not kfd.exists() or not rnodes:
                return {"success": False, "message": "0 GPU agents.",
                        "details": "amdgpu kernel driver may not be loaded."}
            perms_ok = os.access(str(kfd), os.R_OK | os.W_OK) and all(os.access(str(n), os.R_OK | os.W_OK) for n in rnodes)
            if perms_ok:
                return {"success": False, "message": "0 GPU agents.",
                        "details": "Try HSA_OVERRIDE_GFX_VERSION or restart ROCm user services."}
            groups = _run(["groups"])
            missing = [g for g in ("render", "video") if g not in groups]
            fix = "sudo usermod -aG render,video $USER"
            return {"success": False, "message": "0 GPU agents.",
                    "details": f"Missing groups: {', '.join(missing) or 'unknown'}. Run: {fix}"}
        return {"success": True, "message": f"ROCm detected {gpu_count} GPU agent(s).",
                "details": f"override={override or 'not set'}"}

    if os_name == "windows":
        hip_path = os.environ.get("HIP_PATH", "")
        hipinfo_valid = False
        if hip_path:
            hipinfo = os.path.join(hip_path, "bin", "hipInfo.exe")
            if os.path.exists(hipinfo):
                hipinfo_valid = True
                try:
                    res = subprocess.run([hipinfo], capture_output=True, text=True, env=env,
                                         timeout=15, encoding="utf-8", errors="replace")
                except Exception as e:
                    return {"success": False, "message": str(e), "details": ""}
                if res.returncode == 0 and "device" in res.stdout.lower():
                    return {"success": True, "message": "HIP SDK detected your GPU.",
                            "details": f"override={override or 'not set'}"}

        # Fallback to pure ctypes-based Vulkan verification if HIP driver tools are missing
        vk_res = vulkan_smoke_test_ctypes()
        if vk_res["success"]:
            notes = " (HIP missing, fallback Vulkan interface operational)" if not hipinfo_valid else ""
            return {"success": True, "message": f"{vk_res['message']}{notes}", "details": f"override={override or 'not set'}"}

        if not hip_path:
            return {"success": False, "message": "HIP_PATH env missing + Vulkan pipeline failure.", "details": "Run 'rocmfix install-hip'"}
        return {"success": False, "message": "HIP diagnostics failed and Vulkan interfaces failed to link.", "details": f"Vulkan status: {vk_res['message']}"}

    return {"success": False, "message": "Unsupported OS", "details": ""}

# ──────────────────────────────────────────────────────────────────────
# 9. AUTO-APPLY & UNDO
# ──────────────────────────────────────────────────────────────────────

def _backup_file(path: Path):
    _ensure_config_dirs()
    if not path.exists(): return None
    bp = BACKUP_DIR / f"{path.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    shutil.copy2(path, bp)
    return bp

ROCMFIX_MARKER = "# Added by ROCmFix"
DISABLED_PREFIX = "# [ROCmFix disabled] "
SYSTEMD_DROPIN = Path("/etc/systemd/system/ollama.service.d/rocmfix.conf")

_ENV_LINE_RE = re.compile(r'^\s*(?:export\s+|set\s+-gx\s+|set\s+-xg\s+)?HSA_OVERRIDE_GFX_VERSION\b')

def _shell_rc_file(shell: str) -> tuple[Path, str]:
    home = Path.home()
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish", "set -gx HSA_OVERRIDE_GFX_VERSION {}"
    if shell == "zsh":
        return home / ".zshrc", "export HSA_OVERRIDE_GFX_VERSION={}"
    return home / ".bashrc", "export HSA_OVERRIDE_GFX_VERSION={}"

def apply_override_unix(override: str, shell: str) -> dict:
    if not VERSION_RE.fullmatch(override):
        return {"success": False, "error": "Invalid override value"}
    cfg, template = _shell_rc_file(shell)
    try:
        existed = cfg.exists()
        backup = _backup_file(cfg) if existed else None
        existing = cfg.read_text() if existed else ""
        out = []
        for l in existing.splitlines(keepends=True):
            if ROCMFIX_MARKER in l: continue  # our own earlier line gets replaced
            if _ENV_LINE_RE.match(l):
                # The user's own override is commented out, not deleted, so undo can bring it back.
                out.append(DISABLED_PREFIX + (l if l.endswith("\n") else l + "\n"))
                continue
            out.append(l)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg, "w") as f:
            f.writelines(out)
            if out and not out[-1].endswith("\n"): f.write("\n")
            f.write(f"{template.format(override)}  {ROCMFIX_MARKER}\n")
        return {"success": True, "backup": str(backup) if backup else None,
                "file": str(cfg), "existed_before": existed, "method": f"unix_{shell}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def _undo_unix_file(tp: Path):
    """Removes our line and re-enables any line we commented out. Never deletes the file."""
    new_lines = []
    for l in tp.read_text().splitlines(keepends=True):
        if ROCMFIX_MARKER in l: continue
        if l.startswith(DISABLED_PREFIX): l = l[len(DISABLED_PREFIX):]
        new_lines.append(l)
    tp.write_text("".join(new_lines))

def ollama_systemd_unit() -> bool:
    """True when Ollama runs as a systemd service (the default Linux install)."""
    if detect_os() != "linux" or not shutil.which("systemctl"): return False
    out, _, rc = safe_run(["systemctl", "list-unit-files", "ollama.service", "--no-legend"], timeout=10)
    return rc == 0 and "ollama.service" in out

def _sudo(cmd: list[str], input_text: str | None = None) -> bool:
    # Not captured, so sudo can ask for a password on the terminal.
    try:
        r = subprocess.run(["sudo"] + cmd, input=input_text, text=True,
                           stdout=subprocess.DEVNULL if input_text is not None else None)
        return r.returncode == 0
    except Exception:
        return False

def apply_override_systemd(override: str) -> dict:
    """Services don't read ~/.bashrc, so Ollama's unit gets a drop-in with the override."""
    if not VERSION_RE.fullmatch(override):
        return {"success": False, "error": "Invalid override value"}
    content = f'[Service]\nEnvironment="HSA_OVERRIDE_GFX_VERSION={override}"\n'
    ok = (_sudo(["mkdir", "-p", str(SYSTEMD_DROPIN.parent)])
          and _sudo(["tee", str(SYSTEMD_DROPIN)], input_text=content)
          and _sudo(["systemctl", "daemon-reload"])
          and _sudo(["systemctl", "restart", "ollama"]))
    return {"success": ok, "file": str(SYSTEMD_DROPIN)} if ok else {"success": False, "error": "sudo step failed"}

def undo_override_systemd() -> bool:
    return (_sudo(["rm", "-f", str(SYSTEMD_DROPIN)])
            and _sudo(["systemctl", "daemon-reload"])
            and _sudo(["systemctl", "restart", "ollama"]))

def apply_override_windows(override: str) -> dict:
    """Sets the variable for the current user. Ollama's bundled ROCm on Windows reads it."""
    if not VERSION_RE.fullmatch(override):
        return {"success": False, "error": "Invalid override value"}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        try: old_value, _ = winreg.QueryValueEx(key, "HSA_OVERRIDE_GFX_VERSION")
        except FileNotFoundError: old_value = None
        winreg.SetValueEx(key, "HSA_OVERRIDE_GFX_VERSION", 0, winreg.REG_SZ, override)
        winreg.CloseKey(key)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
        return {"success": True, "old_value": old_value, "method": "windows_registry",
                "file": r"HKCU\Environment"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def apply_override(override: str, systemd: bool = False) -> dict:
    if detect_os() == "windows":
        res = apply_override_windows(override)
    else:
        res = apply_override_unix(override, detect_shell())
    if res.get("success") and systemd:
        sd = apply_override_systemd(override)
        res["systemd_dropin"] = sd.get("file") if sd.get("success") else None
        res["systemd_error"] = None if sd.get("success") else sd.get("error")
    if res.get("success"):
        cfg = load_config()
        cfg["applied_overrides"].append({
            "value": override,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": res.get("method"),
            "backup": res.get("backup"),
            "file": res.get("file"),
            "existed_before": res.get("existed_before"),
            "old_value": res.get("old_value"),
            "systemd_dropin": res.get("systemd_dropin"),
        })
        save_config(cfg)
    return res

def undo_last_override() -> dict:
    cfg = load_config()
    if not cfg["applied_overrides"]:
        return {"success": False, "error": "Nothing to undo."}
    last = cfg["applied_overrides"][-1]

    if last.get("method") == "windows_registry":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            if last.get("old_value"):
                winreg.SetValueEx(key, "HSA_OVERRIDE_GFX_VERSION", 0, winreg.REG_SZ, last["old_value"])
            else:
                try: winreg.DeleteValue(key, "HSA_OVERRIDE_GFX_VERSION")
                except FileNotFoundError: pass
            winreg.CloseKey(key)
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
            cfg["applied_overrides"].pop()
            save_config(cfg)
            return {"success": True, "method": "windows_registry"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    notes = []
    if last.get("systemd_dropin"):
        if undo_override_systemd(): notes.append(f"removed {last['systemd_dropin']} and restarted ollama")
        else: return {"success": False, "error": f"Could not remove {last['systemd_dropin']} (sudo failed)."}

    target = last.get("file")
    if target:
        tp = Path(target)
        try:
            if tp.exists(): _undo_unix_file(tp)
        except Exception as e:
            return {"success": False, "error": str(e)}
    cfg["applied_overrides"].pop()
    save_config(cfg)
    return {"success": True, "restored_from": last.get("backup"), "notes": notes}

# ──────────────────────────────────────────────────────────────────────
# 10. TELEMETRY SYSTEM
# ──────────────────────────────────────────────────────────────────────

# Benchmark keys that are safe to share. Model names and server URLs never leave the machine.
_BENCH_SHARED_KEYS = {"runtime", "model_class", "vulkan", "hip", "vulkan_toks", "hip_toks", "winner",
                      "single_backend", "backend", "measurement", "runs", "max_tokens"}

def sanitize_benchmark(benchmark: dict | None) -> dict | None:
    if not benchmark: return None
    return {k: v for k, v in benchmark.items() if k in _BENCH_SHARED_KEYS}

def build_telemetry_payload(gpu, info, rocm_ver, smoke_result=None, benchmark=None):
    cfg = load_config()
    rec = recommend(gpu, info, rocm_ver)
    payload = {
        "schema_version": 4, "user_id": cfg["user_id"], "rocmfix_version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "os": detect_os(), "shell": detect_shell(),
        "gpu": {"name": gpu.get("name"), "pci_id": gpu.get("pci_id"),
                "driver_version": gpu.get("driver_version"),
                "gfx_target": rec["gfx"], "gfx_source": rec["gfx_source"]},
        "database": {"in_database": info is not None,
                     "status": rec["status"],
                     "override_recommended": rec["override"],
                     "supported_native": rec["status"] == "native"},
        "rocm_version": rocm_ver,
        "smoke_test": smoke_result if smoke_result else None,
    }
    if benchmark:
        payload["benchmark"] = sanitize_benchmark(benchmark)
    return payload

def _queue_payload(payload):
    _ensure_config_dirs()
    files = sorted(OUTBOX_DIR.glob("*.json"))
    while len(files) >= 100:
        try: files[0].unlink()
        except Exception: pass
        files = sorted(OUTBOX_DIR.glob("*.json"))
    (OUTBOX_DIR / f"payload_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}.json").write_text(json.dumps(payload))

def _send_payload(payload) -> bool:
    if "YOURNAME" in TELEMETRY_ENDPOINT: return False
    try:
        req = urllib.request.Request(
            TELEMETRY_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": f"ROCmFix/{__version__}"},
        )
        with urllib.request.urlopen(req, timeout=TELEMETRY_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500:
            return True
        return False
    except Exception:
        return False

def flush_outbox():
    if not OUTBOX_DIR.exists(): return 0, 0
    sent, failed = 0, 0
    for f in sorted(OUTBOX_DIR.glob("*.json")):
        try:
            ok = _send_payload(json.loads(f.read_text()))
        except Exception:
            try: f.unlink()
            except Exception: pass
            continue
        if ok:
            try: f.unlink()
            except Exception: pass
            sent += 1
        else:
            failed += 1
            break
    return sent, failed

def send_telemetry(payload):
    cfg = load_config()
    if not cfg.get("telemetry_enabled"): return
    if not _send_payload(payload):
        _queue_payload(payload)
    try: flush_outbox()
    except Exception: pass

def show_telemetry_sample(gpu, info, rocm_ver):
    p = build_telemetry_payload(gpu, info, rocm_ver)
    print(f"\n  {C.BOLD}Exact JSON that would be sent:{C.RESET}")
    print(f"  {C.GRAY}{'─'*60}{C.RESET}")
    for line in json.dumps(p, indent=2).splitlines(): print(f"    {line}")
    print(f"  {C.GRAY}{'─'*60}{C.RESET}")
    print(f"  {C.GRAY}Endpoint: {TELEMETRY_ENDPOINT}{C.RESET}\n")

def prompt_telemetry_optin() -> bool:
    print(f"\n{C.BOLD}{C.CYAN}📊 Help improve ROCmFix?{C.RESET}\n")
    print("  ROCmFix can anonymously share GPU detection + benchmark numbers.")
    print("  A random install ID is generated. Model names, file paths and prompts")
    print("  are never sent. Like any web request, the server sees your IP, but it")
    print("  is not stored with the report. Details: PRIVACY.md\n")
    print(f"  [{C.GREEN}Y{C.RESET}] Yes  [{C.RED}N{C.RESET}] No  [{C.CYAN}?{C.RESET}] Show payload\n")
    while True:
        try: ans = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt): return False
        if ans == "y": return True
        if ans == "n": return False
        if ans == "?":
            gpus = detect_gpus()
            if gpus:
                show_telemetry_sample(gpus[0], GPU_DATABASE.get(gpus[0]["pci_id"]), detect_rocm_version())

# ─── 11. GLOBAL INSTALLER ─────────────────────────────────
def is_installed_globally(): return shutil.which("rocmfix") is not None

def install_globally() -> bool:
    os_name = detect_os()
    script_path = Path(__file__).resolve()
    script_dir = script_path.parent
    print(f"\n{C.BOLD}{C.CYAN}⚙️  Register 'rocmfix' as a global command?{C.RESET}")
    try:
        if input("  Install globally? [Y/n]: ").strip().lower() == "n": return False
    except Exception: return False

    if os_name == "windows":
        try:
            (script_dir / "rocmfix.bat").write_text(f'@echo off\npython "{script_path}" %*\n')
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            try: path_val, _ = winreg.QueryValueEx(key, "Path")
            except Exception: path_val = ""
            if str(script_dir) not in path_val:
                new_path = f"{path_val};{script_dir}" if path_val else str(script_dir)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
            winreg.CloseKey(key)
            print(f"\n  {C.GREEN}✓ Registered! Open a NEW terminal to use 'rocmfix'.{C.RESET}\n")
            return True
        except Exception as e:
            print(f"{C.RED}Failed: {e}{C.RESET}"); return False
    else:
        try:
            local_bin = Path.home() / ".local" / "bin"
            local_bin.mkdir(parents=True, exist_ok=True)
            dest = local_bin / "rocmfix"
            shutil.copy2(script_path, dest)
            dest.chmod(0o755)
            print(f"\n  {C.GREEN}✓ Installed to {dest}{C.RESET}")
            return True
        except Exception as e:
            print(f"{C.RED}Failed: {e}{C.RESET}"); return False

def format_env_command(override):
    if detect_os() == "windows":
        return f'  $env:HSA_OVERRIDE_GFX_VERSION="{override}"' if detect_shell() == "powershell" else f"  set HSA_OVERRIDE_GFX_VERSION={override}"
    return f"  set -gx HSA_OVERRIDE_GFX_VERSION {override}" if detect_shell() == "fish" else f"  export HSA_OVERRIDE_GFX_VERSION={override}"

def get_issue_url(gpu, rocm_ver):
    body = f"## Unknown GPU\n\n- **Name:** {gpu.get('name')}\n- **PCI ID:** `{gpu.get('pci_id')}`\n- **Driver:** {gpu.get('driver_version')}\n- **OS:** {detect_os()}\n"
    params = urllib.parse.urlencode({"title": f"[GPU Report] {gpu.get('name','?')} ({gpu.get('pci_id','?')})", "body": body, "labels": "gpu-report"})
    return f"https://github.com/{GITHUB_REPO}/issues/new?{params}"

# ─── 12. BACKEND BENCHMARK (VULKAN VS HIP) ─────────────
BENCH_PROMPT = "Write a 150 word story about a robot who learns to paint. Use plain prose."
BENCH_MAX_TOKENS = 128
BENCH_RUNS = 3

def _popen_kwargs() -> dict:
    if detect_os() == "windows": return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {"start_new_session": True}

def _kill_tree(proc):
    """ollama serve spawns runner processes that hold VRAM, so the whole tree has to go."""
    try:
        if detect_os() == "windows":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        try: proc.kill()
        except Exception: pass

def _median(vals):
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals: return None
    mid = len(vals) // 2
    m = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return round(m, 3)

def _summarize_runs(runs: list[dict]) -> dict:
    keys = {k for r in runs for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    out = {k: _median([r.get(k) for r in runs]) for k in sorted(keys)}
    out["runs"] = len(runs)
    return out

def model_class(model_id: str | None, details: dict | None = None) -> str | None:
    """'8B Q4_K_M' style label. Shared in telemetry instead of the model name."""
    details = details or {}
    params = details.get("parameter_size")
    quant = details.get("quantization_level") or details.get("quantization")
    name = (model_id or "").lower()
    if not params:
        m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?![a-z])", name)
        if m: params = f"{m.group(1)}B"
    if not quant:
        m = re.search(r"(?<![a-z])(i?q\d(?:_[a-z0-9]+)*|bf16|fp16|f16|f32)(?![a-z0-9])", name)
        if m: quant = m.group(1).upper()
    parts = [p for p in (str(params).upper() if params else None, str(quant).upper() if quant else None) if p]
    return " ".join(parts) or None

def _model_params_b(mclass: str | None) -> float | None:
    m = re.match(r"(\d+(?:\.\d+)?)B\b", mclass or "")
    return float(m.group(1)) if m else None

def _backend_from_runtime_name(name: str | None) -> str:
    n = (name or "").lower()
    if "vulkan" in n: return "vulkan"
    if "rocm" in n or "hip" in n: return "hip"
    if "cuda" in n: return "cuda"
    if "metal" in n: return "metal"
    if "cpu" in n: return "cpu"
    return "unknown"

def _load_bench_history() -> dict:
    try: return json.loads(BENCH_HISTORY_FILE.read_text())
    except Exception: return {}

def _save_bench_history(hist: dict):
    try:
        _ensure_config_dirs()
        BENCH_HISTORY_FILE.write_text(json.dumps(hist, indent=2))
    except OSError:
        pass

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

def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None

def _stream_chat_completions(base_url: str, payload: dict, timeout: int = 180) -> dict:
    """Streams from an OpenAI-compatible endpoint. Uses the server's own token timings when it sends them."""
    payload = dict(payload)
    payload["stream"] = True
    payload["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "Authorization": "Bearer rocmfix"},
    )

    start_time = time.time()
    first_token_time = last_token_time = None
    chunk_count = 0
    usage, timings = {}, {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"): continue
                body = line[5:].strip()
                if body == "[DONE]": break
                try: chunk = json.loads(body)
                except Exception: continue
                if chunk.get("usage"): usage = chunk["usage"]
                if chunk.get("timings"): timings = chunk["timings"]
                choices = chunk.get("choices") or []
                if choices and (choices[0].get("delta") or {}).get("content"):
                    now = time.time()
                    if first_token_time is None: first_token_time = now
                    last_token_time = now
                    chunk_count += 1
    except Exception as e:
        return {"error": f"Streaming request failed: {e}"}

    if first_token_time is None:
        return {"error": "Server did not stream back any content tokens."}

    out = {"ttft_s": round(first_token_time - start_time, 3),
           "total_time_s": round(max(time.time() - start_time, 0.001), 3)}
    if timings.get("predicted_per_second"):
        # llama.cpp server reports exact prompt/decode speeds
        out["decode_toks_per_s"] = round(timings["predicted_per_second"], 2)
        out["decode_toks"] = timings.get("predicted_n")
        if timings.get("prompt_per_second"):
            out["prompt_toks_per_s"] = round(timings["prompt_per_second"], 2)
    else:
        decode_duration = max((last_token_time or first_token_time) - first_token_time, 0.001)
        tokens = usage.get("completion_tokens") or chunk_count
        # first token arrives at TTFT, so the decode window covers tokens - 1
        out["decode_toks"] = tokens
        out["decode_toks_per_s"] = round(max(tokens - 1, 1) / decode_duration, 2)
    return out

def check_cpu_fallback(gpu_name: str, best_decode_toks_s: float, mclass: str | None) -> dict | None:
    """Flags decode speeds that look like CPU execution. Only meaningful for small (<=9B) models."""
    params = _model_params_b(mclass)
    if best_decode_toks_s <= 0 or params is None or params > 9:
        return None
    vram_gb, tier = _estimate_vram_tier(gpu_name)
    thresh = CPU_FALLBACK_HEURISTIC.get(tier, {}).get("min_expected_toks", 10)

    if best_decode_toks_s < thresh * 0.4:
        return {
            "level": "critical",
            "message": (f"Measured speed ({best_decode_toks_s} t/s) is far below "
                        f"the ~{thresh}+ t/s expected for a {params:g}B model on {gpu_name}. "
                        f"This is most likely running on the CPU. Check overrides.")
        }
    elif best_decode_toks_s < thresh * 0.7:
        return {
            "level": "warn",
            "message": (f"Measured speed ({best_decode_toks_s} t/s) is low "
                        f"for {gpu_name}. Some layers may be offloaded to the CPU.")
        }
    return None

def _empty_results(runtime: str, model: str, mclass: str | None) -> dict:
    return {"runtime": runtime, "model": model, "model_class": mclass,
            "vulkan": {}, "hip": {}, "vulkan_toks": 0.0, "hip_toks": 0.0,
            "winner": "n/a", "runs": BENCH_RUNS, "max_tokens": BENCH_MAX_TOKENS}

def _pick_winner(results: dict):
    vk, hp = results["vulkan_toks"], results["hip_toks"]
    if vk > 0 and hp > 0:
        results["winner"] = "vulkan" if vk > hp else "hip" if hp > vk else "tie"
    else:
        results["winner"] = "n/a"

def _lms_chat_v0(model: str, timeout: int = 300) -> dict:
    """LM Studio's REST v0 API returns its own token stats and the runtime that ran the request."""
    try:
        data = _lms_api("POST", "/api/v0/chat/completions", {
            "model": model, "messages": [{"role": "user", "content": BENCH_PROMPT}],
            "max_tokens": BENCH_MAX_TOKENS, "temperature": 0.0, "stream": False,
        }, timeout=timeout)
    except Exception as e:
        return {"error": f"LM Studio request failed: {e}"}
    stats, usage = data.get("stats") or {}, data.get("usage") or {}
    rt = data.get("runtime")
    if isinstance(rt, dict): rt = rt.get("name")
    if not stats.get("tokens_per_second"):
        return {"error": "LM Studio returned no token stats (needs LM Studio 0.3.6 or newer)."}
    return {"decode_toks_per_s": round(stats["tokens_per_second"], 2),
            "ttft_s": round(stats.get("time_to_first_token") or 0, 3),
            "decode_toks": usage.get("completion_tokens"),
            "prompt_toks": usage.get("prompt_tokens"),
            "_runtime_name": rt}

def bench_via_lmstudio(gpu_override: str | None) -> dict:
    """
    Benchmarks LM Studio's currently selected runtime. LM Studio only switches runtimes through
    the interactive `lms runtime select`, so the other backend is compared from the last run.
    """
    lms_cli = shutil.which("lms") is not None
    try:
        _lms_api("GET", "/v1/models", timeout=3)
        server_running = True
    except Exception:
        server_running = False

    if not server_running and lms_cli:
        print(f"  {C.GRAY}Starting LM Studio server via CLI...{C.RESET}")
        try:
            subprocess.Popen(["lms", "server", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, **_popen_kwargs())
            server_running = wait_for_port("127.0.0.1", 1234, timeout=15)
        except Exception:
            pass
    if not server_running:
        return {"error": "LM Studio server is not running on port 1234. Start LM Studio and enable the local server."}

    chosen_model, details = None, {}
    try:
        models = [m for m in (_lms_api("GET", "/api/v0/models", timeout=5).get("data") or []) if m.get("type") in ("llm", "vlm")]
        models.sort(key=lambda m: m.get("state") != "loaded")
        if models:
            chosen_model = models[0].get("id")
            details = {"quantization": models[0].get("quantization")}
    except Exception:
        pass
    if not chosen_model:
        try:
            ids = [m.get("id") for m in (_lms_api("GET", "/v1/models", timeout=5).get("data") or []) if m.get("id")]
            ids = [i for i in ids if "embed" not in i.lower()]
            chosen_model = ids[0] if ids else None
        except Exception:
            pass
    if not chosen_model:
        return {"error": "No models found in LM Studio. Download a model in LM Studio first."}

    mclass = model_class(chosen_model, details)
    print(f"  Model: {chosen_model}")
    results = _empty_results("lm_studio", chosen_model, mclass)

    print(f"  Warming up (loads the model)...")
    warm = _lms_chat_v0(chosen_model, timeout=600)
    if "error" in warm:
        return {"error": warm["error"]}
    backend_key = _backend_from_runtime_name(warm.get("_runtime_name"))
    print(f"  Runtime: {warm.get('_runtime_name') or 'unknown'} → {C.BOLD}{backend_key.upper()}{C.RESET}\n")

    runs = []
    for i in range(BENCH_RUNS):
        r = _lms_chat_v0(chosen_model)
        if "error" in r:
            print(f"    {C.RED}→ run {i+1}: {r['error']}{C.RESET}")
            continue
        r.pop("_runtime_name", None)
        runs.append(r)
        print(f"    run {i+1}: decode {r['decode_toks_per_s']} tok/s | TTFT {r['ttft_s']*1000:.0f}ms")
    if not runs:
        return {"error": "All benchmark runs failed."}
    summary = _summarize_runs(runs)
    summary["backend_verified"] = backend_key in ("vulkan", "hip")

    if backend_key not in ("vulkan", "hip"):
        results["single_backend"] = True
        results["backend"] = backend_key
        results["measurement"] = summary
        return results

    results[backend_key] = summary
    results[f"{backend_key}_toks"] = summary.get("decode_toks_per_s") or 0.0

    # Pair with the last run of the other backend on the same model, if there is one.
    hist = _load_bench_history()
    key = f"lm_studio|{chosen_model}"
    entry = hist.get(key, {})
    entry[backend_key] = dict(summary, measured_at=datetime.now(timezone.utc).isoformat())
    hist[key] = entry
    _save_bench_history(hist)
    other = "hip" if backend_key == "vulkan" else "vulkan"
    if entry.get(other):
        prev = dict(entry[other])
        prev["from_previous_run"] = prev.pop("measured_at", None)
        results[other] = prev
        results[f"{other}_toks"] = prev.get("decode_toks_per_s") or 0.0
    else:
        results["compare_hint"] = (f"To compare with {other.upper()}, run `lms runtime select`, pick a "
                                   f"{'ROCm' if other == 'hip' else 'Vulkan'} llama.cpp runtime, then run `rocmfix bench` again.")
    _pick_winner(results)
    return results

def bench_via_llamacpp(gpu_override: str | None, custom_url: str | None = None) -> dict:
    """Benchmarks an already-running llama.cpp server (whichever backend it was built with)."""
    target_url = (custom_url or os.environ.get("ROCMFIX_LLAMA_CPP_URL") or "http://127.0.0.1:8080").rstrip("/")
    try:
        req = urllib.request.Request(f"{target_url}/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            models_data = json.loads(resp.read())
    except Exception as e:
        return {"error": f"llama.cpp server not accessible at {target_url}: {e}"}

    models_list = [m.get("id") for m in (models_data.get("data") or []) if m.get("id")]
    active_model = models_list[0] if models_list else "unknown"
    print(f"  Model: {active_model}")
    print(f"  URL:   {target_url}\n")

    prompt_payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": BENCH_PROMPT}],
        "max_tokens": BENCH_MAX_TOKENS,
        "temperature": 0.0,
    }
    _stream_chat_completions(target_url, prompt_payload, timeout=300)  # warm-up
    runs = []
    for i in range(BENCH_RUNS):
        m = _stream_chat_completions(target_url, prompt_payload, timeout=180)
        if "error" in m:
            print(f"    {C.RED}→ run {i+1}: {m['error']}{C.RESET}")
            continue
        runs.append(m)
        print(f"    run {i+1}: decode {m['decode_toks_per_s']} tok/s | TTFT {m['ttft_s']*1000:.0f}ms")
    if not runs:
        return {"error": "All benchmark runs failed."}

    results = _empty_results("llama_cpp", active_model, model_class(active_model))
    results.update(single_backend=True, backend="unknown", measurement=_summarize_runs(runs))
    return results

def _ollama_generate(port: int, model: str, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/generate",
        data=json.dumps({"model": model, "prompt": BENCH_PROMPT, "stream": False,
                         "options": {"num_predict": BENCH_MAX_TOKENS, "temperature": 0, "seed": 42}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}
    prompt_tokens = data.get("prompt_eval_count", 0)
    prompt_eval_ns = data.get("prompt_eval_duration", 0) or 1
    gen_tokens = data.get("eval_count", 0)
    eval_ns = data.get("eval_duration", 0) or 1
    return {
        "ttft_s": round(prompt_eval_ns / 1e9, 3),
        "prompt_toks": prompt_tokens,
        "prompt_toks_per_s": round(prompt_tokens / (prompt_eval_ns / 1e9), 2) if prompt_tokens else 0.0,
        "decode_toks": gen_tokens,
        "decode_toks_per_s": round(gen_tokens / (eval_ns / 1e9), 2) if gen_tokens else 0.0,
        "load_s": round((data.get("load_duration", 0) or 0) / 1e9, 3),
    }

def _ollama_get(port: int, path: str, body: dict | None = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def _ollama_detected_library(log_text: str) -> str | None:
    """Ollama logs the compute library it picked, e.g. 'inference compute ... library=ROCm'."""
    libs = re.findall(r"inference compute.*?library=\"?(\w+)", log_text) or re.findall(r"library=\"?(\w+)", log_text)
    return libs[-1].lower() if libs else None

def _parse_size_gb(s: str) -> float:
    m = re.match(r"([\d.]+)\s*(GB|MB|KB)", s or "", re.IGNORECASE)
    if not m: return 9e9
    return float(m.group(1)) * {"gb": 1, "mb": 1e-3, "kb": 1e-6}[m.group(2).lower()]

def bench_via_ollama(gpu_override: str | None) -> dict:
    """Runs a separate Ollama server per backend and checks Ollama's own log for which one it used."""
    if not _ollama_installed():
        return {"error": "ollama not installed"}

    models_out, _, _ = safe_run(["ollama", "list"])
    rows = []
    for line in models_out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and "embed" not in parts[0].lower():
            rows.append((parts[0], _parse_size_gb(f"{parts[2]} {parts[3]}")))
    if not rows:
        return {"error": "no ollama models installed (run e.g. `ollama pull llama3.2`)"}
    model = min(rows, key=lambda r: r[1])[0]  # smallest model: fits in VRAM, finishes fast

    try:
        if _ollama_get(11434, "/api/ps", timeout=2).get("models"):
            print(f"  {C.YELLOW}⚠ Your main Ollama server has models loaded. They share VRAM with the benchmark;"
                  f" run `ollama stop <model>` for cleaner numbers.{C.RESET}")
    except Exception:
        pass

    port = 11435
    print(f"  Model: {model}\n")
    results = _empty_results("ollama", model, model_class(model))

    for backend in ["vulkan", "rocm"]:
        backend_key = "vulkan" if backend == "vulkan" else "hip"
        print(f"  Testing {C.BOLD}{backend.upper()}{C.RESET}...")

        env = os.environ.copy()
        env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
        if backend == "vulkan":
            env["OLLAMA_VULKAN"] = "1"
            env["ROCR_VISIBLE_DEVICES"] = "-1"
            env["HIP_VISIBLE_DEVICES"] = "-1"
        else:
            env["OLLAMA_VULKAN"] = "0"
            env["GGML_VK_VISIBLE_DEVICES"] = "-1"
            if gpu_override:
                env["HSA_OVERRIDE_GFX_VERSION"] = gpu_override

        _ensure_config_dirs()
        log_path = CONFIG_DIR / f"bench_ollama_{backend}.log"
        try:
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_f:
                proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=log_f, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, **_popen_kwargs())
        except Exception as e:
            print(f"    {C.RED}→ Failed to launch Ollama: {e}{C.RESET}")
            continue

        runs, vram_frac, err = [], None, None
        try:
            if not wait_for_port("127.0.0.1", port, timeout=20):
                err = "Ollama did not start within 20 seconds."
            else:
                if not results["model_class"]:
                    try:
                        results["model_class"] = model_class(model, _ollama_get(port, "/api/show", {"model": model}).get("details"))
                    except Exception:
                        pass
                warm = _ollama_generate(port, model, timeout=600)
                if "error" in warm:
                    err = warm["error"]
                else:
                    try:
                        loaded = (_ollama_get(port, "/api/ps").get("models") or [{}])[0]
                        if loaded.get("size"):
                            vram_frac = round(loaded.get("size_vram", 0) / loaded["size"], 2)
                    except Exception:
                        pass
                    for i in range(BENCH_RUNS):
                        r = _ollama_generate(port, model)
                        if "error" in r:
                            print(f"    {C.RED}→ run {i+1}: {r['error']}{C.RESET}")
                            continue
                        runs.append(r)
        finally:
            _kill_tree(proc)

        if err or not runs:
            print(f"    {C.RED}→ {err or 'All runs failed.'}{C.RESET}")
            continue

        try: lib = _ollama_detected_library(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError: lib = None
        summary = _summarize_runs(runs)
        summary["library"] = lib
        summary["vram_fraction"] = vram_frac
        expected = "vulkan" if backend == "vulkan" else "rocm"
        on_gpu = vram_frac is None or vram_frac >= 0.5
        summary["backend_verified"] = bool(lib == expected and on_gpu)
        results[backend_key] = summary

        line = (f"prefill {summary.get('prompt_toks_per_s')} tok/s | decode {summary.get('decode_toks_per_s')} tok/s"
                f" | TTFT {(summary.get('ttft_s') or 0)*1000:.0f}ms | median of {len(runs)}")
        if summary["backend_verified"]:
            results[f"{backend_key}_toks"] = summary.get("decode_toks_per_s") or 0.0
            print(f"    {C.GREEN}→ {line}{C.RESET}")
        else:
            got = lib or "unknown"
            if not on_gpu: got += f", only {int((vram_frac or 0)*100)}% in VRAM"
            print(f"    {C.YELLOW}→ {line}{C.RESET}")
            print(f"    {C.YELLOW}⚠ Asked for {expected.upper()} but Ollama used {got}. Not counted.{C.RESET}")
            if backend == "vulkan":
                print(f"    {C.GRAY}  Vulkan needs Ollama 0.12.11 or newer.{C.RESET}")
            print(f"    {C.GRAY}  Server log: {log_path}{C.RESET}")

    _pick_winner(results)
    return results

def cmd_bench(args):
    print_header()
    sync_gpu_database()
    print(f"{C.BOLD}{C.CYAN}🏎️  Backend Benchmark (Vulkan vs HIP){C.RESET}\n")

    has_ollama = _ollama_installed()
    lms_models = find_lm_studio_models()
    has_lms = bool(shutil.which("lms")) or bool(lms_models)

    has_llamacpp = False
    llama_url = os.environ.get("ROCMFIX_LLAMA_CPP_URL") or "http://127.0.0.1:8080"
    try:
        req = urllib.request.Request(f"{llama_url}/v1/models")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if 200 <= resp.status < 300:
                has_llamacpp = True
    except Exception:
        pass

    print(f"{C.BOLD}Detected AI runtimes:{C.RESET}")
    print(f"  {'✓ Ollama' if has_ollama else '✗ Ollama'}")
    print(f"  {'✓ LM Studio' if has_lms else '✗ LM Studio'}")
    print(f"  {'✓ llama.cpp server (' + llama_url + ')' if has_llamacpp else '✗ llama.cpp server'}")
    print()

    gpus = detect_gpus()
    gpu_override = None
    target_gpu = gpus[0] if gpus else None
    if target_gpu:
        rec = recommend(target_gpu, GPU_DATABASE.get(target_gpu["pci_id"]), detect_rocm_version())
        if rec["status"] == "override":
            gpu_override = rec["override"]

    result = None
    forced = getattr(args, "runtime", None)

    if forced == "ollama" or (not forced and has_ollama):
        result = bench_via_ollama(gpu_override)
    elif forced == "lmstudio" or (not forced and has_lms):
        result = bench_via_lmstudio(gpu_override)
    elif forced == "llamacpp" or (not forced and has_llamacpp):
        result = bench_via_llamacpp(gpu_override)

    if not result or "error" in result:
        err_msg = result.get("error") if result else "No AI runtimes or models found."
        print(f"  {C.RED}✗ {err_msg}{C.RESET}\n")
        return

    print(f"\n{C.BOLD}🏆 Results{C.RESET} ({result.get('model_class') or result.get('model')}, "
          f"{BENCH_MAX_TOKENS} tokens, median of {BENCH_RUNS} runs):\n")
    if result.get("single_backend"):
        m = result.get("measurement") or {}
        label = {"llama_cpp": "llama.cpp server", "lm_studio": "LM Studio"}.get(result["runtime"], result["runtime"])
        print(f"  Runtime: {label} (backend: {result.get('backend')})")
        print(f"  TTFT:    {(m.get('ttft_s') or 0)*1000:.0f} ms")
        print(f"  Decode:  {m.get('decode_toks_per_s', 0)} tok/s")
        if m.get("prompt_toks_per_s"): print(f"  Prefill: {m['prompt_toks_per_s']} tok/s")
        best_decode = m.get("decode_toks_per_s") or 0.0
    else:
        vk = result.get("vulkan") or {}
        hp = result.get("hip") or {}

        print(f"  {'Backend':<10} {'TTFT (ms)':>10} {'Prefill t/s':>12} {'Decode t/s':>11}  Note")
        print(f"  {'-'*10} {'-'*10} {'-'*12} {'-'*11}  {'-'*20}")

        def _print_metrics(label, d):
            if not d:
                print(f"  {label:<10} {'-':>10} {'-':>12} {'-':>11}  not measured"); return
            ttft = f"{d['ttft_s']*1000:.0f}" if d.get("ttft_s") is not None else "-"
            pre = f"{d['prompt_toks_per_s']}" if d.get("prompt_toks_per_s") else "-"
            dec = f"{d.get('decode_toks_per_s', 0)}"
            note = "not verified" if d.get("backend_verified") is False else ""
            if d.get("from_previous_run"): note = f"from run on {d['from_previous_run'][:10]}"
            print(f"  {label:<10} {ttft:>10} {pre:>12} {dec:>11}  {note}")

        _print_metrics("Vulkan", vk)
        _print_metrics("HIP/ROCm", hp)

        vk_dec = result.get("vulkan_toks", 0.0)
        hp_dec = result.get("hip_toks", 0.0)
        best_decode = max(vk_dec, hp_dec, vk.get("decode_toks_per_s") or 0, hp.get("decode_toks_per_s") or 0)

        if result["winner"] == "vulkan":
            print(f"\n  {C.GREEN}Winner: VULKAN (+{int((vk_dec/hp_dec-1)*100)}% decode){C.RESET}")
        elif result["winner"] == "hip":
            print(f"\n  {C.GREEN}Winner: HIP/ROCm (+{int((hp_dec/vk_dec-1)*100)}% decode){C.RESET}")
        elif result["winner"] == "tie":
            print(f"\n  {C.YELLOW}Tie.{C.RESET}")
        else:
            print(f"\n  {C.YELLOW}No verified comparison yet: only one backend was measured.{C.RESET}")
        if result.get("compare_hint"):
            print(f"  {C.CYAN}{result['compare_hint']}{C.RESET}")

    if target_gpu:
        fallback_alert = check_cpu_fallback(target_gpu.get("name", ""), best_decode, result.get("model_class"))
        if fallback_alert:
            alert_color = C.RED if fallback_alert["level"] == "critical" else C.YELLOW
            alert_icon = "✗" if fallback_alert["level"] == "critical" else "⚠"
            print(f"\n  {alert_color}{alert_icon} PERFORMANCE ALERT: {fallback_alert['message']}{C.RESET}")

    cfg = load_config()
    if cfg.get("telemetry_enabled") and target_gpu:
        info = GPU_DATABASE.get(target_gpu["pci_id"])
        payload = build_telemetry_payload(target_gpu, info, detect_rocm_version(), benchmark=result)
        send_telemetry(payload)
        print(f"\n  {C.GREEN}✓ Benchmark numbers shared (no model name).{C.RESET}\n")
    else:
        print()

# ──────────────────────────────────────────────────────────────────────
# 13. COMMAND DISPATCH
# ──────────────────────────────────────────────────────────────────────

GFX_SOURCE_LABELS = {"kfd": "read from amdgpu driver", "hipinfo": "read from hipInfo", "database": "from database"}

def print_recommendation(rec: dict, os_name: str, indent: str = "      "):
    gfx = rec.get("gfx") or "unknown"
    st = rec["status"]
    if st == "native":
        print(f"{indent}Status:  {C.GREEN}Natively supported ({gfx}){C.RESET}: no override needed")
    elif st == "override":
        print(f"{indent}Status:  {C.YELLOW}Needs override{C.RESET}")
        print(f"{indent}gfx:     {gfx} → HSA_OVERRIDE_GFX_VERSION={rec['override']}")
        if os_name == "windows":
            print(f"{indent}{C.GRAY}On Windows, Ollama's bundled ROCm reads this variable. The HIP SDK and other apps may ignore it;{C.RESET}")
            print(f"{indent}{C.GRAY}if one does, use its Vulkan backend instead.{C.RESET}")
    elif st == "legacy":
        print(f"{indent}Status:  {C.YELLOW}Legacy ({gfx}): dropped from current ROCm{C.RESET}")
        print(f"{indent}Use Vulkan, or pin ROCm 6.x or older, which still ships {gfx} kernels.")
    elif st == "vulkan":
        print(f"{indent}Status:  {C.YELLOW}No ROCm support for {gfx}, and no reliable override{C.RESET}")
        print(f"{indent}Use Vulkan (LM Studio, Ollama 0.12.11+, llama.cpp all support it).")
    elif st == "igpu":
        print(f"{indent}Status:  {C.GRAY}Not useful for AI (small iGPU){C.RESET}")
    else:
        print(f"{indent}Status:  {C.YELLOW}Unknown{C.RESET}: couldn't read the gfx target.")
        hint = "install ROCm" if os_name == "linux" else "install the HIP SDK (rocmfix install-hip)"
        print(f"{indent}To fix: {hint} so ROCmFix can read it from the driver, or report your GPU.")
    for n in rec.get("notes") or []:
        print(f"{indent}Notes:   {n}")
    print(f"{indent}Backend: {C.BOLD}{rec['rec_backend'].upper()}{C.RESET} recommended")

def _offer_apply(rec: dict, shell: str):
    override = rec["override"]
    print(f"\n      {C.BOLD}Apply this override?{C.RESET}  [A]uto  [S]how  [N]o")
    try: ch = input("      > ").strip().lower()
    except Exception: ch = "n"
    if ch == "a":
        systemd = False
        if ollama_systemd_unit():
            print(f"\n      Ollama runs as a systemd service, which doesn't read your shell config.")
            print(f"      Add the override to the service too? Writes {SYSTEMD_DROPIN} with sudo. [Y/n]")
            try: systemd = input("      > ").strip().lower() != "n"
            except Exception: systemd = False
        res = apply_override(override, systemd=systemd)
        if res["success"]:
            print(f"\n      {C.GREEN}✓ Added to {res.get('file')}.{C.RESET}")
            if res.get("systemd_dropin"):
                print(f"      {C.GREEN}✓ Ollama service updated and restarted.{C.RESET}")
            elif systemd:
                print(f"      {C.RED}✗ Ollama service not updated: {res.get('systemd_error')}{C.RESET}")
            if detect_os() == "windows":
                print(f"      {C.YELLOW}⚠ Quit Ollama from the system tray and start it again (and open a NEW terminal).{C.RESET}")
            else:
                print(f"      {C.YELLOW}⚠ Open a NEW terminal for it to take effect.{C.RESET}")
            print(f"      Undo with: rocmfix undo")
        else:
            print(f"\n      {C.RED}✗ Failed: {res.get('error')}{C.RESET}\n{format_env_command(override)}")
    elif ch == "s":
        print(f"\n      {C.BOLD}Run in {shell}:{C.RESET}\n{format_env_command(override)}")
        if ollama_systemd_unit():
            print(f"\n      {C.BOLD}For the Ollama service:{C.RESET}")
            print(f"        sudo systemctl edit ollama")
            print(f'        # add:  [Service]  Environment="HSA_OVERRIDE_GFX_VERSION={override}"')
            print(f"        sudo systemctl restart ollama")
    else:
        print(f"      {C.GRAY}Skipped.{C.RESET}")

def cmd_detect(args):
    print_header()
    sync_gpu_database()
    cfg = load_config()

    if cfg.get("first_run") and not is_installed_globally():
        if install_globally(): cfg["installed_globally"] = True
    if cfg.get("telemetry_enabled") is None:
        cfg["telemetry_enabled"] = prompt_telemetry_optin()
    cfg["first_run"] = False
    save_config(cfg)

    if cfg["telemetry_enabled"]:
        try:
            sent, _ = flush_outbox()
            if sent > 0: print(f"  {C.GRAY}[Telemetry: sent {sent} queued reports]{C.RESET}\n")
        except Exception: pass

    maybe_print_update_banner()

    os_name, shell, rocm_ver = detect_os(), detect_shell(), detect_rocm_version()
    print(f"{C.BOLD}System Info:{C.RESET}")
    print(f"  OS:       {os_name}\n  Shell:    {shell}\n  ROCm/HIP: {rocm_ver or f'{C.YELLOW}Not detected{C.RESET}'}\n")

    gpus = detect_gpus()
    if not gpus:
        print(f"{C.RED}No AMD GPUs detected.{C.RESET}"); sys.exit(1)

    print(f"{C.BOLD}Detected {len(gpus)} AMD GPU(s):{C.RESET}")
    for i, gpu in enumerate(gpus, 1):
        print(f"\n  [{i}] {C.BOLD}{gpu['name']}{C.RESET}")
        print(f"      PCI ID:  {gpu['pci_id']}\n      Driver:  {gpu['driver_version']}")
        if gpu.get("gfx_target"):
            print(f"      gfx:     {gpu['gfx_target']} ({GFX_SOURCE_LABELS.get(gpu.get('gfx_source'), '?')})")
        info = GPU_DATABASE.get(gpu["pci_id"])
        rec = recommend(gpu, info, rocm_ver, os_name)
        if cfg["telemetry_enabled"]:
            send_telemetry(build_telemetry_payload(gpu, info, rocm_ver))

        print_recommendation(rec, os_name)
        for issue in (info or {}).get("known_issues", []):
            print(f"        - {issue}")
        if not info:
            print(f"\n      Not in the database yet. Help us add it:\n      {C.BLUE}{get_issue_url(gpu, rocm_ver)}{C.RESET}")

        if rec["status"] == "override":
            _offer_apply(rec, shell)

    warn_unneeded_override(gpus, rocm_ver, os_name, indent="  ")

def warn_unneeded_override(gpus: list[dict], rocm_ver: str | None, os_name: str, indent: str):
    """An override on a natively supported card forces it onto a sibling's kernels for no gain."""
    ov = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    if not ov: return
    recs = [recommend(g, GPU_DATABASE.get(g["pci_id"]), rocm_ver, os_name) for g in gpus]
    useful = [r for r in recs if r["status"] not in ("igpu",)]
    if useful and all(r["status"] == "native" for r in useful):
        print(f"\n{indent}{C.YELLOW}⚠ HSA_OVERRIDE_GFX_VERSION={ov} is set, but your GPU doesn't need it.")
        parts = ov.split(".")
        as_gfx = f"gfx{parts[0]}{int(parts[1]):x}{int(parts[2]):x}" if VERSION_RE.fullmatch(ov) else ov
        print(f"{indent}  It makes ROCm run the card as {as_gfx} instead of its real target.")
        print(f"{indent}  Remove it with `rocmfix undo` (if ROCmFix set it) or from your environment variables.{C.RESET}")
    elif len(useful) > 1:
        print(f"\n{indent}{C.GRAY}Several AMD GPUs: the override applies to all of them. Per-GPU variants exist")
        print(f"{indent}(HSA_OVERRIDE_GFX_VERSION_0, _1, ...) if they need different values.{C.RESET}")

def cmd_doctor(args):
    print_header(); sync_gpu_database()
    print(f"{C.BOLD}{C.CYAN}🩺 System Doctor{C.RESET}\n")
    os_name, rocm_ver = detect_os(), detect_rocm_version()
    gpus = detect_gpus()
    print(f"{C.BOLD}1. Hardware & Drivers{C.RESET}")
    if not gpus: print(f"  {C.RED}✗ No AMD GPUs detected{C.RESET}")
    for g in gpus:
        print(f"  {C.GREEN}✓ GPU:{C.RESET} {g['name']} (PCI: {g['pci_id']})")
        if g.get("gfx_target"):
            print(f"  {C.GREEN}✓ gfx:{C.RESET} {g['gfx_target']} ({GFX_SOURCE_LABELS.get(g.get('gfx_source'), '?')})")
        d = analyze_driver(g['driver_version'])
        if d:
            color = C.GREEN if d["status"]=="ok" else C.YELLOW if d["status"]=="warn" else C.RED
            icon = "✓" if d["status"]=="ok" else "⚠" if d["status"]=="warn" else "✗"
            print(f"  {color}{icon} Driver:{C.RESET} {g['driver_version']} — {d['msg']}")
        else:
            print(f"  {C.GRAY}• Driver:{C.RESET} {g['driver_version']}")

    print(f"\n{C.BOLD}2. ROCm / HIP{C.RESET}")
    if os_name == "windows":
        hp = os.environ.get("HIP_PATH")
        if hp and Path(hp).exists(): print(f"  {C.GREEN}✓ HIP SDK:{C.RESET} {hp}")
        else:
            print(f"  {C.YELLOW}• HIP SDK not installed{C.RESET} (only needed for HIP; Vulkan works without it)")
            print(f"    {C.CYAN}Run: rocmfix install-hip{C.RESET}")
    else:
        if rocm_ver:
            label = rocm_ver if rocm_ver.startswith("installed") else f"v{rocm_ver}"
            print(f"  {C.GREEN}✓ ROCm:{C.RESET} {label}")
        else:
            print(f"  {C.RED}✗ ROCm not found{C.RESET}")
            print(f"    {C.CYAN}Run: rocmfix install-rocm{C.RESET}")

    print(f"\n{C.BOLD}3. Vulkan{C.RESET}")
    vk_ct = vulkan_smoke_test_ctypes()
    if vk_ct["success"]:
        print(f"  {C.GREEN}✓ Vulkan interface:{C.RESET} {vk_ct['message']}")
    else:
        vk, _, _ = safe_run(["vulkaninfo", "--summary"])
        if "Vulkan Instance Version" in vk or "device" in vk.lower():
            print(f"  {C.GREEN}✓ Vulkan API interface active{C.RESET}")
        else:
            print(f"  {C.YELLOW}⚠ Vulkan pipeline diagnostic failed:{C.RESET} {vk_ct['message']}")

    print(f"\n{C.BOLD}4. Env override{C.RESET}")
    ov = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    if os_name == "windows":
        print(f"  {'• set to ' + ov + ' (read by Ollama; restart Ollama after changing it)' if ov else '• not set'}")
    else:
        print(f"  {'✓ shell: ' + ov if ov else '• shell: not set'}")
        if ollama_systemd_unit():
            env_out, _, _ = safe_run(["systemctl", "show", "ollama", "-p", "Environment"], timeout=10)
            m = re.search(r"HSA_OVERRIDE_GFX_VERSION=(\S+)", env_out)
            print(f"  {'✓ ollama service: ' + m.group(1) if m else '• ollama service: not set (services ignore ~/.bashrc)'}")

    print(f"\n{C.BOLD}Recommendation:{C.RESET}")
    for g in gpus:
        print(f"  {C.BOLD}{g['name']}{C.RESET}")
        print_recommendation(recommend(g, GPU_DATABASE.get(g["pci_id"]), rocm_ver, os_name), os_name, indent="    ")
    warn_unneeded_override(gpus, rocm_ver, os_name, indent="  ")
    print()

def cmd_install_hip(args):
    print_header()
    if detect_os() != "windows":
        print(f"  {C.RED}Windows only.{C.RESET}"); return
    if os.environ.get("HIP_PATH"):
        print(f"  {C.GREEN}✓ HIP already at {os.environ.get('HIP_PATH')}{C.RESET}"); return
    hub = "https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html"
    print(f"  Direct downloads sometimes 404 after AMD site changes.")
    print(f"  Opening: {hub}")
    try: webbrowser.open(hub)
    except Exception: pass

def cmd_install_rocm(args):
    print_header()
    if detect_os() != "linux":
        print(f"  {C.RED}Linux only.{C.RESET}"); return
    distro = "unknown"
    try:
        content = Path("/etc/os-release").read_text().lower()
        if "ubuntu" in content or "debian" in content: distro = "ubuntu"
        elif "fedora" in content: distro = "fedora"
        elif "arch" in content: distro = "arch"
    except Exception: pass
    print(f"  Distro: {distro}")
    if distro == "ubuntu":
        print(f"    {C.CYAN}sudo apt install -y rocm-hip-sdk rocminfo{C.RESET}")
    elif distro == "fedora":
        print(f"    {C.CYAN}sudo dnf install -y rocm-hip rocminfo{C.RESET}")
    elif distro == "arch":
        print(f"    {C.CYAN}sudo pacman -S --needed rocm-hip-sdk rocminfo{C.RESET}")
    print(f"    {C.CYAN}sudo usermod -aG render,video $USER{C.RESET}")

def cmd_optimize(args):
    print_header()
    if detect_os() != "windows":
        print(f"  Only Linux group hint available:")
        g = _run(["groups"])
        for m in ("render","video"):
            if m not in g:
                print(f"  {C.YELLOW}Missing group {m}. sudo usermod -aG {m} $USER{C.RESET}")
        return
    import winreg
    tdr_key = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
    values = {"TdrDelay": 60, "TdrDdiDelay": 60}
    cfg = load_config()

    def _read_current() -> dict:
        cur = {}
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key) as k:
            for name in values:
                try: cur[name] = winreg.QueryValueEx(k, name)[0]
                except FileNotFoundError: cur[name] = None
        return cur

    if getattr(args, "revert", False):
        backup = cfg.get("optimize_backup")
        if not backup:
            print(f"  {C.YELLOW}Nothing to revert.{C.RESET}"); return
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key, 0, winreg.KEY_ALL_ACCESS) as k:
                for name, old in backup.items():
                    if old is None:
                        try: winreg.DeleteValue(k, name)
                        except FileNotFoundError: pass
                    else:
                        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, old)
            cfg.pop("optimize_backup", None); save_config(cfg)
            print(f"  {C.GREEN}✓ Restored previous TDR settings. Reboot to apply.{C.RESET}")
        except PermissionError:
            print(f"  {C.RED}Run as Administrator.{C.RESET}")
        return

    print(f"  {C.BOLD}Windows GPU timeout (TDR) tweak{C.RESET}")
    print(f"  Long AI kernels can trip Windows' 2-second GPU watchdog and reset the driver.")
    print(f"  This raises the timeout. It changes system-wide settings in:")
    print(f"    HKLM\\{tdr_key}")
    current = _read_current()
    for name, new in values.items():
        print(f"    {name}: {current[name] if current[name] is not None else 'not set (default 2)'} → {new}")
    print(f"  Revert any time with: rocmfix optimize --revert")
    try:
        if input("\n  Apply? [y/N]: ").strip().lower() != "y":
            print(f"  {C.GRAY}Skipped.{C.RESET}"); return
    except Exception:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key, 0, winreg.KEY_ALL_ACCESS) as k:
            for name, new in values.items():
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, new)
        if "optimize_backup" not in cfg:
            cfg["optimize_backup"] = current
            save_config(cfg)
        print(f"  {C.GREEN}✓ TDR timeout raised. Reboot to apply.{C.RESET}")
    except PermissionError:
        print(f"  {C.RED}Run as Administrator.{C.RESET}")
    except Exception as e:
        print(f"  {C.RED}{e}{C.RESET}")

def cmd_update(args):
    print_header()
    print(f"  Checking...")
    try:
        req = urllib.request.Request(UPDATE_CHECK_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  {C.RED}Failed: {e}{C.RESET}"); return
    latest = str(data.get("tag_name","")).lstrip("v")
    if _version_tuple(latest) <= _version_tuple(__version__):
        print(f"  {C.GREEN}You are on latest ({__version__}){C.RESET}"); return

    tag_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/v{latest}/rocmfix.py"
    print(f"  Downloading v{latest} from tag...")
    try:
        req = urllib.request.Request(tag_url, headers={"User-Agent": f"ROCmFix/{__version__}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            new_content = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  {C.RED}Download failed: {e}{C.RESET}"); return
    try:
        compile(new_content, "rocmfix.py", "exec")
    except SyntaxError as e:
        print(f"  {C.RED}Downloaded file failed syntax check: {e}{C.RESET}"); return

    script_path = Path(__file__).resolve()
    _ensure_config_dirs()
    shutil.copy2(script_path, BACKUP_DIR / f"rocmfix.py.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
    if detect_os() == "windows":
        newp = script_path.with_suffix(".py.new")
        newp.write_text(new_content, encoding="utf-8")
        swap = script_path.parent / "_rocmfix_swap.bat"
        swap.write_text(f'@echo off\ntimeout /t 2 /nobreak >nul\nmove /y "{newp}" "{script_path}"\ndel "%~f0"\n')
        subprocess.Popen([str(swap)], shell=True, creationflags=0x08000000)
        print(f"  {C.GREEN}✓ Update queued. Restart terminal.{C.RESET}")
    else:
        script_path.write_text(new_content, encoding="utf-8")
        print(f"  {C.GREEN}✓ Updated.{C.RESET}")

_STATUS_TEXT = {"native": "Native", "override": "Override (Linux)", "legacy": "Legacy (old ROCm only)",
                "vulkan": "Vulkan only", "igpu": "Not for AI", "unknown": "Unknown"}

def cmd_export(args):
    print_header(); sync_gpu_database()
    os_name = detect_os()
    gpus = detect_gpus(); rocm_ver = detect_rocm_version()
    lines = [f"# ROCmFix report", f"- v{__version__}", f"- OS: {os_name}", f"- ROCm/HIP: {rocm_ver or 'not detected'}", "",
             "## GPUs", "| Name | PCI | gfx | Driver | Status | Backend |", "|---|---|---|---|---|---|"]
    for g in gpus:
        rec = recommend(g, GPU_DATABASE.get(g["pci_id"]), rocm_ver, os_name)
        st = _STATUS_TEXT[rec["status"]]
        if rec["override"]: st += f" `HSA_OVERRIDE_GFX_VERSION={rec['override']}`"
        lines.append(f"| {g['name']} | `{g['pci_id']}` | {rec['gfx'] or '?'} | {g['driver_version']} | {st} | {rec['rec_backend']} |")
    out = Path.home() / f"rocmfix-report-{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ✓ {out}")

def cmd_test(args):
    print_header(); sync_gpu_database()
    current = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    print(f"  HSA_OVERRIDE_GFX_VERSION = {C.BOLD}{current or 'NOT SET'}{C.RESET}")
    result = run_smoke_test(current)
    if result["success"]: print(f"  {C.GREEN}PASS: {result['message']}{C.RESET}")
    else:
        print(f"  {C.RED}FAIL: {result['message']}{C.RESET}")
        if result["details"]: print(f"  {C.GRAY}{result['details']}{C.RESET}")
    cfg = load_config()
    if cfg.get("telemetry_enabled"):
        for gpu in detect_gpus():
            send_telemetry(build_telemetry_payload(gpu, GPU_DATABASE.get(gpu["pci_id"]), detect_rocm_version(), result))

def cmd_list(args):
    print_header(); sync_gpu_database()
    print(f"  {C.BOLD}Known GPUs ({len(GPU_DATABASE)}):{C.RESET}  (status for current ROCm on Linux)\n")
    print(f"  {'PCI':<6} {'Name':<46} {'gfx':<8} {'Override':<10} Status")
    print(f"  {'-'*6} {'-'*46} {'-'*8} {'-'*10} {'-'*16}")
    colors = {"native": C.GREEN, "override": C.YELLOW, "legacy": C.YELLOW, "vulkan": C.YELLOW}
    for pid, info in GPU_DATABASE.items():
        rec = recommend({"pci_id": pid}, info, None, "linux")
        ov = rec["override"] or "-"
        st = f"{colors.get(rec['status'], C.GRAY)}{_STATUS_TEXT[rec['status']]}{C.RESET}"
        print(f"  {pid:<6} {info['name'][:46]:<46} {(rec['gfx'] or '?'):<8} {ov:<10} {st}")
    print(f"\n  Not listed? ROCmFix reads the real gfx target from your driver, so `rocmfix` still works.")

def cmd_gpus(args):
    print_header()
    if getattr(args, "online", False):
        print(f"{C.BOLD}Checking online database version...{C.RESET}\n")
        meta = fetch_db_metadata_online()
        if meta["status"] == "ok":
            print(f"  Server URL: {DATABASE_ENDPOINT}")
            print(f"  Version:    {C.BOLD}{meta['version']}{C.RESET}")
            print(f"  GPUs:       {meta['gpu_count']}")
            print(f"  Age:        {meta['age']}\n")
            cfg = load_config()
            last_sync = cfg.get("last_db_sync")
            if last_sync:
                try:
                    sync_time = datetime.fromisoformat(last_sync)
                    diff = datetime.now(timezone.utc) - sync_time
                    hours = int(diff.total_seconds() // 3600)
                    print(f"  Local cache synced: {hours} hour(s) ago")
                except Exception:
                    pass
        else:
            print(f"  {C.RED}✗ Cloud database unreachable: {meta.get('error')}{C.RESET}\n")
    cmd_list(args)

def cmd_undo(args):
    print_header()
    r = undo_last_override()
    if r["success"]:
        print(f"  {C.GREEN}✓ Reverted.{C.RESET}")
        for n in r.get("notes") or []: print(f"  {C.GREEN}✓ {n}{C.RESET}")
        if r.get("restored_from"): print(f"  {C.GRAY}Backup of the original file: {r['restored_from']}{C.RESET}")
    else:
        print(f"  {C.RED}✗ {r['error']}{C.RESET}")

def cmd_telemetry(args):
    print_header()
    cfg = load_config()
    print(f"  Status: {'ENABLED' if cfg.get('telemetry_enabled') else 'DISABLED'}")
    print(f"  Queued: {len(list(OUTBOX_DIR.glob('*.json'))) if OUTBOX_DIR.exists() else 0}")
    print(f"  Endpoint: {TELEMETRY_ENDPOINT}\n")
    print("  [E]nable [D]isable [F]lush [S]ample [Q]uit")
    try: c = input("  > ").strip().lower()
    except Exception: return
    if c == "e": cfg["telemetry_enabled"] = True; save_config(cfg); print("enabled")
    elif c == "d": cfg["telemetry_enabled"] = False; save_config(cfg); print("disabled")
    elif c == "f":
        s, f = flush_outbox()
        print(f"  Sent {s} of {s+f}")
    elif c == "s":
        gpus = detect_gpus()
        if gpus: show_telemetry_sample(gpus[0], GPU_DATABASE.get(gpus[0]["pci_id"]), detect_rocm_version())

def cmd_verify(args):
    print_header(); sync_gpu_database()
    pci = input("  PCI ID: ").strip().lower()
    ov = input("  Override: ").strip()
    info = GPU_DATABASE.get(pci)
    if info: print(f"  In DB: {info['name']}")
    r = run_smoke_test(ov or None)
    print(("PASS: " if r["success"] else "FAIL: ") + r["message"])

def cmd_contribute(args):
    print_header()
    pci = input("  PCI: ").strip().lower()
    name = input("  Name: ").strip()
    arch = input("  Arch: ").strip()
    gfx = input("  gfx target: ").strip()
    ov = input("  Override (blank=none): ").strip()
    notes = input("  Notes: ").strip()
    override_json = json.dumps(ov) if ov else "null"
    print(f'\n    "{pci}": {{')
    print(f'        "name": {json.dumps(name)}, "arch": {json.dumps(arch)},')
    print(f'        "gfx_target": {json.dumps(gfx)},')
    print(f'        "override": {override_json},')
    print(f'        "supported": {"false" if ov else "true"},')
    print(f'        "known_issues": [], "notes": {json.dumps(notes)}, "rec_backend": "vulkan"')
    print(f'    }},')

def cmd_sync(args):
    print_header()
    r = sync_gpu_database(force=True)
    print(f"  sync: {r} ({len(GPU_DATABASE)} entries)")

def main():
    enable_ansi()
    p = argparse.ArgumentParser(prog="rocmfix", description="AMD GPU override helper")
    p.add_argument("--version", action="version", version=f"ROCmFix {__version__}")
    p.add_argument("command", nargs="?", default="detect",
                   choices=["detect","test","list","contribute","install","verify",
                            "undo","telemetry","doctor","install-hip","install-rocm",
                            "bench","update","export","sync","optimize","gpus"])
    p.add_argument("--online", action="store_true", help="Fetch online database age (for 'gpus' command)")
    p.add_argument("--runtime", choices=["ollama", "lmstudio", "llamacpp"], help="Select benchmark model target")
    p.add_argument("--revert", action="store_true", help="Undo 'optimize' changes")
    args = p.parse_args()
    
    disp = {
        "detect": cmd_detect, "test": cmd_test, "list": cmd_list, "verify": cmd_verify,
        "contribute": cmd_contribute, "install": lambda a: install_globally(),
        "undo": cmd_undo, "telemetry": cmd_telemetry, "doctor": cmd_doctor,
        "install-hip": cmd_install_hip, "install-rocm": cmd_install_rocm,
        "bench": cmd_bench, "update": cmd_update, "export": cmd_export,
        "sync": cmd_sync, "optimize": cmd_optimize, "gpus": cmd_gpus,
    }
    
    try:
        disp[args.command](args)
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Cancelled by user.{C.RESET}")
        sys.exit(130)

if __name__ == "__main__":
    main()
