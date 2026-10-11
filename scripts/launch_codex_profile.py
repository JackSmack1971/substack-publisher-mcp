#!/usr/bin/env python3
"""Fail-closed Codex profile launcher (CP-008A). Validates profile selection before `codex` starts.

Contract (DOCUMENTED + OBSERVED on codex-cli 0.162.1, see docs/architecture/consequence-tier-profiles.md):
a profile is `$CODEX_HOME/<name>.config.toml`; a missing profile is NOT an error in Codex (silent fallback to
the base config); a trusted project `.codex/config.toml` and CLI flags outrank the profile. This launcher refuses
what Codex tolerates. It never creates, edits or installs a profile, never changes trust, and starts no model
session itself. EXPECTED values come from parsing TOML; CONFIRMED values come from `codex debug prompt-input`
(renders the prompt, no model call). Neither proves the sandbox actually blocks anything. Residual bypass paths
are listed in docs/architecture/consequence-tier-profiles.md.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_control_plane import repository_trust, validate_codex_config

NAME_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\Z')
SANDBOX_RANK = {'read-only': 0, 'workspace-write': 1, 'danger-full-access': 2}
APPROVAL_RANK = {'never': 0, 'on-request': 1}  # anything else (granular tables, unknown) ranks above: weaker/unknown
UNKNOWN_APPROVAL_RANK = 2
# Keys that can widen access in ways this launcher cannot rank. A higher layer setting them fails closed.
OPAQUE_KEYS = ('sandbox_workspace_write', 'default_permissions', 'permissions', 'approvals_reviewer', 'mcp_servers', 'hooks', 'profile', 'profiles')
BYPASS_FLAGS = ('--dangerously-bypass-approvals-and-sandbox', '--yolo', '--approve-for-me', '--add-dir', '--profile', '-p', '--remote')
WARN_FLAGS = ('--search', '--oss', '--enable', '--disable')

def approval_rank(value):
    return APPROVAL_RANK.get(value, UNKNOWN_APPROVAL_RANK) if isinstance(value, str) else UNKNOWN_APPROVAL_RANK

def default_codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')

def check_name(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return [f'invalid profile name {name!r}: use 1-64 letters, digits, `_` or `-`, not starting with `-`']
    return []

def load_toml(path, label, errors):
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        errors.append(f'{label}: unreadable or malformed TOML ({exc})')
        return None

def locate_profile(name, home, errors):
    """Return the profile path, or None with errors. Rejects missing, non-regular, symlinked and ambiguous profiles."""
    if not home.is_dir():
        errors.append(f'CODEX_HOME is not a directory: {home}')
        return None
    suffix = '.config.toml'
    matches = [p for p in home.iterdir() if p.name.lower() == (name + suffix).lower()]
    if not matches:
        errors.append(f'profile `{name}` not found: expected {home / (name + suffix)} (Codex itself would silently fall back to the base config)')
        return None
    if len(matches) > 1 or matches[0].name != name + suffix:
        errors.append(f'profile `{name}` is ambiguous: {sorted(p.name for p in matches)}')
        return None
    path = matches[0]
    if path.is_symlink() or not path.is_file() or path.resolve().parent != home.resolve():
        errors.append(f'profile `{name}` must be a regular file directly inside CODEX_HOME (no symlinks)')
        return None
    return path

def project_layers(cwd):
    """(project root, existing `.codex/config.toml` files from the Git root down to cwd)."""
    cwd = cwd.resolve()
    root = next((d for d in (cwd, *cwd.parents) if (d / '.git').exists()), cwd)
    chain = [d for d in (cwd, *cwd.parents) if d == root or root in d.parents]
    return root, [d / '.codex' / 'config.toml' for d in reversed(chain) if (d / '.codex' / 'config.toml').is_file()]

def parse_cli(args):
    """Extract security-relevant CLI overrides. Returns (overrides, cwd_override, errors, warnings)."""
    overrides, errors, warnings, cwd, i = {}, [], [], None, 0
    args = list(args)
    def value(flag):
        nonlocal i
        if '=' in args[i] and args[i].startswith('--'): return args[i].split('=', 1)[1]
        i += 1
        if i >= len(args):
            errors.append(f'{flag} requires a value')
            return None
        return args[i]
    while i < len(args):
        arg = args[i]
        flag = arg.split('=', 1)[0] if arg.startswith('--') else arg
        if flag in BYPASS_FLAGS:
            errors.append(f'CLI flag {flag} is not allowed with a profile launch')
        elif flag in ('-s', '--sandbox'):
            v = value(flag)
            if v is not None: overrides['sandbox_mode'] = (v, f'CLI {flag}')
        elif flag in ('-a', '--ask-for-approval'):
            v = value(flag)
            if v is not None: overrides['approval_policy'] = (v, f'CLI {flag}')
        elif flag in ('-C', '--cd'):
            v = value(flag)
            if v is not None: cwd = Path(v)
        elif flag in ('-c', '--config'):
            v = value(flag)
            if v is not None and '=' in v:
                key, raw = v.split('=', 1)
                key = key.strip()
                try: parsed = tomllib.loads(f'v = {raw}')['v']
                except ValueError: parsed = raw
                if key in ('sandbox_mode', 'approval_policy'): overrides[key] = (parsed, f'CLI {flag} {key}')
                elif key.split('.')[0] in OPAQUE_KEYS or key.split('.')[0] == 'windows':
                    errors.append(f'CLI override of `{key}` cannot be ranked and is not allowed')
            elif v is not None:
                errors.append(f'malformed {flag} override {v!r}')
        elif flag in WARN_FLAGS:
            warnings.append(f'CLI flag {flag} changes behavior outside sandbox_mode/approval_policy (see PD-4)')
        i += 1
    return overrides, cwd, errors, warnings

def merge(layers):
    """layers: ordered (label, dict) lowest to highest precedence. Returns {key: (value, label)} for security keys."""
    merged = {}
    for label, config in layers:
        for key in ('sandbox_mode', 'approval_policy'):
            if key in config: merged[key] = (config[key], label)
        if isinstance(config.get('windows'), dict) and 'sandbox' in config['windows']:
            merged['windows.sandbox'] = (config['windows']['sandbox'], label)
    return merged

def resolve_profile(name, codex_home, cwd, cli_args=(), platform=None, confirm=None):
    """Return a report dict. `ok` is True only when no error was found. `confirm(name, home, cwd, cli_args)` returns
    (sandbox, approval) as rendered by Codex, or raises; None skips confirmation (report says NOT RUN)."""
    platform = platform or sys.platform
    errors, warnings = check_name(name), []
    report = {'profile': name, 'ok': False, 'errors': errors, 'warnings': warnings, 'intent': None,
              'expected': None, 'confirmed': 'NOT RUN', 'notes': []}
    if errors: return report
    home, cwd = Path(codex_home), Path(cwd)
    overrides, cwd_override, cli_errors, cli_warnings = parse_cli(cli_args)
    errors += cli_errors; warnings += cli_warnings
    if cwd_override is not None: cwd = cwd_override if cwd_override.is_absolute() else cwd / cwd_override
    if not cwd.is_dir():
        errors.append(f'working directory does not exist: {cwd}')
        return report
    profile_path = locate_profile(name, home, errors)
    user_cfg_path = home / 'config.toml'
    user_cfg = load_toml(user_cfg_path, 'config.toml', errors) if user_cfg_path.is_file() else {}
    if user_cfg and (user_cfg.get('profile') is not None or name in (user_cfg.get('profiles') or {})):
        errors.append(f'config.toml contains legacy `profile` or `[profiles.{name}]`; ambiguous selection (Codex 0.162.1 errors on this)')
    if profile_path is None or user_cfg is None: return report
    profile = load_toml(profile_path, profile_path.name, errors)
    if profile is None: return report
    e, w = validate_codex_config(profile, profile_path.name)
    errors += e; warnings += w
    for key in ('sandbox_mode', 'approval_policy'):
        if key not in profile: errors.append(f'{profile_path.name}: must declare `{key}` explicitly; an undeclared tier has no restriction to protect')
    if SANDBOX_RANK.get(profile.get('sandbox_mode')) is None and 'sandbox_mode' in profile:
        errors.append(f'{profile_path.name}: unsupported sandbox_mode {profile["sandbox_mode"]!r}')
    if 'approval_policy' in profile and approval_rank(profile['approval_policy']) == UNKNOWN_APPROVAL_RANK:
        errors.append(f'{profile_path.name}: approval_policy {profile["approval_policy"]!r} cannot be ranked; use "never" or "on-request"')
    if errors: return report
    intent = {'sandbox_mode': profile['sandbox_mode'], 'approval_policy': profile['approval_policy']}
    report['intent'] = intent

    root, project_files = project_layers(cwd)
    trust = repository_trust(root, home)
    layers = [('config.toml', user_cfg), (profile_path.name, profile)]
    if project_files and trust == 'untrusted':
        warnings.append('project .codex/ layers are skipped by Codex: repository is marked untrusted')
        project_files = []
    elif project_files and trust == 'unknown':
        warnings.append('repository trust is not recorded; project layers are assumed to load (fail-closed assumption)')
    for path in project_files:
        label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        cfg = load_toml(path, label, errors)
        if cfg is None: continue
        e, w = validate_codex_config(cfg, label)
        errors += e; warnings += w
        opaque = sorted(k for k in cfg if k in OPAQUE_KEYS or k == 'windows')
        if opaque: errors.append(f'{label}: sets {opaque}, which outranks the profile and cannot be ranked')
        layers.append((label, cfg))
    layers.append(('CLI', {k: v for k, (v, _) in overrides.items()}))
    merged = merge(layers)
    if errors: return report

    sandbox, sandbox_src = merged['sandbox_mode']
    approval, approval_src = merged['approval_policy']
    for key, value, src, rank_ok in (
            ('sandbox_mode', sandbox, sandbox_src, SANDBOX_RANK.get(sandbox, 99) <= SANDBOX_RANK[intent['sandbox_mode']]),
            ('approval_policy', approval, approval_src, approval_rank(approval) <= approval_rank(intent['approval_policy']))):
        if not rank_ok:
            errors.append(f'{key} weakened: profile intends {intent[key]!r} but {src} sets {value!r}')
        elif value != intent[key]:
            warnings.append(f'{key} tightened by {src}: {value!r} (profile intends {intent[key]!r})')
    expected = {'sandbox_mode': sandbox, 'approval_policy': approval, 'sources': {'sandbox_mode': sandbox_src, 'approval_policy': approval_src}}
    if sandbox == 'workspace-write' and platform == 'win32' and 'windows.sandbox' not in merged:
        expected['sandbox_mode_effective_guess'] = 'read-only'
        report['notes'].append('Windows: no [windows] sandbox configured, so Codex 0.162.1 resolves workspace-write to read-only. Do not assume write access.')
    elif sandbox == 'workspace-write' and platform == 'win32':
        report['notes'].append('Windows: workspace-write is configured, but real write access is NOT confirmed by this launcher.')
    report['expected'] = expected
    if errors: return report

    if confirm is not None:
        try:
            got_sandbox, got_approval = confirm(name, home, cwd, cli_args)
        except Exception as exc:  # any failure to confirm is a failure to launch
            errors.append(f'confirmation by Codex failed: {exc}')
            return report
        report['confirmed'] = {'sandbox_mode': got_sandbox, 'approval_policy': got_approval}
        if got_sandbox not in SANDBOX_RANK or SANDBOX_RANK[got_sandbox] > SANDBOX_RANK[intent['sandbox_mode']]:
            errors.append(f'Codex renders sandbox_mode {got_sandbox!r}, weaker than or unrecognized against intent {intent["sandbox_mode"]!r}')
        if got_approval not in APPROVAL_RANK or approval_rank(got_approval) > approval_rank(intent['approval_policy']):
            errors.append(f'Codex renders approval_policy {got_approval!r}, weaker than or unrecognized against intent {intent["approval_policy"]!r}')
        if not errors and (got_sandbox, got_approval) != (sandbox, approval):
            warnings.append(f'Codex renders ({got_sandbox}, {got_approval}), differing from expected ({sandbox}, {approval}); the rendered value governs')
    report['ok'] = not errors
    return report

def codex_confirm(name, home, cwd, cli_args):
    """Render the permissions block with the installed Codex (no model call) for the same profile and flags."""
    if shutil.which('codex') is None: raise RuntimeError('codex not found on PATH')
    env = {**os.environ, 'CODEX_HOME': str(home)}
    run = subprocess.run(['codex', '-p', name, *cli_args, 'debug', 'prompt-input', 'permissions check'], cwd=cwd, env=env,
                         capture_output=True, text=True, encoding='utf-8', timeout=90, check=False)
    if run.returncode != 0: raise RuntimeError(f'codex debug prompt-input exit {run.returncode}: {run.stderr.strip()[:200]}')
    sandbox = re.search(r'sandbox_mode` is `([a-z-]+)`', run.stdout)
    approval = 'never' if 'Approval policy is currently never' in run.stdout else (
        'on-request' if 'How to request escalation' in run.stdout else None)
    if not sandbox or approval is None: raise RuntimeError('could not read sandbox/approval from the rendered prompt')
    return sandbox.group(1), approval

def render(report):
    lines = [f'profile: {report["profile"]}', f'intent (profile file): {report["intent"]}',
             f'EXPECTED (parsed TOML, not confirmed): {json.dumps(report["expected"])}',
             f'CONFIRMED BY CODEX (debug prompt-input): {report["confirmed"]}']
    lines += [f'NOTE: {n}' for n in report['notes']] + [f'WARN: {w}' for w in report['warnings']]
    lines += [f'FAIL: {e}' for e in report['errors']]
    lines.append('RESULT: ' + ('PASS (enforcement by the sandbox itself NOT TESTED)' if report['ok'] else 'FAIL (Codex not launched)'))
    return '\n'.join(lines)

def main(argv=None, launcher=subprocess.call, confirmer=codex_confirm):
    argv = list(sys.argv[1:] if argv is None else argv)
    own, passthrough = (argv[:argv.index('--')], argv[argv.index('--') + 1:]) if '--' in argv else (argv, [])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog='Arguments for codex go after `--`.')
    parser.add_argument('profile')
    parser.add_argument('--codex-home', type=Path, default=None)
    parser.add_argument('--check-only', action='store_true', help='validate and report; do not launch Codex')
    parser.add_argument('--skip-confirm', action='store_true', help='with --check-only: skip the `codex debug prompt-input` confirmation')
    args = parser.parse_args(own)
    if args.skip_confirm and not args.check_only:
        parser.error('--skip-confirm is only valid with --check-only')
    home = args.codex_home or default_codex_home()
    report = resolve_profile(args.profile, home, Path.cwd(), passthrough, confirm=None if args.skip_confirm else confirmer)
    print(render(report))
    if not report['ok']: return 1
    if args.check_only: return 0
    return launcher(['codex', '-p', args.profile, *passthrough], env={**os.environ, 'CODEX_HOME': str(home)})

if __name__ == '__main__':
    sys.exit(main())
