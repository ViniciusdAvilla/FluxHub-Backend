"""Optional idempotent MCP schema bootstrap (no Mercado Livre tables modified)."""
from app.database.mcp_schema import ensure_mcp_schema


if __name__ == "__main__":
    ensure_mcp_schema()
    print("MCP schema ready")
