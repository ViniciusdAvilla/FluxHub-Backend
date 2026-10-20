"""Compatibilidade com deployments que usam `uvicorn main:app`.

A única instância da aplicação e seu middleware de segurança estão em app.main.
"""

from app.main import app

__all__ = ["app"]
