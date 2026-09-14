# ATK-DL16 MCP Windows x64 离网安装包

这个目录是一个可复制、可离网安装的完整运行包。它已经内置：

- Windows x64 embeddable Python 3.12 运行时（当前发布包为 3.12.10）；
- `numpy`、`mcp`、`pyyaml` 及其运行依赖；
- 静态链接的 `atk-dl16.exe`；
- Pi 的 `pi-mcp-extension` 及其 npm 运行依赖。

目标电脑安装时不会运行 `pip`、`npm`，也不会访问互联网。Claude Code、Codex 或 Pi 客户端本身需要预先安装；客户端软件不包含在本包中。

## 一键安装

1. 解压整个 zip，保持目录结构不变。
2. 双击 `install-offline.cmd`。
3. 安装器默认写入当前 Windows 用户的全局配置：Claude Code、Codex、Pi。
4. 重启对应客户端。

安装位置默认为 `%LOCALAPPDATA%\ATK-DL16-MCP`，采集数据默认为 `%LOCALAPPDATA%\ATK-DL16-MCP\data`。配置文件会保留其它 MCP 服务；已有的 Pi 无效 JSON 会先备份为 `.bak`。

## 项目级配置

如需只对某个工程启用 Claude Code 和 Pi：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-offline.ps1 `
  -Client all `
  -ProjectRoot "D:\work\my-project"
```

这会写入工程的 `.mcp.json` 与 `.pi\mcp.json`，同时仍更新 Codex 全局配置。

## 验证

```powershell
.\atk-dl16-mcp.cmd status
```

没有连接 ATK-DL16 时，`status` 报告设备未连接是正常现象；MCP 配置和离线运行时仍然有效。`manifest.json` 和发布目录中的 `SHA256SUMS.txt` 可用于校验文件完整性。
