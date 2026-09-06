"""
ATK-DL16 MCP Server Entry Point & Multi-Client Installer.

Usage:
  atk-dl16-mcp                          # Run MCP server on stdio (for AI clients)
  atk-dl16-mcp config [--client <name>] # Print MCP JSON config for Codex/Claude/Cursor
  atk-dl16-mcp install [--client <name>]# One-click install config into client settings
  atk-dl16-mcp status                   # Inspect connected ATK-DL16 hardware status
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .server import server, logic_status, _find_cli


CLIENT_CONFIG_PATHS = {
    "claude": Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json" if sys.platform == "win32" else Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    "cursor": Path.home() / ".cursor" / "mcp.json",
    "codex": Path.home() / ".codex" / "config.json",
}


def get_mcp_config_snippet(client: str = "codex") -> dict:
    """Generate the standard MCP configuration snippet for this installation."""
    python_exe = sys.executable
    return {
        "atk-dl16": {
            "command": python_exe,
            "args": ["-m", "atk_dl16_mcp"],
            "description": "ATK-DL16 / DL16 Plus Headless Logic Analyzer & Analysis MCP Server"
        }
    }


def cmd_config(client: str):
    """Print MCP config snippet."""
    cfg = {"mcpServers": get_mcp_config_snippet(client)}
    print(json.dumps(cfg, indent=2, ensure_ascii=False))


def cmd_install(client: str):
    """One-click inject MCP configuration into target client configuration file."""
    target_path = CLIENT_CONFIG_PATHS.get(client.lower())
    snippet = get_mcp_config_snippet(client)

    if not target_path:
        print(f"[Error] Unsupported auto-install client '{client}'. Supported: {list(CLIENT_CONFIG_PATHS.keys())}")
        print("You can manually copy this snippet into your client's MCP configuration:")
        print(json.dumps({"mcpServers": snippet}, indent=2))
        return 1

    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing_data: dict = {}
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception as ex:
            print(f"[Warning] Failed to parse existing {target_path}: {ex}. Will backup to .bak")
            target_path.rename(target_path.with_suffix(".json.bak"))
            existing_data = {}

    if "mcpServers" not in existing_data or not isinstance(existing_data["mcpServers"], dict):
        existing_data["mcpServers"] = {}

    existing_data["mcpServers"]["atk-dl16"] = snippet["atk-dl16"]

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Successfully installed ATK-DL16 MCP configuration to {client}!")
    print(f"     Configuration file: {target_path}")
    print("     Configuration content:")
    print(json.dumps({"atk-dl16": snippet["atk-dl16"]}, indent=2))
    return 0


def cmd_status():
    """Check and display hardware connection status."""
    cli = _find_cli()
    print(f"ATK-DL16 CLI Binary : {cli or 'NOT FOUND'}")
    print(f"Python Executable   : {sys.executable}")
    status = logic_status()
    print("Hardware Status     :")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0 if status.get("connected") else 1


def main():
    if len(sys.argv) == 1:
        # Standard MCP invocation by client via stdio
        server.run(transport="stdio")
        return

    parser = argparse.ArgumentParser(
        prog="atk-dl16-mcp",
        description="ATK-DL16 Logic Analyzer MCP Server & Management CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # Run command
    sub_run = subparsers.add_parser("run", help="Run MCP server (default)")
    sub_run.add_argument("--transport", default="stdio", choices=["stdio", "sse"], help="MCP transport")

    # Config command
    sub_cfg = subparsers.add_parser("config", help="Generate MCP configuration snippet")
    sub_cfg.add_argument("--client", default="codex", choices=["codex", "claude", "cursor"], help="Target client")

    # Install command
    sub_inst = subparsers.add_parser("install", help="One-click install MCP server into client configuration")
    sub_inst.add_argument("--client", default="codex", choices=["codex", "claude", "cursor"], help="Target client")

    # Status command
    subparsers.add_parser("status", help="Inspect connected hardware status")

    args = parser.parse_args()

    if args.command in (None, "run"):
        transport = getattr(args, "transport", "stdio")
        server.run(transport=transport)
    elif args.command == "config":
        cmd_config(args.client)
    elif args.command == "install":
        sys.exit(cmd_install(args.client))
    elif args.command == "status":
        sys.exit(cmd_status())


if __name__ == "__main__":
    main()
