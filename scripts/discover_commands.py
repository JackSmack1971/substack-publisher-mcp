"""Report build/lint/typecheck/test/generate/install commands that a repository itself declares.

Reads package.json scripts, Makefile targets and GitHub Actions `run:` steps. Never executes anything, never
supplies a default command: a category with no declared command stays empty. Category is a name/keyword heuristic.
`--cache FILE` stores results with sha256 fingerprints of every candidate config file; any change, addition or
removal of one invalidates the cache.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MAX_BYTES = 1_000_000
FIXED = ('package.json', 'Makefile', 'GNUmakefile', 'makefile')
LOCKFILES = {'package-lock.json': 'npm', 'pnpm-lock.yaml': 'pnpm', 'yarn.lock': 'yarn', 'bun.lock': 'bun', 'bun.lockb': 'bun'}
CATEGORIES = (  # first match wins; typecheck before test/build
    ('typecheck', r'typecheck|type-check|\btsc\b|mypy|pyright'),
    ('lint', r'lint|eslint|ruff|flake8|pylint|clippy'),
    ('test', r'\btests?\b|pytest|unittest|jest|vitest|mocha|go test|cargo test'),
    ('generate', r'generat|codegen|\bgen\b|protoc'),
    ('build', r'\bbuild\b|compile|\bbundle\b'),
    ('install', r'\binstall\b|npm ci|pip install|poetry install|bundle install'),
)

def candidates(root):
    found = {root / n for n in (*FIXED, *LOCKFILES) if (root / n).is_file() and not (root / n).is_symlink()}
    wf = root / '.github' / 'workflows'
    if wf.is_dir():
        found |= {p for p in wf.iterdir() if p.suffix in ('.yml', '.yaml') and p.is_file() and not p.is_symlink()}
    return sorted(found)

def fingerprint(root, files):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

def category(text):
    for name, pattern in CATEGORIES:
        if re.search(pattern, text, re.I): return name
    return 'other'

def entry(source, line, name, command, text):
    # The author's own script/target name outranks keywords in the command body.
    cat = category(name) if name else 'other'
    if cat == 'other': cat = category(text)
    return {'category': cat, 'name': name, 'command': command, 'source': source, 'line': line}

def read(path):
    return path.read_bytes()[:MAX_BYTES].decode('utf-8', 'replace')

def package_manager(root, pkg):
    declared = str(pkg.get('packageManager', '')).split('@')[0]
    if declared: return declared, 'package.json packageManager'
    for lock, name in LOCKFILES.items():
        if (root / lock).is_file(): return name, lock
    return None, None

def from_package_json(root, path, out):
    text = read(path)
    try:
        pkg = json.loads(text)
    except ValueError:
        out['warnings'].append('package.json: invalid JSON; scripts not read')
        return
    pm, evidence = package_manager(root, pkg)
    if pm: out['package_managers'].append({'name': pm, 'evidence': evidence})
    lines = text.splitlines()
    for name, body in (pkg.get('scripts') or {}).items():
        if not isinstance(body, str): continue
        line = next((i for i, l in enumerate(lines, 1) if f'"{name}"' in l), None)
        # Without a determined package manager the runner is unknown; report the script body, not a guessed command.
        e = entry('package.json', line, name, f'{pm} run {name}' if pm else None, body)
        e['script'] = body
        out['commands'].append(e)

def from_makefile(path, out):
    for i, l in enumerate(read(path).splitlines(), 1):
        m = re.match(r'([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?!=)', l)
        if m: out['commands'].append(entry(path.name, i, m.group(1), f'make {m.group(1)}', m.group(1)))

def from_workflow(root, path, out):
    rel, lines, i = path.relative_to(root).as_posix(), read(path).splitlines(), 0
    while i < len(lines):
        m = re.match(r'(\s*)(?:-\s+)?run:\s*(.*)$', lines[i])
        i += 1
        if not m: continue
        indent, rest = len(m.group(1)), m.group(2).strip()
        if rest[:1] in ('|', '>'):
            start = i
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent): i += 1
            for k, l in enumerate(lines[start:i]):
                if l.strip(): out['commands'].append(entry(rel, start + 1 + k, None, l.strip(), l))
        elif rest:
            out['commands'].append(entry(rel, i, None, rest, rest))

def discover(root):
    files = candidates(root)
    out = {'root': str(root), 'fingerprint': fingerprint(root, files), 'commands': [], 'package_managers': [], 'warnings': []}
    for p in files:
        if p.name == 'package.json': from_package_json(root, p, out)
        elif p.name.lower() in ('makefile', 'gnumakefile'): from_makefile(p, out)
        elif p.parent.name == 'workflows': from_workflow(root, p, out)
    if not out['commands']:
        out['warnings'].append('no declared commands found; none are assumed')
    return out

def load_cache(cache, root):
    """Return (result, reason). reason names the first change that invalidated the cache, or None on a hit."""
    try:
        old = json.loads(cache.read_text(encoding='utf-8'))
        recorded = old['fingerprint']
        if not isinstance(recorded, dict) or old['root'] != str(root): return None, 'cache unusable'
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'no usable cache'
    current = fingerprint(root, candidates(root))
    for path in sorted(recorded.keys() | current.keys()):
        if recorded.get(path) != current.get(path): return None, f'changed: {path}'
    return old, None

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('root', nargs='?', default='.')
    ap.add_argument('--cache', help='JSON cache file to read and (on miss) write; no cache is used without this flag')
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    result, status = None, 'cache: not used'
    if args.cache:
        result, reason = load_cache(Path(args.cache), root)
        status = 'cache: hit' if result else f'cache: miss ({reason})'
    if result is None:
        result = discover(root)
        if args.cache: Path(args.cache).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(status, file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
