import json
import sys
from pathlib import Path

import pytest
import yaml

from atk_dl16_mcp.__main__ import (
    _install_json_client,
    _upsert_codex_config,
    cmd_config,
    cmd_install,
    get_client_config_path,
    get_mcp_config_snippet,
    normalize_client,
    render_codex_config,
)


def test_client_aliases_and_project_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert normalize_client("claude") == "claude-code"
    assert normalize_client("claude_code") == "claude-code"
    assert get_client_config_path("claude-code") == tmp_path / ".mcp.json"
    assert get_client_config_path("pi") == tmp_path / ".pi" / "mcp.json"
    assert get_client_config_path("claude-code-user").name == ".claude.json"
    assert get_client_config_path("pi-global").parts[-3:] == (".pi", "agent", "mcp.json")


def test_json_snippets_use_standard_stdio_and_pi_extensions():
    claude_entry = get_mcp_config_snippet("claude-code")["atk-dl16"]
    assert claude_entry["command"] == str(Path(sys.executable).absolute())
    assert claude_entry["args"] == ["-m", "atk_dl16_mcp"]
    assert "transport" not in claude_entry

    pi_entry = get_mcp_config_snippet("pi")["atk-dl16"]
    assert pi_entry["transport"] == "stdio"
    assert pi_entry["lifecycle"] == "eager"


def test_runtime_data_directory_is_forwarded_to_all_client_formats(monkeypatch, tmp_path):
    data_dir = tmp_path / "portable-data"
    monkeypatch.setenv("ATK_DL16_DATA_DIR", str(data_dir))

    claude_entry = get_mcp_config_snippet("claude-code-user")["atk-dl16"]
    assert claude_entry["env"] == {"ATK_DL16_DATA_DIR": str(data_dir.resolve())}

    pi_entry = get_mcp_config_snippet("pi-global")["atk-dl16"]
    assert pi_entry["env"] == {"ATK_DL16_DATA_DIR": str(data_dir.resolve())}

    rendered = render_codex_config()
    assert "[mcp_servers.atk-dl16.env]" in rendered
    assert f"ATK_DL16_DATA_DIR = {json.dumps(str(data_dir.resolve()))}" in rendered


def test_codex_config_is_valid_toml_and_is_idempotent(tmp_path):
    toml = pytest.importorskip("tomllib")
    config_path = tmp_path / "config.toml"
    config_path.write_text('[profiles.default]\nmodel = "test"\n', encoding="utf-8")

    assert _upsert_codex_config(config_path) == 0
    assert _upsert_codex_config(config_path) == 0

    parsed = toml.loads(config_path.read_text(encoding="utf-8"))
    server_config = parsed["mcp_servers"]["atk-dl16"]
    assert server_config["command"] == str(Path(sys.executable).absolute())
    assert server_config["args"] == ["-m", "atk_dl16_mcp"]
    assert config_path.read_text(encoding="utf-8").count("[mcp_servers.atk-dl16]") == 1


def test_json_install_preserves_existing_servers(tmp_path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "existing"}}}),
        encoding="utf-8",
    )

    assert _install_json_client("claude-code", config_path) == 0
    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["mcpServers"]["existing"] == {"command": "existing"}
    assert parsed["mcpServers"]["atk-dl16"]["args"] == ["-m", "atk_dl16_mcp"]


def test_codex_config_render_has_native_table():
    rendered = render_codex_config()
    assert rendered.startswith("[mcp_servers.atk-dl16]\n")
    assert 'args = ["-m", "atk_dl16_mcp"]' in rendered


def test_install_all_targets_are_project_scoped_except_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))

    assert cmd_install("all", project_root=tmp_path) == 0
    assert (tmp_path / ".mcp.json").exists()
    assert (tmp_path / ".pi" / "mcp.json").exists()
    assert (tmp_path / "codex-home" / "config.toml").exists()
    assert not (tmp_path / "dsh-home").exists()


def test_dsh_config_is_a_standalone_stdio_overlay(monkeypatch, tmp_path, capsys):
    data_dir = tmp_path / "portable data"
    monkeypatch.setenv("ATK_DL16_DATA_DIR", str(data_dir))

    assert cmd_config("dsh") == 0
    assert yaml.safe_load(capsys.readouterr().out) == [{"insert": [{
        "id": "mcp-atk-dl16",
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": {
            "serverName": "atk-dl16",
            "transport": "stdio",
            "command": str(Path(sys.executable).absolute()),
            "args": ["-m", "atk_dl16_mcp"],
            "env": {"ATK_DL16_DATA_DIR": str(data_dir.resolve())},
        },
    }]}]


@pytest.mark.parametrize("existing", [
    "",
    "# Keep the user's Cordis expression\n- set:\n    example: !!js process.cwd()\n",
    "[ ] # Existing empty flow sequence\n",
])
def test_dsh_install_preserves_patches_and_reuses_its_entry(tmp_path, monkeypatch, existing):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    path = get_client_config_path("dsh")
    assert path == tmp_path / "cordis.patch.yml"
    if existing:
        path.write_text(existing, encoding="utf-8")
    first_data_dir = tmp_path / "data"
    monkeypatch.setenv("ATK_DL16_DATA_DIR", str(first_data_dir))

    assert cmd_install("dsh") == 0
    first_install = path.read_text(encoding="utf-8")
    assert cmd_install("dsh") == 0
    assert path.read_text(encoding="utf-8") == first_install

    updated_data_dir = tmp_path / "updated data"
    monkeypatch.setenv("ATK_DL16_DATA_DIR", str(updated_data_dir))
    assert cmd_install("dsh") == 0
    updated = path.read_text(encoding="utf-8")
    assert updated.count("mcp-atk-dl16") == 1
    if existing.startswith("#"):
        assert updated.startswith(existing)
    before = yaml.load(existing, Loader=yaml.BaseLoader) or []
    after = yaml.load(updated, Loader=yaml.BaseLoader)
    server = after.pop()["insert"][0]["config"]
    assert after == before
    assert server["env"] == {"ATK_DL16_DATA_DIR": str(updated_data_dir.resolve())}


@pytest.mark.parametrize("existing", [
    "broken: [",
    "insert: []\n",
    "- insert:\n  - id: mcp-atk-dl16\n    name: '@deepseek-ai/dsh-mcp-client'\n",
])
def test_dsh_install_leaves_invalid_or_conflicting_configs_intact(tmp_path, monkeypatch, existing):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    path = tmp_path / "cordis.patch.yml"
    path.write_text(existing, encoding="utf-8")

    assert cmd_install("dsh") == 1
    assert path.read_text(encoding="utf-8") == existing
