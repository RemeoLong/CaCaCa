"""One-time, non-destructive local preview migration. Refuses a populated target."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
source_env = os.environ.copy()
source_env.update(DJANGO_DEBUG='true', CACACA_LOCAL_ENV='false', DATABASE_URL='')
target_env = os.environ.copy()
target_env.pop('DATABASE_URL', None)
target_env.update(DJANGO_DEBUG='true', CACACA_LOCAL_ENV='true')
artifacts = root / 'artifacts'
artifacts.mkdir(exist_ok=True)

def run(args, env):
    subprocess.run([sys.executable, 'manage.py', *args], env=env, check=True)

run(['migrate'], target_env)
check = 'from django.contrib.auth import get_user_model; from catalog.models import Product; assert not get_user_model().objects.exists() and not Product.objects.exists(), "Target contains data; migration cancelled"'
run(['shell', '-c', check], target_env)
shutil.copy2(root / 'db.sqlite3', artifacts / 'pre-postgres.sqlite3')
run(['dumpdata', '--exclude', 'contenttypes', '--exclude', 'auth.permission', '--natural-foreign', '--natural-primary',
     '--output', str(artifacts / 'local-data.json')], source_env)
run(['loaddata', str(artifacts / 'local-data.json')], target_env)
print('Local records migrated to PostgreSQL. SQLite source and private local backup retained.')
