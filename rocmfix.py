#!/usr/bin/env python3
"""
ROCmFix v0.1.6 — Cross-platform AMD GPU override detector for ROCm/HIP.
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
import webbrowser
import glob
from pathlib import Path
from datetime import datetime, timezone, timedelta

__version__ = "0.1.6"
GITHUB_REPO = "xanpavle/rocmfix"

TELEMETRY_ENDPOINT = "https://rocmfix-data.onrender.com/submit"
DATABASE_ENDPOINT = "https://rocmfix-data.onrender.com/gpus.json"
UPDATE_CHECK_ENDPOINT = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DOWNLOAD_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/rocmfix.py"
TELEMETRY_TIMEOUT = 15

CONFIG_DIR = Path.home() / ".rocmfix"
CONFIG_FILE = CONFIG_DIR / "config.json"
OUTBOX_DIR = CONFIG_DIR / "outbox"
BACKUP_DIR = CONFIG_DIR / "backups"
DB_CACHE_FILE = CONFIG_DIR / "db_cache.json"
DB_CACHE_TTL_HOURS = 24
UPDATE_CHECK_TTL_HOURS = 168

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# ──────────────────────────────────────────────────────────────────────
# 1. FALLBACK DATABASE
# ──────────────────────────────────────────────────────────────────────

FALLBACK_DATABASE = {
    "7550": {"name": "RX 9070 XT / 9070 / 9070 GRE (Navi 48)", "arch": "RDNA4", "gfx_target": "gfx1201", "override": None, "supported": True, "rec_backend": "vulkan", "known_issues": [], "notes": "Natively supported in ROCm 6.4+."},
    "7551": {"name": "Radeon AI PRO R9700 (Navi 48 Pro)", "arch": "RDNA4", "gfx_target": "gfx1201", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Enterprise variant of Navi 48."},
    "7590": {"name": "RX 9060 XT / 9060 / 9050 (Navi 44)", "arch": "RDNA4", "gfx_target": "gfx1200", "override": "12.0.1", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1201."},
    "7448": {"name": "Radeon PRO W7900 (Navi 31)", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Workstation Navi 31."},
    "744c": {"name": "RX 7900 XTX / XT / GRE / M (Navi 31)", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "747e": {"name": "RX 7700 XT / RX 7800 XT (Navi 32)", "arch": "RDNA3", "gfx_target": "gfx1101", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100."},
    "7470": {"name": "Radeon PRO W7700 (Navi 32)", "arch": "RDNA3", "gfx_target": "gfx1100", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Workstation Navi 32."},
    "7480": {"name": "RX 7600 (Navi 33)", "arch": "RDNA3", "gfx_target": "gfx1102", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100."},
    "7483": {"name": "RX 7600M / 7600M XT (Navi 33)", "arch": "RDNA3", "gfx_target": "gfx1102", "override": "11.0.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1100."},
    "73af": {"name": "RX 6900 XT (Navi 21)", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73bf": {"name": "RX 6800 / 6800 XT / 6900 XT (Navi 21)", "arch": "RDNA2", "gfx_target": "gfx1030", "override": None, "supported": True, "rec_backend": "hip", "known_issues": [], "notes": "Natively supported."},
    "73df": {"name": "RX 6700 XT (Navi 22)", "arch": "RDNA2", "gfx_target": "gfx1031", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "73ef": {"name": "RX 6700S (Navi 23)", "arch": "RDNA2", "gfx_target": "gfx1032", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "73ff": {"name": "RX 6600 XT / 6600 (Navi 23)", "arch": "RDNA2", "gfx_target": "gfx1032", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "743f": {"name": "RX 6500 XT (Navi 24)", "arch": "RDNA2", "gfx_target": "gfx1034", "override": "10.3.0", "supported": False, "rec_backend": "vulkan", "known_issues": [], "notes": "Override to gfx1030."},
    "1114": {"name": "Radeon 840M / 860M Graphics (Krackan)", "arch": "RDNA3.5", "gfx_target": "gfx1150", "override": None, "supported": True, "rec_backend": "vulkan", "known_issues": [], "notes": "Natively supported in modern ROCm."},
    "164e": {"name": "Radeon Graphics (Ryzen 7000 iGPU)", "arch": "RDNA2", "gfx_target": "gfx1036", "override": None, "supported": False, "rec_backend": "cpu", "known_issues": [], "notes": "iGPU — ignore for AI."},
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
            import ctypes
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

    try:
        req = urllib.request.Request(DATABASE_ENDPOINT, headers={"User-Agent": f"ROCmFix/{__version__}"})
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
        pass

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

def _detect_linux() -> list[dict]:
    gpus = []
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
    if detect_os() == "windows": return _detect_windows_registry()
    if detect_os() == "linux": return _detect_linux()
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
        if not hip_path:
            return {"success": False, "message": "HIP_PATH not set.", "details": "Run 'rocmfix install-hip'"}
        hipinfo = os.path.join(hip_path, "bin", "hipInfo.exe")
        if not os.path.exists(hipinfo):
            return {"success": False, "message": "hipInfo.exe not found.", "details": f"Looked in: {hip_path}\\bin\\"}
        try:
            res = subprocess.run([hipinfo], capture_output=True, text=True, env=env,
                                 timeout=15, encoding="utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}
        if res.returncode != 0:
            return {"success": False, "message": "hipInfo returned error.", "details": (res.stderr or res.stdout)[:300]}
        if "device" not in res.stdout.lower():
            return {"success": False, "message": "No devices found.", "details": res.stdout[:300]}
        return {"success": True, "message": "HIP SDK detected your GPU.", "details": f"override={override or 'not set'}"}

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

_ENV_LINE_RE = re.compile(r'^\s*(?:export\s+|set\s+-gx\s+|set\s+-xg\s+)?HSA_OVERRIDE_GFX_VERSION\b')

def apply_override_windows(override: str) -> dict:
    if not VERSION_RE.fullmatch(override):
        return {"success": False, "error": "Invalid override value"}
    try:
        import winreg, ctypes
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        try: old_value, _ = winreg.QueryValueEx(key, "HSA_OVERRIDE_GFX_VERSION")
        except FileNotFoundError: old_value = None
        winreg.SetValueEx(key, "HSA_OVERRIDE_GFX_VERSION", 0, winreg.REG_SZ, override)
        winreg.CloseKey(key)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long()))
        return {"success": True, "old_value": old_value, "method": "windows_registry"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def apply_override_unix(override: str, shell: str) -> dict:
    if not VERSION_RE.fullmatch(override):
        return {"success": False, "error": "Invalid override value"}
    home = Path.home()
    if shell == "fish":
        cfg = home / ".config" / "fish" / "config.fish"
        line = f"set -gx HSA_OVERRIDE_GFX_VERSION {override}\n"
    elif shell == "zsh":
        cfg = home / ".zshrc"
        line = f"export HSA_OVERRIDE_GFX_VERSION={override}\n"
    else:
        cfg = home / ".bashrc"
        line = f"export HSA_OVERRIDE_GFX_VERSION={override}\n"
    marker = "# Added by ROCmFix"
    try:
        existed = cfg.exists()
        backup = _backup_file(cfg) if existed else None
        existing = cfg.read_text() if existed else ""
        cleaned = []
        for l in existing.splitlines(keepends=True):
            if marker in l: continue
            if _ENV_LINE_RE.match(l): continue
            cleaned.append(l)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg, "w") as f:
            f.writelines(cleaned)
            if cleaned and not cleaned[-1].endswith("\n"): f.write("\n")
            f.write(f"\n{line.rstrip()}  {marker}\n")
        return {"success": True, "backup": str(backup) if backup else None,
                "file": str(cfg), "existed_before": existed, "method": f"unix_{shell}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def apply_override(override: str) -> dict:
    res = apply_override_windows(override) if detect_os() == "windows" else apply_override_unix(override, detect_shell())
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
        })
        save_config(cfg)
    return res

def undo_last_override() -> dict:
    cfg = load_config()
    if not cfg["applied_overrides"]:
        return {"success": False, "error": "Nothing to undo."}
    last = cfg["applied_overrides"][-1]

    if detect_os() == "windows":
        try:
            import winreg, ctypes
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

    target = last.get("file")
    if not target:
        return {"success": False, "error": "This entry has no file path (created by older version). Use backup: " + str(last.get("backup"))}
    tp = Path(target)
    try:
        if not tp.exists():
            cfg["applied_overrides"].pop()
            save_config(cfg)
            return {"success": True, "note": "target already gone"}
        text = tp.read_text()
        new_lines = []
        marker = "# Added by ROCmFix"
        for l in text.splitlines(keepends=True):
            if marker in l: continue
            if _ENV_LINE_RE.match(l): continue
            new_lines.append(l)
        if not last.get("existed_before"):
            try: tp.unlink()
            except OSError: pass
        else:
            tp.write_text("".join(new_lines))
        cfg["applied_overrides"].pop()
        save_config(cfg)
        return {"success": True, "restored_from": last.get("backup")}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ──────────────────────────────────────────────────────────────────────
# 10. TELEMETRY SYSTEM
# ──────────────────────────────────────────────────────────────────────

def build_telemetry_payload(gpu, info, rocm_ver, smoke_result=None, benchmark=None):
    cfg = load_config()
    payload = {
        "schema_version": 2, "user_id": cfg["user_id"], "rocmfix_version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "os": detect_os(), "shell": detect_shell(),
        "gpu": {"name": gpu.get("name"), "pci_id": gpu.get("pci_id"),
                "driver_version": gpu.get("driver_version")},
        "database": {"in_database": info is not None,
                     "override_recommended": info.get("override") if info else None,
                     "supported_native": info.get("supported") if info else None},
        "rocm_version": rocm_ver,
        "smoke_test": smoke_result if smoke_result else None,
    }
    if benchmark:
        payload["benchmark"] = benchmark
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
    print("  ROCmFix can anonymously share GPU detection + benchmark data.")
    print("  A random install ID is generated. The server sees your public IP")
    print("  (standard HTTP), but no IP is stored with the payload.\n")
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

# ─── 12. DUAL-BACKEND BENCHMARK HELPER (VULKAN VS HIP) ─────────────
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

def _unload_lmstudio(env: dict, model_id: str | None = None):
    cmds = [["lms", "unload", "--all"], ["lms", "unload", "-a"]]
    if model_id: cmds.insert(0, ["lms", "unload", str(model_id)])
    for cmd in cmds: safe_run(cmd, env=env, timeout=15)
    time.sleep(2)

def bench_via_lmstudio(gpu_override: str | None) -> dict:
    """Run dual-backend benchmark (Vulkan vs HIP) via LM Studio."""
    lms_cli = shutil.which("lms") is not None
    
    server_running = False
    try:
        models_data = _lms_api("GET", "/v1/models", timeout=3)
        server_running = True
    except Exception:
        models_data = {}

    if not server_running and lms_cli:
        print(f"  {C.GRAY}Starting LM Studio server via CLI...{C.RESET}")
        flags = 0x08000000 if detect_os() == "windows" else 0
        try:
            subprocess.Popen(["lms", "server", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=flags)
            time.sleep(3)
            models_data = _lms_api("GET", "/v1/models", timeout=5)
            server_running = True
        except Exception:
            pass

    if not server_running:
        return {"error": "LM Studio server is not running on port 1234. Start LM Studio and enable Local Server."}

    available_models = [m.get("id") for m in (models_data.get("data") or []) if m.get("id")]
    chosen_model = None

    if available_models:
        chosen_model = available_models[0]
    elif lms_cli:
        ls_out, _, _ = safe_run(["lms", "ls"])
        for line in ls_out.splitlines():
            line = line.strip()
            if not line or line.lower().startswith(("you have", "params", "identifier", "name", "---", "path")):
                continue
            parts = line.split()
            if parts and not parts[0].endswith(".gguf"):
                chosen_model = parts[0]
                break

    if not chosen_model:
        fs_models = find_lm_studio_models()
        if fs_models:
            chosen_model = Path(fs_models[0]).stem

    if not chosen_model:
        return {"error": "No models found in LM Studio. Download a model in LM Studio first."}

    print(f"  Model: {chosen_model}\n")
    results = {"runtime": "lm_studio", "model": chosen_model, "vulkan_toks": 0.0, "hip_toks": 0.0, "winner": "tie"}

    prompt_payload = {
        "model": chosen_model,
        "messages": [{"role": "user", "content": "Write a 40 word story about a robot."}],
        "max_tokens": 64,
        "stream": False,
        "temperature": 0.0
    }

    # Dual-Pass Benchmark: Test VULKAN, then test HIP
    for backend in ["vulkan", "rocm"]:
        print(f"  Testing {C.BOLD}{backend.upper()}{C.RESET}...")
        env = os.environ.copy()
        env["OLLAMA_GPU_BACKEND"] = backend
        if backend == "vulkan": env["GGML_VK_VISIBLE_DEVICES"] = "0"
        if gpu_override: env["HSA_OVERRIDE_GFX_VERSION"] = gpu_override

        _unload_lmstudio(env, chosen_model)
        safe_run(["lms", "server", "stop"], env=env, timeout=15)
        time.sleep(1)

        try:
            subprocess.Popen(
                ["lms", "server", "start"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=0x08000000 if detect_os() == "windows" else 0
            )
        except Exception:
            pass

        time.sleep(3)
        safe_run(["lms", "load", chosen_model, "-y"], env=env, timeout=120)
        time.sleep(3)

        try:
            start = time.time()
            data = _lms_api("POST", "/v1/chat/completions", prompt_payload, timeout=180)
            elapsed = max(time.time() - start, 0.001)
            usage = data.get("usage") or {}
            toks = usage.get("completion_tokens") or 32
            tok_s = round(toks / elapsed, 2)
            results[f"{'vulkan' if backend=='vulkan' else 'hip'}_toks"] = tok_s
            print(f"    {C.GREEN}→ {tok_s} tok/s{C.RESET}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            print(f"    {C.RED}→ HTTP {e.code}: {body[:100]}{C.RESET}")
        except Exception as e:
            print(f"    {C.RED}→ Failed: {e}{C.RESET}")

        _unload_lmstudio(env, chosen_model)

    safe_run(["lms", "server", "stop"])
    vk, hp = results["vulkan_toks"], results["hip_toks"]
    results["winner"] = "vulkan" if vk > hp else "hip" if hp > vk else "tie"
    return results

def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None

def bench_via_ollama(gpu_override: str | None) -> dict:
    """Run dual-backend benchmark (Vulkan vs HIP) via isolated Ollama server."""
    if not _ollama_installed():
        return {"error": "ollama not installed"}

    models_out, _, _ = safe_run(["ollama", "list"])
    models = [line.split()[0] for line in models_out.splitlines()[1:] if line.strip()]
    if not models:
        return {"error": "no ollama models installed"}

    model = models[0]
    prompt = "Write a short 30 word story."
    print(f"  Model: {model}\n")
    results = {"runtime": "ollama", "model": model, "vulkan_toks": 0.0, "hip_toks": 0.0, "winner": "tie"}

    for backend in ["vulkan", "rocm"]:
        print(f"  Testing {C.BOLD}{backend.upper()}{C.RESET}...")
        
        env = os.environ.copy()
        env["OLLAMA_GPU_BACKEND"] = backend
        env["OLLAMA_HOST"] = "127.0.0.1:11435"  # ISOLATED PORT TO PREVENT KILLING USER OLLAMA
        if gpu_override:
            env["HSA_OVERRIDE_GFX_VERSION"] = gpu_override

        try:
            proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=0x08000000 if detect_os() == "windows" else 0)
        except Exception as e:
            print(f"    {C.RED}→ Failed to launch isolated Ollama server: {e}{C.RESET}")
            continue

        time.sleep(4)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:11435/api/generate",
                data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
                headers={"Content-Type": "application/json"}
            )
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

def cmd_bench(args):
    print_header()
    sync_gpu_database()
    print(f"{C.BOLD}{C.CYAN}🏎️  Backend Benchmarker (Vulkan vs HIP){C.RESET}\n")

    has_ollama = _ollama_installed()
    lms_models = find_lm_studio_models()
    has_lms = bool(shutil.which("lms")) or bool(lms_models)

    print(f"{C.BOLD}Detected AI runtimes:{C.RESET}")
    print(f"  {'✓ Ollama' if has_ollama else '✗ Ollama'}")
    print(f"  {'✓ LM Studio' if has_lms else '✗ LM Studio'}\n")

    gpus = detect_gpus()
    gpu_override = None
    target_gpu = gpus[0] if gpus else None
    if target_gpu:
        info = GPU_DATABASE.get(target_gpu["pci_id"])
        if info and info.get("override"):
            gpu_override = info["override"]

    result = None

    # 1. Try LM Studio first if available
    if has_lms:
        print(f"  Benchmarking via {C.BOLD}LM Studio{C.RESET}...")
        result = bench_via_lmstudio(gpu_override)

    # 2. Try Ollama if LM Studio failed or wasn't present
    if (not result or "error" in result) and has_ollama:
        print(f"  Benchmarking via {C.BOLD}Ollama{C.RESET}...")
        result = bench_via_ollama(gpu_override)

    if not result or "error" in result:
        err_msg = result.get("error") if result else "No AI runtimes or models found."
        print(f"  {C.RED}✗ {err_msg}{C.RESET}\n")
        return

    vk, hp = result["vulkan_toks"], result["hip_toks"]
    print(f"\n{C.BOLD}🏆 Results:{C.RESET}")
    print(f"  Vulkan: {vk} tok/s")
    print(f"  HIP/ROCm: {hp} tok/s")

    if vk > hp and vk > 0:
        print(f"\n  {C.GREEN}Winner: VULKAN (+{int((vk/hp-1)*100 if hp else 0)}%){C.RESET}")
    elif hp > vk and hp > 0:
        print(f"\n  {C.GREEN}Winner: HIP/ROCm (+{int((hp/vk-1)*100 if vk else 0)}%){C.RESET}")
    else:
        print(f"\n  {C.YELLOW}No clear winner or single backend tested.{C.RESET}")

    cfg = load_config()
    if cfg.get("telemetry_enabled") and target_gpu:
        info = GPU_DATABASE.get(target_gpu["pci_id"])
        payload = build_telemetry_payload(target_gpu, info, detect_rocm_version(), benchmark=result)
        send_telemetry(payload)
        print(f"\n  {C.GREEN}✓ Benchmark results sent to community DB!{C.RESET}\n")

# ──────────────────────────────────────────────────────────────────────
# 13. COMMAND DISPATCH
# ──────────────────────────────────────────────────────────────────────

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
        info = GPU_DATABASE.get(gpu["pci_id"])
        if cfg["telemetry_enabled"]:
            send_telemetry(build_telemetry_payload(gpu, info, rocm_ver))

        if not info:
            print(f"      Status:  {C.YELLOW}UNKNOWN — not in database{C.RESET}")
            print(f"\n      Help us add it:\n      {C.BLUE}{get_issue_url(gpu, rocm_ver)}{C.RESET}")
            continue

        if info.get("override") is None and not info.get("supported"):
            print(f"      Status:  {C.YELLOW}Not useful for AI (iGPU){C.RESET}\n      Notes:   {info.get('notes','')}")
            continue

        if info.get("supported"):
            print(f"      Status:  {C.GREEN}Natively supported{C.RESET}\n      Notes:   {info.get('notes','')}")
            continue

        print(f"      Status:  {C.YELLOW}Needs override{C.RESET}")
        print(f"      gfx target: {info.get('gfx_target','?')} → override {info.get('override')}")
        for issue in info.get("known_issues", []):
            print(f"        - {issue}")

        print(f"\n      {C.BOLD}Apply this override?{C.RESET}  [A]uto  [S]how  [N]o")
        try: ch = input("      > ").strip().lower()
        except Exception: ch = "n"
        if ch == "a":
            res = apply_override(info["override"])
            if res["success"]:
                print(f"\n      {C.GREEN}✓ Override applied.{C.RESET}")
                print(f"      {C.YELLOW}⚠ Open a NEW terminal for it to take effect.{C.RESET}")
            else:
                print(f"\n      {C.RED}✗ Failed: {res.get('error')}{C.RESET}\n{format_env_command(info['override'])}")
        elif ch == "s":
            print(f"\n      {C.BOLD}Run in {shell}:{C.RESET}\n{format_env_command(info['override'])}")
        else:
            print(f"      {C.GRAY}Skipped.{C.RESET}")

def cmd_doctor(args):
    print_header(); sync_gpu_database()
    print(f"{C.BOLD}{C.CYAN}🩺 System Doctor{C.RESET}\n")
    gpus = detect_gpus()
    print(f"{C.BOLD}1. Hardware & Drivers{C.RESET}")
    if not gpus: print(f"  {C.RED}✗ No AMD GPUs detected{C.RESET}")
    for g in gpus:
        print(f"  {C.GREEN}✓ GPU:{C.RESET} {g['name']} (PCI: {g['pci_id']})")
        d = analyze_driver(g['driver_version'])
        if d:
            color = C.GREEN if d["status"]=="ok" else C.YELLOW if d["status"]=="warn" else C.RED
            icon = "✓" if d["status"]=="ok" else "⚠" if d["status"]=="warn" else "✗"
            print(f"  {color}{icon} Driver:{C.RESET} {g['driver_version']} — {d['msg']}")
        else:
            print(f"  {C.GRAY}• Driver:{C.RESET} {g['driver_version']}")

    print(f"\n{C.BOLD}2. ROCm / HIP{C.RESET}")
    if detect_os() == "windows":
        hp = os.environ.get("HIP_PATH")
        if hp and Path(hp).exists(): print(f"  {C.GREEN}✓ HIP SDK:{C.RESET} {hp}")
        else:
            print(f"  {C.RED}✗ HIP SDK missing{C.RESET}")
            print(f"    {C.CYAN}Run: rocmfix install-hip{C.RESET}")
    else:
        r = detect_rocm_version()
        if r:
            label = r if r.startswith("installed") else f"v{r}"
            print(f"  {C.GREEN}✓ ROCm:{C.RESET} {label}")
        else:
            print(f"  {C.RED}✗ ROCm not found{C.RESET}")
            print(f"    {C.CYAN}Run: rocmfix install-rocm{C.RESET}")

    print(f"\n{C.BOLD}3. Vulkan{C.RESET}")
    vk, _, _ = safe_run(["vulkaninfo", "--summary"])
    print(f"  {'✓' if ('Vulkan Instance Version' in vk or 'device' in vk.lower()) else '⚠'} Vulkan API")

    print(f"\n{C.BOLD}4. Env override{C.RESET}")
    ov = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
    print(f"  {'✓ ' + ov if ov else '• not set'}")

    print(f"\n{C.BOLD}Recommendation:{C.RESET}")
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        if info:
            print(f"  For {g['name']}: use {C.BOLD}{info.get('rec_backend','vulkan').upper()}{C.RESET}")
            if info.get("override"): print(f"    HSA_OVERRIDE_GFX_VERSION={info['override']} for HIP")
        else:
            print(f"  {g['name']}: not in DB — please open a report.")
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
    try:
        import winreg
        tdr_key = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, tdr_key, 0, winreg.KEY_ALL_ACCESS) as k:
            winreg.SetValueEx(k, "TdrDelay", 0, winreg.REG_DWORD, 60)
            winreg.SetValueEx(k, "TdrDdiDelay", 0, winreg.REG_DWORD, 60)
            winreg.SetValueEx(k, "HwSchMode", 0, winreg.REG_DWORD, 1)
        print(f"  {C.GREEN}✓ TDR + HAGS applied (need admin){C.RESET}")
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

def cmd_export(args):
    print_header(); sync_gpu_database()
    gpus = detect_gpus(); rocm_ver = detect_rocm_version()
    lines = [f"# ROCmFix report", f"- v{__version__}", f"- OS: {detect_os()}", "", "## GPUs", "| Name | PCI | Driver | Status |", "|---|---|---|---|"]
    for g in gpus:
        info = GPU_DATABASE.get(g["pci_id"])
        st = "Unknown"
        if info:
            if info.get("supported"): st = "Native"
            elif info.get("override"): st = f"Override {info['override']}"
            else: st = "iGPU / not for AI"
        lines.append(f"| {g['name']} | `{g['pci_id']}` | {g['driver_version']} | {st} |")
    lines += [f"", f"ROCm: {rocm_ver or 'not detected'}"]
    out = Path.home() / f"rocmfix-report-{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text("\n".join(lines))
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
    print(f"  {C.BOLD}Known GPUs ({len(GPU_DATABASE)}):{C.RESET}\n")
    print(f"  {'PCI':<6} {'Name':<44} {'Arch':<8} {'Override':<10} Status")
    print(f"  {'-'*6} {'-'*44} {'-'*8} {'-'*10} {'-'*10}")
    for pid, info in GPU_DATABASE.items():
        if info.get("supported"): st = f"{C.GREEN}Native{C.RESET}"
        elif info.get("override"): st = f"{C.YELLOW}Override{C.RESET}"
        else: st = f"{C.GRAY}Not for AI{C.RESET}"
        ov = info.get("override") or "-"
        print(f"  {pid:<6} {info['name']:<44} {info['arch']:<8} {ov:<10} {st}")

def cmd_undo(args):
    print_header()
    r = undo_last_override()
    if r["success"]:
        print(f"  {C.GREEN}✓ Reverted.{C.RESET}")
        if r.get("restored_from"): print(f"  {C.GRAY}Backup: {r['restored_from']}{C.RESET}")
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
                            "bench","update","export","sync","optimize"])
    args = p.parse_args()
    disp = {
        "detect": cmd_detect, "test": cmd_test, "list": cmd_list, "verify": cmd_verify,
        "contribute": cmd_contribute, "install": lambda a: install_globally(),
        "undo": cmd_undo, "telemetry": cmd_telemetry, "doctor": cmd_doctor,
        "install-hip": cmd_install_hip, "install-rocm": cmd_install_rocm,
        "bench": cmd_bench, "update": cmd_update, "export": cmd_export,
        "sync": cmd_sync, "optimize": cmd_optimize,
    }
    
    try:
        disp[args.command](args)
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Cancelled by user.{C.RESET}")
        sys.exit(130)

if __name__ == "__main__":
    main()
