# ROCmFix Privacy Policy

## What ROCmFix collects (only if you opt in)

- **GPU model name** (e.g. "RX 7800 XT")
- **PCI device ID** (e.g. "7470")
- **Driver version** (e.g. "32.0.31041.1004")
- **Operating system** (e.g. "windows")
- **ROCm/HIP version** if installed
- **Override value used** (e.g. "11.0.0")
- **Smoke test result** (pass/fail)
- **Random anonymous user ID** (generated once, stored locally)
- **ROCmFix version and timestamp**

## What ROCmFix does NOT collect

- Your name, username, or email
- Your IP address (Cloudflare Tunnel strips this)
- File paths or filenames on your computer
- Model files or model names you use
- Any content you generate with AI
- Any personal or system information beyond GPU specs

## How to opt out

Run: `rocmfix telemetry` and choose Disable.

## How to see exactly what gets sent

Run: `rocmfix telemetry`, then press `S` to view a sample payload.

## Where data is stored

Anonymous reports are sent to a private server run by the ROCmFix maintainer.
Data is used to expand the GPU database, detect driver regressions, and
improve override recommendations.

## Data queue when offline

If the collector server is unreachable, reports are saved to
`~/.rocmfix/outbox/` and sent on the next successful run. This means your PC
never blocks or fails waiting for the server.