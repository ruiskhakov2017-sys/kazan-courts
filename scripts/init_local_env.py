"""Create private, unique credentials for a NEW local Compose database."""
import argparse
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path)
args = parser.parse_args()

root = Path(__file__).resolve().parent.parent
text = (root / '.env.example').read_text(encoding='utf-8')
for name in ('POSTGRES_PASSWORD', 'DEV_DB_PASSWORD', 'TEST_DB_PASSWORD', 'DJANGO_SECRET_KEY'):
    text = text.replace(f'{name}=GENERATE_LOCALLY', f'{name}={secrets.token_urlsafe(48)}')
# O_EXCL prevents replacing credentials for an existing volume.
fd = os.open(args.output or root / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as output:
    output.write(text)
print('Created private .env for local development; values are not printed.')
