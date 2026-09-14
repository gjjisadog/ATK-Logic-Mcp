"""ATK-DL16 MCP server entry point and multi-client configuration helper.

The MCP server itself is client agnostic: every supported client starts the
same Python module and speaks MCP over stdio. This module only owns the
optional convenience CLI used to print or install client-specific
configuration files.

Usage::

    atk-dl16-mcp                             # Run MCP server over stdio
    atk-dl16-mcp run --transport stdio      # Explicit server invocation
    atk-dl16-mcp config --client all        # Print all client formats
    atk-dl16-mcp install --client all       # Configure Claude Code, Codex, Pi
    atk-dl16-mcp status                     # Inspect connected hardware
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .server import _find_cli, logic_status, server


SERVER_NAME = "atk-dl16"
ROOT_DIR = Path(__file__).resolve().parent.parent

# ``claude`` is kept as a compatibility alias, but it now means Claude Code.
# Claude Desktop remains available explicitly as ``claude-desktop``.
CLIENT_ALIASES = {
    "claude": "claude-code",
    "claude_code": "claude-code",
    "claudecode": "claude-code",
    "claude-user": "claude-code-user",
    "claude-code-global": "claude-code-user",
    "claude-code-user": "claude-code-user",
    "desktop": "claude-desktop",
    "claude_desktop": "claude-desktop",
    "pi-user": "pi-global",
    "pi-global": "pi-global",
}
SUPPORTED_CLIENTS = (
    "codex",
    "claude-code",
    "claude-code-user",
    "claude-desktop",
    "pi",
    "pi-global",
    "cursor",
    "all",
)


def normalize_client(client: str) -> str:
    """Return the canonical client name used by the installer."""
    normalized = client.strip().lower().replace("_", "-")
    return CLIENT_ALIASES.get(normalized, normalized)


def _project_root(project_root: Optional[Path] = None) -> Path:
    """Resolve the project whose client-local config should be updated."""
    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    configured_root = os.environ.get("ATK_DL16_PROJECT_ROOT")
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    return Path.cwd().resolve()


def _codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    base_dir = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base_dir / "config.toml"


def _claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "Claude" / "claude_desktop_config.json"
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _pi_agent_dir() -> Path:
    configured_dir = os.environ.get("PI_CODING_AGENT_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser().resolve()
    return Path.home() / ".pi" / "agent"


def get_client_config_path(client: str, project_root: Optional[Path] = None) -> Path:
    """Return the default config path for a single canonical client."""
    normalized = normalize_client(client)
    resolved_project_root = _project_root(project_root)
    paths = {
        "claude-code": resolved_project_root / ".mcp.json",
        "claude-code-user": Path.home() / ".claude.json",
        "claude-desktop": _claude_desktop_config_path(),
        "cursor": Path.home() / ".cursor" / "mcp.json",
        "codex": _codex_config_path(),
        "pi": resolved_project_root / ".pi" / "mcp.json",
        "pi-global": _pi_agent_dir() / "mcp.json",
    }
    try:
        return paths[normalized]
    except KeyError as ex:
        supported = ", ".join(SUPPORTED_CLIENTS)
        raise ValueError(f"Unsupported client '{client}'. Supported: {supported}") from ex


# Kept as a public compatibility map for callers that used the original
# installer module. Dynamic paths are used by get_client_config_path() so a
# caller can change project/environment context after importing this module.
CLIENT_CONFIG_PATHS = {
    "claude-code": _project_root() / ".mcp.json",
    "claude": _project_root() / ".mcp.json",
    "claude-code-user": Path.home() / ".claude.json",
    "claude-desktop": _claude_desktop_config_path(),
    "cursor": Path.home() / ".cursor" / "mcp.json",
    "codex": _codex_config_path(),
    "pi": _project_root() / ".pi" / "mcp.json",
    "pi-global": _pi_agent_dir() / "mcp.json",
}


def _server_command() -> tuple[str, list[str]]:
    """Build a client-independent stdio command for the installed package."""
    executable = str(Path(sys.executable).resolve())
    return executable, ["-m", "atk_dl16_mcp"]


def _runtime_env() -> Optional[Dict[str, str]]:
    """Return optional runtime environment forwarded to spawned MCP servers."""
    configured_data_dir = os.environ.get("ATK_DL16_DATA_DIR")
    if not configured_data_dir:
        return None
    return {
        "ATK_DL16_DATA_DIR": str(Path(configured_data_dir).expanduser().resolve()),
    }


def _stdio_entry() -> Dict[str, Any]:
    executable, args = _server_command()
    # Keep this entry limited to the common stdio fields. In particular, do
    # not emit client-specific fields such as Pi's ``lifecycle`` here.
    entry: Dict[str, Any] = {
        "command": executable,
        "args": args,
    }
    runtime_env = _runtime_env()
    if runtime_env:
        entry["env"] = runtime_env
    return entry


def get_mcp_config_snippet(client: str = "codex") -> Dict[str, Dict[str, Any]]:
    """Return a JSON-shaped MCP server entry for Claude Code, Pi, or Cursor.

    Codex stores the same stdio definition in TOML; use
    :func:`render_codex_config` for its native representation.
    """
    normalized = normalize_client(client)
    if normalized == "all":
        raise ValueError("'all' is only valid for the config/install commands")
    if normalized == "codex":
        entry = _stdio_entry()
    elif normalized == "pi":
        entry = _stdio_entry()
        entry.update({"transport": "stdio", "lifecycle": "eager"})
    elif normalized in {"claude-code", "claude-code-user", "claude-desktop", "cursor"}:
        entry = _stdio_entry()
    elif normalized == "pi-global":
        entry = _stdio_entry()
        entry.update({"transport": "stdio", "lifecycle": "eager"})
    else:
        raise ValueError(f"Unsupported client '{client}'")
    return {SERVER_NAME: entry}


def _json_config(client: str) -> Dict[str, Any]:
    return {"mcpServers": get_mcp_config_snippet(client)}


def _toml_string(value: str) -> str:
    """Encode a string as a TOML basic string without another dependency."""
    # JSON string escaping is compatible with TOML basic strings for the
    # characters used in executable paths and command arguments.
    return json.dumps(value, ensure_ascii=False)


def render_codex_config() -> str:
    """Render the native Codex ``config.toml`` entry for this MCP server."""
    executable, args = _server_command()
    args_text = ", ".join(_toml_string(arg) for arg in args)
    rendered = (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f"command = {_toml_string(executable)}\n"
        f"args = [{args_text}]\n"
    )
    runtime_env = _runtime_env()
    if runtime_env:
        rendered += f"\n[mcp_servers.{SERVER_NAME}.env]\n"
        for key, value in runtime_env.items():
            rendered += f"{key} = {_toml_string(value)}\n"
    return rendered


def _write_text_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def _backup_path(path: Path) -> Path:
    candidate = Path(f"{path}.bak")
    index = 1
    while candidate.exists():
        candidate = Path(f"{path}.bak.{index}")
        index += 1
    return candidate


def _load_json_object(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as config_file:
            data = json.load(config_file)
    except Exception as ex:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        print(f"[Warning] Failed to parse {path}: {ex}. Backed up original to {backup}")
        return {}
    if not isinstance(data, dict):
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        print(f"[Warning] {path} does not contain a JSON object. Backed up original to {backup}")
        return {}
    return data


def _install_json_client(client: str, target_path: Path) -> int:
    existing_data = _load_json_object(target_path)
    servers = existing_data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        existing_data["mcpServers"] = servers
    servers.update(get_mcp_config_snippet(client))
    _write_text_atomically(
        target_path,
        json.dumps(existing_data, indent=2, ensure_ascii=False) + "\n",
    )

    print(f"[OK] Configured {client} MCP server")
    print(f"     Configuration file: {target_path}")
    return 0


def _is_atk_dl16_toml_table(table_name: str) -> bool:
    normalized = table_name.replace('"', "").strip()
    return normalized == f"mcp_servers.{SERVER_NAME}" or normalized.startswith(
        f"mcp_servers.{SERVER_NAME}."
    )


def _find_codex_server_block(text: str) -> Optional[tuple[int, int]]:
    header_pattern = re.compile(
        rf"(?m)^[ \t]*\[mcp_servers\.(?:{re.escape(SERVER_NAME)}|\"{re.escape(SERVER_NAME)}\")]\s*\r?\n"
    )
    match = header_pattern.search(text)
    if not match:
        return None

    end = len(text)
    cursor = match.end()
    table_pattern = re.compile(r"^[ \t]*\[([^\]]+)\]", re.MULTILINE)
    while True:
        next_table = table_pattern.search(text, cursor)
        if not next_table:
            break
        if not _is_atk_dl16_toml_table(next_table.group(1)):
            end = next_table.start()
            break
        cursor = next_table.end()
    return match.start(), end


def _upsert_codex_config(path: Path) -> int:
    if path.exists():
        try:
            existing_text = path.read_text(encoding="utf-8")
        except OSError as ex:
            print(f"[Error] Could not read Codex configuration {path}: {ex}")
            return 1
    else:
        existing_text = ""

    block = render_codex_config()
    server_block = _find_codex_server_block(existing_text)
    if server_block:
        start, end = server_block
        prefix = existing_text[:start]
        suffix = existing_text[end:]
        if suffix and not suffix.startswith("\n"):
            block = block + "\n"
        updated_text = prefix + block + suffix
    else:
        separator = "" if not existing_text or existing_text.endswith(("\n", "\r")) else "\n"
        updated_text = existing_text + separator + block

    try:
        _write_text_atomically(path, updated_text)
    except OSError as ex:
        print(f"[Error] Could not write Codex configuration {path}: {ex}")
        return 1

    print("[OK] Configured codex MCP server")
    print(f"     Configuration file: {path}")
    return 0


def _print_client_config(client: str, project_root: Optional[Path] = None) -> None:
    normalized = normalize_client(client)
    if normalized == "codex":
        print(f"# Add to {get_client_config_path('codex', project_root)}")
        print(render_codex_config(), end="")
        return
    print(json.dumps(_json_config(normalized), indent=2, ensure_ascii=False))


def cmd_config(
    client: str = "codex",
    output_format: str = "auto",
    project_root: Optional[Path] = None,
) -> int:
    """Print a native configuration snippet without changing any files."""
    normalized = normalize_client(client)
    if normalized == "all":
        if output_format not in {"auto", "json"}:
            print("[Error] --format toml is only valid for --client codex")
            return 1
        resolved_project_root = _project_root(project_root)
        print(f"# Claude Code: {resolved_project_root / '.mcp.json'}")
        print(json.dumps(_json_config("claude-code"), indent=2, ensure_ascii=False))
        print(f"\n# Codex: {get_client_config_path('codex', project_root)}")
        print(render_codex_config(), end="")
        print(f"\n# Pi: {resolved_project_root / '.pi' / 'mcp.json'}")
        print(json.dumps(_json_config("pi"), indent=2, ensure_ascii=False))
        return 0

    if normalized == "codex":
        if output_format == "json":
            print(json.dumps(_json_config("codex"), indent=2, ensure_ascii=False))
        else:
            print(f"# Add to {get_client_config_path('codex', project_root)}")
            print(render_codex_config(), end="")
        return 0

    if output_format == "toml":
        print("[Error] --format toml is only valid for --client codex")
        return 1
    _print_client_config(normalized, project_root)
    return 0


def cmd_install(client: str = "codex", project_root: Optional[Path] = None) -> int:
    """Install this MCP server into one client or all supported clients."""
    normalized = normalize_client(client)
    if normalized == "all":
        results = [
            cmd_install(single_client, project_root)
            for single_client in ("claude-code", "codex", "pi")
        ]
        print("[Info] Pi requires the MCP extension once: pi install npm:pi-mcp-extension")
        return 0 if all(result == 0 for result in results) else 1

    if normalized == "codex":
        return _upsert_codex_config(get_client_config_path(normalized, project_root))

    if normalized not in {
        "claude-code",
        "claude-code-user",
        "claude-desktop",
        "pi",
        "pi-global",
        "cursor",
    }:
        supported = ", ".join(SUPPORTED_CLIENTS)
        print(f"[Error] Unsupported client '{client}'. Supported: {supported}")
        _print_client_config("claude-code", project_root)
        return 1

    try:
        result = _install_json_client(
            normalized,
            get_client_config_path(normalized, project_root),
        )
    except OSError as ex:
        print(f"[Error] Could not configure {normalized}: {ex}")
        return 1

    if normalized in {"pi", "pi-global"} and result == 0:
        print("[Info] Pi requires the MCP extension once: pi install npm:pi-mcp-extension")
    return result


def cmd_status() -> int:
    """Check and display hardware connection status."""
    cli = _find_cli()
    print(f"ATK-DL16 CLI Binary : {cli or 'NOT FOUND'}")
    print(f"Python Executable   : {sys.executable}")
    status = logic_status()
    print("Hardware Status     :")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0 if status.get("connected") else 1


def main() -> None:
    if len(sys.argv) == 1:
        # Standard MCP invocation by Claude Code, Codex, Pi, or any other MCP
        # client. Do not print anything here: stdout is the JSON-RPC channel.
        server.run(transport="stdio")
        return

    parser = argparse.ArgumentParser(
        prog="atk-dl16-mcp",
        description="ATK-DL16 Logic Analyzer MCP Server & multi-client setup CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    sub_run = subparsers.add_parser("run", help="Run MCP server")
    sub_run.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport (stdio is the portable client default)",
    )

    sub_cfg = subparsers.add_parser("config", help="Generate a native MCP configuration snippet")
    sub_cfg.add_argument(
        "--client",
        default="codex",
        choices=[*SUPPORTED_CLIENTS, "claude", "claude_code", "claude-desktop"],
        help="Target client (all prints Claude Code, Codex, and Pi formats)",
    )
    sub_cfg.add_argument(
        "--format",
        default="auto",
        choices=["auto", "json", "toml"],
        help="Output format; auto selects the client's native format",
    )
    sub_cfg.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root for project-scoped Claude Code and Pi configuration",
    )

    sub_inst = subparsers.add_parser("install", help="Install MCP configuration into a client")
    sub_inst.add_argument(
        "--client",
        default="codex",
        choices=[*SUPPORTED_CLIENTS, "claude", "claude_code", "claude-desktop"],
        help="Target client; use all for Claude Code, Codex, and Pi",
    )
    sub_inst.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root for project-scoped Claude Code and Pi configuration",
    )

    subparsers.add_parser("status", help="Inspect connected hardware status")

    args = parser.parse_args()
    if args.command in (None, "run"):
        server.run(transport=getattr(args, "transport", "stdio"))
    elif args.command == "config":
        sys.exit(cmd_config(args.client, args.format, args.project_root))
    elif args.command == "install":
        sys.exit(cmd_install(args.client, args.project_root))
    elif args.command == "status":
        sys.exit(cmd_status())


if __name__ == "__main__":
    main()
