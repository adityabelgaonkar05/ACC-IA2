"""Validate a trusted local API project and make a secret-filtered Docker build context."""
import json
from pathlib import Path
import shlex
import shutil

EXCLUDED = {'.git', '.aws', '.ssh', '.cloudrunner', '.venv', 'venv', 'node_modules',
            '__pycache__', '.terraform', 'dist', 'build', '.pytest_cache'}


def validate(spec):
    try:
        root = Path(spec['path']).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("Project directory is missing or unreadable") from exc
    if any(part in {'.ssh','.aws','.git','.cloudrunner'} for part in root.parts):
        raise ValueError('Choose an API source directory, not a credentials or metadata directory')
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home():
        raise ValueError('Choose a specific API project directory, not your home or filesystem root')
    command = shlex.split(spec['command'])
    if not command or any('\x00' in part for part in command):
        raise ValueError('Provide a valid start command, such as python -m uvicorn main:app --host 0.0.0.0 --port 8080')
    if not spec['health_path'].startswith('/') or any(c in spec['health_path'] for c in ['?', '#', '\n', '\r']):
        raise ValueError('Health path must be an absolute path without query or fragment')
    if not spec['load_path'].startswith('/') or '://' in spec['load_path'] or spec['load_path'].startswith('//'):
        raise ValueError('Load test path must be local to this API')
    return {**spec, 'path': str(root), 'argv': command, 'kind': 'api'}


def snapshot(spec, destination):
    root = Path(spec['path'])
    def ignore(directory, names):
        return [name for name in names if name in EXCLUDED or name.startswith('.env')
                or name.endswith(('.pem', '.key', '.tfstate', '.tfstate.backup'))
                or (Path(directory) / name).is_symlink()]
    shutil.copytree(root, destination, ignore=ignore)
    # User code is executed only by Docker builds/containers, never by the controller.
    if spec['runtime'] == 'dockerfile':
        if not (destination / 'Dockerfile').is_file():
            raise ValueError('Dockerfile runtime requires a Dockerfile in the project directory')
        return 'Dockerfile'
    if spec['runtime'] == 'python':
        text = ('FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n'
                'RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi\n')
    else:
        text = ('FROM node:22-slim\nWORKDIR /app\nCOPY . .\n'
                'RUN if [ -f package-lock.json ]; then npm ci --omit=dev; '
                'elif [ -f package.json ]; then npm install --omit=dev; fi\n')
    text += 'ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1\nUSER 10001:10001\n'
    text += 'CMD ' + json.dumps(spec['argv']) + '\n'
    name = 'Dockerfile.cloudrunner'
    (destination / name).write_text(text)
    return name
