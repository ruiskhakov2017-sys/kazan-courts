"""Targeted leak audit of generated local credentials. Never prints their values.

This is not a full historical or third-party credential audit.
"""
import argparse
import re
from pathlib import Path
import subprocess
import tarfile
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--temporary-secrets-only-image', action='append', default=[],
                    help='For an unmodified upstream image, scan all generated secrets; do not claim a generic fixture/key audit')
parser.add_argument('--env-file', type=Path)
parser.add_argument('--logs', type=Path, required=True)
parser.add_argument('--image', action='append', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
names = {'POSTGRES_PASSWORD', 'DEV_DB_PASSWORD', 'TEST_DB_PASSWORD', 'DJANGO_SECRET_KEY'}
values = [line.split('=', 1)[1].encode() for line in (args.env_file or root / '.env').read_text().splitlines()
          if line.partition('=')[0] in names]
assert len(values) == 4 and all(len(value) >= 32 for value in values)


# Token boundaries avoid substrings inside libunistring Unicode name tables.
patterns = [rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])',
            rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']


def clean(data, location, signatures=True):
    if signatures and any(re.search(pattern, data) for pattern in patterns):
        raise SystemExit(f'FAIL credential signature detected in {location}; value withheld')
    if any(value in data for value in values):
        raise SystemExit(f'FAIL local credential detected in {location}; value withheld')


paths = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=root).split(b'\0')
for item in paths:
    if item:
        path = root / item.decode()
        if path.is_file():
            clean(path.read_bytes(), str(path.relative_to(root)))
print('PASS current tracked/untracked nonignored repository files')
clean(subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=root), 'diff')
for path in args.logs.glob('*.log'):
    clean(path.read_bytes(), f'log {path.name}')
print('PASS diff and captured logs')
seen = set()
for image in args.image:
    identity = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image]).strip()
    if identity in seen:
        continue
    seen.add(identity)
    signatures = image not in args.temporary_secrets_only_image
    clean(subprocess.check_output(['docker', 'image', 'inspect', image]), 'image config', signatures)
    clean(subprocess.check_output(['docker', 'history', '--no-trunc', image]), 'image history', signatures)
    with tempfile.TemporaryFile() as archive:
        subprocess.run(['docker', 'save', image], stdout=archive, check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode='r|*') as saved:
            for member in saved:
                if not member.isfile():
                    continue
                source = saved.extractfile(member)
                tail = b''
                while block := source.read(1024 * 1024):
                    clean(tail + block, 'saved image layer/config', signatures)
                    tail = block[-max(map(len, values)):]
    scope = 'generated temporary credentials only' if not signatures else 'generated credentials and common signatures'
    print(f'PASS image config, history and all saved layers: {image}; scope={scope}')
print('Scope: four generated local credentials; .env excluded intentionally; historical secrets not certified.')
