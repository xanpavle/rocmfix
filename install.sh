#!/usr/bin/env bash
set -e
git clone https://github.com/xanpavle/rocmfix.git
cd rocmfix
pip install .
echo ""
echo "✓ Installed. Run: rocmfix"