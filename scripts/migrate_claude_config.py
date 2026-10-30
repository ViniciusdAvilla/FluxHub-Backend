"""Create the optional encrypted Claude configuration table without touching users or OAuth."""
from app.database.claude_provider_schema import ensure_claude_provider_schema


if __name__ == "__main__":
    ensure_claude_provider_schema()
    print("Tabela claude_provider_config pronta; nenhum dado existente removido.")
