"""Create local-only database credentials, without printing secrets."""
import secrets
from pathlib import Path

path = Path(__file__).resolve().parent.parent / '.env.local'
if not path.exists():
    password = secrets.token_urlsafe(32)
    path.write_text(
        f'POSTGRES_PASSWORD={password}\n'
        f'DATABASE_URL=postgresql://cacaca:{password}@127.0.0.1:55432/cacaca\n'
        'DJANGO_DEBUG=true\n', encoding='utf-8')
    print('Created ignored .env.local with local database credentials.')
else:
    print('Existing .env.local preserved.')
