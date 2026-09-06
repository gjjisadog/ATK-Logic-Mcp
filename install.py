#!/usr/bin/env python3
"""
Cross-Platform One-Click Installer for ATK-DL16 MCP Server.

Usage:
    python install.py [--client codex|claude|cursor|none]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="ATK-DL16 MCP Server One-Click Installer")
    parser.add_argument(
        "--client",
        default="codex",
        choices=["codex", "claude", "cursor", "none"],
        help="Target AI client to configure (default: codex)"
    )
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parent

    print("=" * 60)
    print("  ATK-DL16 Logic Analyzer MCP Server - One-Click Installer")
    print("=" * 60)

    # 1. Bundle binary if available locally
    pkg_bin_dir = root_dir / "atk_dl16_mcp" / "bin"
    pkg_bin_exe = pkg_bin_dir / "atk-dl16.exe"
    build_exe = root_dir / "build" / "Release" / "atk-dl16.exe"
    if not pkg_bin_exe.exists() and build_exe.exists():
        pkg_bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build_exe, pkg_bin_exe)
        print(f"[1/3] Bundled prebuilt CLI binary: {pkg_bin_exe}")
    else:
        print("[1/3] Prebuilt binary check complete.")

    # 2. Install package in editable mode
    print(f"[2/3] Installing atk-dl16-mcp via pip...")
    res = subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(root_dir)])
    if res.returncode != 0:
        print("[Error] pip installation failed.")
        sys.exit(res.returncode)

    # 3. Configure target client
    print(f"[3/3] Configuring target client: {args.client}...")
    from atk_dl16_mcp.__main__ import cmd_install, cmd_config, cmd_status

    cmd_status()

    if args.client != "none":
        ret = cmd_install(args.client)
        if ret != 0:
            print("[Warning] Failed to auto-install config. You can manually copy the config snippet below:")
            cmd_config("codex")
    else:
        print("\nMCP Configuration snippet for manual copy-paste:")
        cmd_config("codex")

    print("\n" + "=" * 60)
    print("  Installation Complete! Restart your AI client to use ATK-DL16.")
    print("=" * 60)


if __name__ == "__main__":
    main()
