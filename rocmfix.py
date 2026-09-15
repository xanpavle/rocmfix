#!/usr/bin/env python3
"""
ROCmFix — Cross-platform AMD GPU override detector and tester for ROCm/HIP.
Single-file, zero external dependencies.
"""

import os
import sys
import platform
import re
import subprocess
import urllib.parse
import json
import argparse
import shutil
from pathlib import Path

__version__ = "0.1.1"
GITHUB_REPO = "xanpavle/rocmfix"

# ──────────────────────────────────────────────────────────────────────
# 1. DATABASE
# ──────────────────────────────────────────────────────────────────────

GPU_DATABASE = {
    # ── RDNA 3 ──────────────────────────────────────────────
    "744c": {
        "name": "RX 7900 XTX", "arch": "RDNA3", "gfx_target": "gfx1100",
        "override": None, "supported": True, "known_issues": [],
        "notes": "Natively supported. No override needed."
    },
    "744e": {
        "name": "RX 7900 XT", "arch": "RDNA3", "gfx_target": "gfx1100",
        "override": None, "supported": True, "known_issues": [],
        "notes": "Natively supported. No override needed."
    },
    "747e": {
        "name": "RX 7900 GRE / RX 7800 XT (variant)", "arch": "RDNA3",
        "gfx_target": "gfx1100",
        "override": None, "supported": True,
        "known_issues": [
            "Some 7800 XT AIB models share this PCI ID but are gfx1101 internally",
            "If llama.cpp fails with HIP errors, try override 11.0.0",
        ],
        "notes": "Usually natively supported. If your card is actually a 7800 XT variant and HIP fails, set override to 11.0.0."
    },
    "7470": {
        "name": "RX 7800 XT", "arch": "RDNA3", "gfx_target": "gfx1101",
        "override": "11.0.0", "supported": False,
        "known_issues": [
            "FP16 atomic operations may produce incorrect results",
            "Some custom HIP kernels using wave32 mode may crash",
            "Flash Attention 2 may fail on certain model sizes",
        ],
        "notes": "Override to gfx1100. Works well for llama.cpp and most GGUF inference."
    },
    "7471": {
        "name": "RX 7700 XT", "arch": "RDNA3", "gfx_target": "gfx1101",
        "override": "11.0.0", "supported": False,
        "known_issues": [
            "FP16 atomic operations may produce incorrect results",
            "Some custom HIP kernels using wave32 mode may crash",
        ],
        "notes": "Same override as 7800 XT. 12GB VRAM limits larger models."
    },
    "7480": {
        "name": "RX 7600", "arch": "RDNA3", "gfx_target": "gfx1102",
        "override": "11.0.0", "supported": False,
        "known_issues": [
            "FP16 atomics unreliable",
            "8GB VRAM severely limits model size",
            "Vulkan backend may outperform HIP on this card",
        ],
        "notes": "Override to gfx1100. Consider Vulkan backend for better perf."
    },
    "7483": {
        "name": "RX 7600 XT", "arch": "RDNA3", "gfx_target": "gfx1102",
        "override": "11.0.0", "supported": False,
        "known_issues": [
            "FP16 atomics unreliable",
            "Vulkan backend may outperform HIP on this card",
        ],
        "notes": "Override to gfx1100. 16GB VRAM is nice for the price."
    },
    # ── RDNA 2 ──────────────────────────────────────────────
    "73af": {
        "name": "RX 6900 XT", "arch": "RDNA2", "gfx_target": "gfx1030",
        "override": None, "supported": True, "known_issues": [],
        "notes": "Natively supported. Best RDNA2 card for ROCm."
    },
    "73bf": {
        "name": "RX 6800 XT / 6800", "arch": "RDNA2", "gfx_target": "gfx1030",
        "override": None, "supported": True, "known_issues": [],
        "notes": "Natively supported."
    },
    "73df": {
        "name": "RX 6700 XT", "arch": "RDNA2", "gfx_target": "gfx1031",
        "override": "10.3.0", "supported": False,
        "known_issues": [
            "Matrix core operations may be slower than native gfx1030",
            "Some BLAS operations produce NaN with FP16",
        ],
        "notes": "Override to gfx1030. 12GB VRAM is decent for 7B-13B models."
    },
    "73ff": {
        "name": "RX 6600 XT / 6600", "arch": "RDNA2", "gfx_target": "gfx1032",
        "override": "10.3.0", "supported": False,
        "known_issues": [
            "Significantly slower than gfx1030 native cards",
            "8GB VRAM limits model size",
            "Vulkan often faster than HIP on this chip",
        ],
        "notes": "Override to gfx1030. Strongly consider Vulkan backend."
    },
    "743f": {
        "name": "RX 6500 XT", "arch": "RDNA2", "gfx_target": "gfx1034",
        "override": "10.3.0", "supported": False,
        "known_issues": [
            "4GB VRAM barely usable for LLM inference",
            "PCIe x4 bus severely limits CPU to GPU transfer",
        ],
        "notes": "Override to gfx1030. Honestly barely worth it for AI."
    },
    # ── INTEGRATED GPUs ─────────────────────────────────────
    "164e": {
        "name": "Radeon Graphics (Ryzen 7000 iGPU)", "arch": "RDNA2",
        "gfx_target": "gfx1036",
        "override": None, "supported": False,
        "known_issues": [
            "Integrated GPU — shares system RAM, no dedicated VRAM",
            "Not usable for ROCm/HIP inference",
            "Use your dedicated GPU instead",
        ],
        "notes": "This is the built-in GPU in your Ryzen CPU. Ignore it for AI — use your RX 7800 XT."
    },
    "1638": {
        "name": "Radeon Graphics (Ryzen 5000 iGPU)", "arch": "Vega",
        "gfx_target": "gfx90c",
        "override": None, "supported": False,
        "known_issues": [
            "Integrated GPU — not usable for ROCm inference",
        ],
        "notes": "Built-in Vega iGPU in Ryzen 5000G APUs. Ignore for AI."
    },
}

# ──────────────────────────────────────────────────────────────────────
# 2. DETECTION
# ──────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], shell: bool = False) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, shell=shell, timeout=10
        )
        return result.stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""

def detect_os() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return "unknown"

def detect_shell() -> str:
    if detect_os() != "windows":
        return "bash"
    if os.environ.get("PROMPT") and not os.environ.get("PSExecutionPolicyPreference"):
        return "cmd"
    if os.environ.get("PSModulePath"):
        return "powershell"
    return "cmd"

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
                    subkey_name = winreg.EnumKey(class_key, i)
                    i += 1
                    if subkey_name in ("Properties",):
                        continue
                    with winreg.OpenKey(class_key, subkey_name) as subkey:
                        try:
                            desc = winreg.QueryValueEx(subkey, "DriverDesc")[0]
                        except FileNotFoundError:
                            continue

                        if not any(v in desc.lower() for v in ["amd", "radeon", "ati"]):
                            continue

                        pci_id = "unknown"
                        raw_pnp = desc
                        try:
                            matching_id = winreg.QueryValueEx(subkey, "MatchingDeviceId")[0]
                            raw_pnp = matching_id
                            match = re.search(r"VEN_1002&DEV_([0-9A-Fa-f]{4})", matching_id)
                            if match:
                                pci_id = match.group(1).lower()
                        except FileNotFoundError:
                            pass

                        driver = "unknown"
                        try:
                            driver = winreg.QueryValueEx(subkey, "DriverVersion")[0]
                        except FileNotFoundError:
                            pass

                        gpus.append({
                            "pci_id": pci_id,
                            "name": desc,
                            "driver_version": driver,
                            "raw_pnp": raw_pnp,
                        })
                except OSError:
                    break
    except OSError:
        pass
    return gpus

def _detect_windows_wmic() -> list[dict]:
    gpus = []
    output = _run(["wmic", "path", "win32_VideoController",
                    "get", "Name,PNPDeviceID,DriverVersion", "/format:list"])
    if not output:
        return gpus

    blocks = re.split(r"\n\s*\n", output.strip())
    for block in blocks:
        entry = {}
        for line in block.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                entry[k.strip()] = v.strip()
        name = entry.get("Name", "")
        pnp = entry.get("PNPDeviceID", "")
        driver = entry.get("DriverVersion", "unknown")

        if any(v in name.lower() for v in ["amd", "radeon", "ati"]):
            match = re.search(r"VEN_1002&DEV_([0-9A-Fa-f]{4})", pnp)
            if match:
                gpus.append({
                    "pci_id": match.group(1).lower(),
                    "name": name,
                    "driver_version": driver,
                    "raw_pnp": pnp,
                })
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
                try:
                    driver = ver_file.read_text().strip()
                except OSError:
                    pass
            else:
                mod_ver = _run(["modinfo", "-F", "version", "amdgpu"])
                if mod_ver.strip():
                    driver = mod_ver.strip()
            gpus.append({
                "pci_id": pci_id, "name": name,
                "driver_version": driver, "raw_pnp": line.strip()
            })
    return gpus

def detect_gpus() -> list[dict]:
    os_name = detect_os()
    if os_name == "windows":
        gpus = _detect_windows_registry()
        if not gpus:
            gpus = _detect_windows_wmic()
        return gpus
    if os_name == "linux":
        return _detect_linux()
    return []

def detect_rocm_version() -> str | None:
    os_name = detect_os()
    if os_name == "linux":
        version_file = Path("/opt/rocm/.info/version")
        if version_file.exists():
            try:
                return version_file.read_text().strip()
            except OSError:
                pass
        output = _run(["rocminfo"])
        match = re.search(r"ROCm Version:\s*([\d.]+)", output)
        if match:
            return match.group(1)
    elif os_name == "windows":
        hip_path = os.environ.get("HIP_PATH", "")
        if hip_path:
            match = re.search(r"[\\\/](\d+\.\d+)[\\\/]?$", hip_path.rstrip("\\/"))
            if match:
                return match.group(1)
        output = _run(["hipconfig", "--version"])
        if output.strip():
            return output.strip().split()[0]
    return None

# ──────────────────────────────────────────────────────────────────────
# 3. SMOKE TESTING
# ──────────────────────────────────────────────────────────────────────

def run_smoke_test(override: str | None) -> dict:
    os_name = detect_os()
    env = os.environ.copy()
    if override:
        env["HSA_OVERRIDE_GFX_VERSION"] = override
    else:
        env.pop("HSA_OVERRIDE_GFX_VERSION", None)

    if os_name == "linux":
        try:
            res = subprocess.run(["rocminfo"], capture_output=True, text=True,
                                 env=env, timeout=10)
            if res.returncode != 0 or "Agent" not in res.stdout:
                return {"success": False,
                        "message": "rocminfo failed to find a GPU agent.",
                        "details": (res.stderr or res.stdout)[:300]}
            gpu_count = res.stdout.count("Device Type:                     GPU")
            if gpu_count == 0:
                return {"success": False,
                        "message": "rocminfo ran but found 0 GPU agents.",
                        "details": ""}
            return {"success": True,
                    "message": f"ROCm detected {gpu_count} GPU agent(s).",
                    "details": f"HSA_OVERRIDE_GFX_VERSION={override or 'not set'}"}
        except FileNotFoundError:
            return {"success": False,
                    "message": "rocminfo not found. Is ROCm installed?",
                    "details": "Install: https://rocm.docs.amd.com/"}
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}

    elif os_name == "windows":
        hip_path = os.environ.get("HIP_PATH", "")
        if not hip_path:
            return {"success": False,
                    "message": "HIP_PATH not set. Install the HIP SDK.",
                    "details": "https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html"}
        hipinfo = os.path.join(hip_path, "bin", "hipInfo.exe")
        if not os.path.exists(hipinfo):
            hipinfo = os.path.join(hip_path, "bin", "hipinfo.exe")
        if not os.path.exists(hipinfo):
            return {"success": False,
                    "message": "hipInfo.exe not found in HIP SDK.",
                    "details": f"Looked in: {hip_path}\\bin\\"}
        try:
            res = subprocess.run([hipinfo], capture_output=True, text=True,
                                 env=env, timeout=10)
            if res.returncode != 0:
                return {"success": False, "message": "hipInfo returned error.",
                        "details": (res.stderr or res.stdout)[:300]}
            if "device" not in res.stdout.lower():
                return {"success": False,
                        "message": "hipInfo ran but found no devices.",
                        "details": res.stdout[:300]}
            return {"success": True,
                    "message": "HIP SDK detected your GPU.",
                    "details": f"HSA_OVERRIDE_GFX_VERSION={override or 'not set'}"}
        except Exception as e:
            return {"success": False, "message": str(e), "details": ""}

    return {"success": False, "message": "Unsupported OS", "details": ""}

# ──────────────────────────────────────────────────────────────────────
# 4. AUTO-INSTALL LOGIC (Global command registrar)
# ──────────────────────────────────────────────────────────────────────

def is_installed_globally() -> bool:
    """Check if the command 'rocmfix' is callable on PATH."""
    return shutil.which("rocmfix") is not None

def install_globally() -> bool:
    """Interactively configure the tool as a global command."""
    os_name = detect_os()
    script_path = Path(__file__).resolve()
    script_dir = script_path.parent

    print(f"\n{C.BOLD}{C.CYAN}⚙️ Automatic Global Command Setup{C.RESET}")
    print("This will let you run 'rocmfix' from any folder on your computer.\n")
    
    confirm = input("Would you like to register 'rocmfix' globally? (y/n): ").strip().lower()
    if confirm != 'y':
        print(f"{C.GRAY}Skip registration.{C.RESET}\n")
        return False

    if os_name == "windows":
        # 1. Create rocmfix.bat wrapper
        bat_path = script_dir / "rocmfix.bat"
        try:
            bat_path.write_text(f'@echo off\npython "{script_path}" %*\n')
        except Exception as e:
            print(f"{C.RED}Failed to create batch wrapper: {e}{C.RESET}")
            return False

        # 2. Add to user registry path
        try:
            import winreg
            import ctypes

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            try:
                path_val, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                path_val = ""

            paths = [p.strip().rstrip("\\/") for p in path_val.split(";")]
            current_dir_str = str(script_dir).rstrip("\\/")

            if current_dir_str not in paths:
                new_path = path_val + ";" + str(script_dir) if path_val else str(script_dir)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                
                # Force Windows to reload environment variables instantly
                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x001A
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000, ctypes.byref(ctypes.c_long())
                )
                print(f"\n{C.GREEN}✓ Successfully added '{script_dir}' to your user PATH.{C.RESET}")
                print(f"{C.YELLOW}⚠ Please close this terminal and open a NEW one for the command to work.{C.RESET}\n")
            else:
                print(f"\n{C.GREEN}✓ 'rocmfix' is already registered in your PATH.{C.RESET}\n")
            
            winreg.CloseKey(key)
            return True
        except Exception as e:
            print(f"{C.RED}Failed to edit Windows PATH Registry: {e}{C.RESET}")
            return False

    elif os_name == "linux":
        # For Linux, save to ~/.local/bin as 'rocmfix' (Standard Linux convention, no extension)
        local_bin = Path.home() / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        dest_file = local_bin / "rocmfix"

        try:
            shutil.copy2(script_path, dest_file)
            dest_file.chmod(0o755)  # Make executable
            print(f"\n{C.GREEN}✓ Copied to {dest_file} (no extension) and made executable.{C.RESET}")

            # Verify PATH
            if str(local_bin) not in os.environ.get("PATH", ""):
                print(f"{C.YELLOW}⚠ {local_bin} is not in your current PATH variable.{C.RESET}")
                print(f"  Add this line to your ~/.bashrc or ~/.zshrc file:")
                print(f"  {C.BOLD}export PATH=\"$HOME/.local/bin:$PATH\"{C.RESET}\n")
            else:
                print(f"{C.GREEN}✓ 'rocmfix' global command is ready!{C.RESET}\n")
            return True
        except Exception as e:
            print(f"{C.RED}Failed to copy binary file: {e}{C.RESET}")
            return False

    return False

# ──────────────────────────────────────────────────────────────────────
# 5. REPORTING UTILS
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
    if detect_os() == "windows":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetConsoleMode(k32.GetStdHandle(-11), 7)
        except Exception:
            for attr in dir(C):
                if not attr.startswith("_") and attr != "RESET":
                    setattr(C, attr, "")
            C.RESET = ""

def format_env_command(override: str) -> str:
    os_name = detect_os()
    shell = detect_shell()
    lines = []

    if os_name == "windows":
        if shell == "powershell":
            lines.append(f"  {C.BOLD}PowerShell (this session):{C.RESET}")
            lines.append(f'    $env:HSA_OVERRIDE_GFX_VERSION="{override}"')
            lines.append("")
            lines.append(f"  {C.BOLD}PowerShell (permanent):{C.RESET}")
            lines.append(f'    [Environment]::SetEnvironmentVariable("HSA_OVERRIDE_GFX_VERSION", "{override}", "User")')
        else:
            lines.append(f"  {C.BOLD}CMD (this session):{C.RESET}")
            lines.append(f"    set HSA_OVERRIDE_GFX_VERSION={override}")
            lines.append("")
            lines.append(f"  {C.BOLD}CMD (permanent):{C.RESET}")
            lines.append(f"    setx HSA_OVERRIDE_GFX_VERSION {override}")
            lines.append(f"    {C.GRAY}(open a NEW terminal after setx){C.RESET}")
    else:
        lines.append(f"  {C.BOLD}Bash/Zsh (this session):{C.RESET}")
        lines.append(f"    export HSA_OVERRIDE_GFX_VERSION={override}")
        lines.append("")
        lines.append(f"  {C.BOLD}Permanent:{C.RESET}")
        lines.append(f"    echo 'export HSA_OVERRIDE_GFX_VERSION={override}' >> ~/.bashrc")

    return "\n".join(lines)

def get_issue_url(gpu: dict, rocm_ver: str | None) -> str:
    title = f"[GPU Report] Unknown AMD GPU: {gpu.get('name','?')} ({gpu.get('pci_id','?')})"
    body = (
        f"## Unknown GPU\n\n"
        f"- **Name:** {gpu.get('name')}\n"
        f"- **PCI ID:** `{gpu.get('pci_id')}`\n"
        f"- **Driver:** {gpu.get('driver_version')}\n"
        f"- **ROCm:** {rocm_ver or 'not detected'}\n"
        f"- **OS:** {detect_os()}\n\n"
        f"*What override value made this card work?*"
    )
    params = urllib.parse.urlencode({"title": title, "body": body, "labels": "unknown-gpu"})
    return f"https://github.com/{GITHUB_REPO}/issues/new?{params}"

# ──────────────────────────────────────────────────────────────────────
# 6. CLI
# ──────────────────────────────────────────────────────────────────────

def print_header():
    print(f"{C.BOLD}{C.CYAN}")
    print("  ╔══════════════════════════════════════╗")
    print("  ║           ROCmFix v" + __version__.ljust(18) + "║")
    print("  ║   AMD GPU override helper for ROCm   ║")
    print("  ╚══════════════════════════════════════╝")
    print(f"{C.RESET}")

def main():
    enable_ansi()

    parser = argparse.ArgumentParser(description="ROCmFix: AMD GPU override helper")
    parser.add_argument("command", nargs="?", default="detect",
                        choices=["detect", "test", "list", "contribute", "install", "verify"])
    args = parser.parse_args()

    # ── FORCE GLOBAL INSTALL ────────────────────────────────
    if args.command == "install":
        print_header()
        install_globally()
        sys.exit(0)

    # ── FIRST RUN INTERACTIVE CHECK ─────────────────────────
    # If the app is run from double-click or python directly and hasn't been registered yet.
    if args.command == "detect" and not is_installed_globally():
        print_header()
        install_globally()
        print(f"{C.GRAY}Continuing to system diagnostics...{C.RESET}\n")

    # ── DETECT ──────────────────────────────────────────────
    if args.command == "detect":
        # Skip header printing if we already did it in the install loop
        if is_installed_globally():
            print_header()

        os_name = detect_os()
        shell = detect_shell()
        rocm_ver = detect_rocm_version()

        print(f"{C.BOLD}System Info:{C.RESET}")
        print(f"  OS:       {os_name}")
        print(f"  Shell:    {shell}")
        print(f"  ROCm/HIP: {rocm_ver or f'{C.YELLOW}Not detected{C.RESET}'}\n")

        gpus = detect_gpus()
        if not gpus:
            print(f"{C.RED}No AMD GPUs detected.{C.RESET}")
            if os_name == "windows":
                print("  Make sure AMD Adrenalin drivers are installed.")
                print("  Check Device Manager > Display Adapters.")
            sys.exit(1)

        print(f"{C.BOLD}Detected {len(gpus)} AMD GPU(s):{C.RESET}")
        for i, gpu in enumerate(gpus, 1):
            print(f"\n  [{i}] {C.BOLD}{gpu['name']}{C.RESET}")
            print(f"      PCI ID:  {gpu['pci_id']}")
            print(f"      Driver:  {gpu['driver_version']}")

            info = GPU_DATABASE.get(gpu["pci_id"])
            if not info:
                print(f"      Status:  {C.YELLOW}UNKNOWN — not in database{C.RESET}")
                print(f"\n      Help us add it! Open this link:")
                print(f"      {C.BLUE}{get_issue_url(gpu, rocm_ver)}{C.RESET}")
                continue

            if info["supported"]:
                print(f"      Status:  {C.GREEN}Natively supported by ROCm{C.RESET}")
                print(f"      Override: Not needed")
                print(f"      Notes:   {info['notes']}")
            else:
                print(f"      Status:  {C.YELLOW}Needs override{C.RESET}")
                print(f"      Real gfx target: {info['gfx_target']}")
                print(f"      Override value:  {C.GREEN}{info['override']}{C.RESET}")
                if info["known_issues"]:
                    print(f"      {C.RED}Known issues:{C.RESET}")
                    for issue in info["known_issues"]:
                        print(f"        - {issue}")
                print(f"\n      {C.BOLD}Run this in your terminal ({shell}):{C.RESET}")
                print()
                print(format_env_command(info["override"]))
                print()
                print(f"      Then verify with: {C.CYAN}rocmfix test{C.RESET}")
                print(f"      Notes: {info['notes']}")

    # ── TEST ────────────────────────────────────────────────
    elif args.command == "test":
        print_header()
        current = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
        print(f"  HSA_OVERRIDE_GFX_VERSION = {C.BOLD}{current or 'NOT SET'}{C.RESET}\n")
        print("  Running smoke test...\n")
        result = run_smoke_test(current)
        if result["success"]:
            print(f"  {C.GREEN}PASS: {result['message']}{C.RESET}")
        else:
            print(f"  {C.RED}FAIL: {result['message']}{C.RESET}")
            if result["details"]:
                print(f"  {C.GRAY}{result['details']}{C.RESET}")

    # ── LIST ────────────────────────────────────────────────
    elif args.command == "list":
        print_header()
        print(f"  {C.BOLD}Known GPUs ({len(GPU_DATABASE)}):{C.RESET}\n")
        print(f"  {'PCI':<6} {'Name':<24} {'Arch':<8} {'Override':<10} Status")
        print(f"  {'---':<6} {'----':<24} {'----':<8} {'--------':<10} ------")
        for pid, info in GPU_DATABASE.items():
            st = f"{C.GREEN}Native{C.RESET}" if info["supported"] else f"{C.YELLOW}Override{C.RESET}"
            ov = info["override"] or "-"
            print(f"  {pid:<6} {info['name']:<24} {info['arch']:<8} {ov:<10} {st}")

    # ── CONTRIBUTE ──────────────────────────────────────────
    elif args.command == "contribute":
        print_header()
        print("  Add a new GPU to the database.\n")
        pci_id = input("  PCI ID (4 hex, e.g. 7470): ").strip().lower()
        name   = input("  GPU Name (e.g. RX 7800 XT): ").strip()
        arch   = input("  Arch (RDNA3/RDNA2/Vega): ").strip()
        gfx    = input("  Real gfx target (e.g. gfx1101): ").strip()
        ov     = input("  Override (e.g. 11.0.0, blank=none): ").strip()
        notes  = input("  Notes: ").strip()
        print(f"\n  {C.GREEN}Open a PR on github.com/{GITHUB_REPO} with this:{C.RESET}\n")
        print(f'    "{pci_id}": {{')
        print(f'        "name": "{name}", "arch": "{arch}",')
        print(f'        "gfx_target": "{gfx}",')
        print(f'        "override": {"\"" + ov + "\"" if ov else "None"},')
        print(f'        "supported": {ov == ""},')
        print(f'        "known_issues": [], "notes": "{notes}"')
        print("    },")

    # ── VERIFY ──────────────────────────────────────────────
    elif args.command == "verify":
        print_header()
        print("  Verify a specific PCI ID + override combination.\n")
        pci_id = input("  PCI ID to verify (4 hex): ").strip().lower()
        override = input("  Override value to test (e.g. 11.0.0): ").strip()

        info = GPU_DATABASE.get(pci_id)
        if info:
            print(f"\n  {C.YELLOW}This GPU is already in the database:{C.RESET}")
            print(f"    Name:     {info['name']}")
            print(f"    Override: {info['override'] or 'None'}")
            print(f"    Status:   {'Supported' if info['supported'] else 'Override'}")
            if override and override != (info['override'] or ''):
                print(f"\n  {C.RED}⚠ Your override ({override}) differs from database ({info['override']}).{C.RESET}")
                print(f"    If yours works better, please open an issue!")
        else:
            print(f"\n  {C.CYAN}PCI {pci_id} is NOT in the database yet.{C.RESET}")

        print(f"\n  Running smoke test with HSA_OVERRIDE_GFX_VERSION={override}...\n")
        result = run_smoke_test(override if override else None)
        if result["success"]:
            print(f"  {C.GREEN}✓ PASS: {result['message']}{C.RESET}")
            print(f"\n  {C.BOLD}This override works! Please submit it:{C.RESET}")
            print(f"  {C.BLUE}https://github.com/{GITHUB_REPO}/issues/new?template=gpu-report.yml{C.RESET}")
        else:
            print(f"  {C.RED}✗ FAIL: {result['message']}{C.RESET}")
            if result["details"]:
                print(f"  {C.GRAY}{result['details']}{C.RESET}")
            print(f"\n  Try a different override value or check your ROCm/HIP installation.")

if __name__ == "__main__":
    main()
