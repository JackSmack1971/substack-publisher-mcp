#!/usr/bin/env python3
"""Advisory offline audit of a ZIP-backed repository; does not enforce Codex actions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file() or '.git' in path.relative_to(root).parts or '__pycache__' in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

def git_state(root):
    if not (root / '.git').exists():
        return {'available': False, 'reason': 'Archive has no .git metadata'}
    args = [['git','status','--porcelain=v1','-uall'], ['git','diff','--name-status'], ['git','diff','--cached','--name-status'], ['git','rev-parse','HEAD']]
    result = {'available': True}
    for cmd in args:
        run = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
        result[' '.join(cmd)] = {'exit_code':run.returncode, 'stdout':run.stdout}
    return result

def compare(before, after, allowed):
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    unexpected = sorted(path for path in changed if path not in allowed)
    return {'changed':changed, 'outside_expected_scope':unexpected}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--baseline', type=Path, help='SHA256 inventory JSON file')
    parser.add_argument('--expected', type=Path, help='JSON array of allowed changed paths')
    parser.add_argument('--output', type=Path, help='write JSON report (default stdout)')
    args = parser.parse_args()
    root = args.root.resolve()
    state = {'inventory':inventory(root), 'git':git_state(root)}
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        allowed = set(json.loads(args.expected.read_text())) if args.expected else set()
        state['scope'] = compare(baseline, state['inventory'], allowed)
    serialized = json.dumps(state, sort_keys=True, indent=2) + '\n'
    if args.output: args.output.write_text(serialized)
    else: print(serialized, end='')
    if 'scope' in state and state['scope']['outside_expected_scope']: return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
