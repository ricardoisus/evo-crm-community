import sys
from .app import secret
from .store import Store

if sys.argv[1:] == ["migrate"]:
    Store(secret("DATABASE_URL")).migrate()
else:
    raise SystemExit("Usage: python -m vetorial_agent migrate")
