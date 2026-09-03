import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from atk_dl16_mcp.server import server

if __name__ == "__main__":
    server.run(transport="stdio")
