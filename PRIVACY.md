# ROCmFix Privacy Policy

Telemetry is **off until you say yes**. ROCmFix asks once on first run, and you can
change your mind any time with `rocmfix telemetry`.

## What ROCmFix sends (only if you opt in)

- **GPU model name** (e.g. "AMD Radeon RX 7800 XT")
- **PCI device ID** (e.g. "747e") and **gfx target** (e.g. "gfx1101")
- **Driver version** (e.g. "32.0.31041.1004")
- **Operating system** (e.g. "windows") and **shell** (e.g. "powershell")
- **ROCm/HIP version**, if installed
- **Recommendation shown** (native / override value) and **smoke test result** (pass/fail)
- **Benchmark numbers** from `rocmfix bench`: runtime (Ollama / LM Studio / llama.cpp),
  backend, tokens per second, time to first token, and the model's **size class and
  quantization only** (e.g. "8B Q4_K_M")
- **Random anonymous install ID** (generated once, stored locally)
- **ROCmFix version and timestamp**

## What ROCmFix never sends

- Your name, username, or email
- **Model names** or model file names
- Server addresses or URLs (e.g. your llama.cpp server)
- File paths on your computer
- Prompts or anything an AI model generates
- Anything else about your system beyond the list above

ROCmFix versions 0.1.7 and older included the model name and llama.cpp server URL in
benchmark reports. The collector now deletes those fields before storing anything.

## Your IP address

Like any web request, the collector server sees the IP address the report comes from,
and the hosting provider (Render) may keep it in short-lived access logs. The IP is
**not stored with the report** and is not used for anything.

## How to see exactly what gets sent

Run `rocmfix telemetry`, then press `S` to view a sample payload.

## How to opt out

Run `rocmfix telemetry` and choose Disable.

## Where data is stored

Reports go to a collector run by the ROCmFix maintainer and are stored in a private
database. The data is used to expand the GPU database, spot driver regressions,
improve override recommendations, and publish aggregate benchmark statistics.

## Offline queue

If the collector is unreachable, reports are saved to `~/.rocmfix/outbox/` (at most
100) and sent on the next successful run, so ROCmFix never blocks waiting for the server.

## Local-only files

`rocmfix bench` keeps a local history in `~/.rocmfix/bench_history.json` so it can
compare backends across runs. That file includes model names and **never leaves your
computer**.
