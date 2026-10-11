#!/usr/bin/env python3
"""Offline structural self-check. Not MCP interoperability or policy enforcement."""
from pathlib import Path
import os
import re
import sys
import json
import hashlib

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ('mcp-discovery','mcp-contract-design','mcp-implementation','mcp-security-review','mcp-conformance','mcp-release')
REQUIRED = ('README.md','AGENTS.md','.codex/config.toml','docs/architecture/architecture.md','docs/contracts/server-contract.md','docs/security/threat-model.md','docs/verification/evidence.md','docs/architecture/standards-matrix.md')

# Codex config coverage. Provenance: Codex CLI 0.162.1 (`codex exec --strict-config -c key=badvalue`
# probes, `codex features list`) and https://learn.chatgpt.com/docs/config-file/config-reference,
# both reviewed 2026-10-10. Offline snapshot, not a full schema: it covers security-relevant keys only
# and must be re-derived when the installed Codex version changes.
APPROVAL_POLICIES = ('on-request', 'never')
DEPRECATED_APPROVAL_POLICIES = ('on-failure',)
UNSUPPORTED_APPROVAL_POLICIES = ('untrusted',)
GRANULAR_APPROVAL_KEYS = ('sandbox_approval','rules','mcp_elicitations','request_permissions','skill_approval')
SANDBOX_MODES = ('read-only', 'workspace-write', 'danger-full-access')
PROJECT_FORBIDDEN_KEYS = ('openai_base_url','chatgpt_base_url','model_provider','model_providers','notify','profile','profiles','otel')
KNOWN_SECURITY_KEYS = ('approval_policy','approvals_reviewer','sandbox_mode','sandbox_workspace_write','default_permissions','permissions','mcp_servers','hooks','features','agents','shell_environment_policy')
OTHER_KNOWN_KEYS = ('model','model_reasoning_effort','developer_instructions','name','description','skills')
SECURITY_KEY_PATTERN = re.compile(r'approval|sandbox|permission|trust|auth|token|secret|credential|network|proxy|hook|mcp|env|shell|key', re.I)
AGENT_SCALAR_KEYS = {'enabled':bool,'interrupt_message':bool,'default_subagent_model':str,'default_subagent_reasoning_effort':str}
AGENT_INT_KEYS = {'max_concurrent_threads_per_session':1,'max_threads':1,'max_depth':0}
EXPERIMENTAL_FEATURES = ('network_proxy','prevent_idle_sleep','login_shell_package_path','analytics_plan_history')
DEPRECATED_FEATURES = ('web_search','web_search_cached','web_search_request','codex_hooks','transcript_v2','use_legacy_landlock')
ROLE_REQUIRED = ('name','description','developer_instructions')

def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)

def validate_codex_config(config, label, role_file=False):
    """Return (errors, warnings) for one parsed Codex TOML layer. Read-only; never fixes settings."""
    errors, warnings = [], []
    for key in config:
        if key in PROJECT_FORBIDDEN_KEYS:
            errors.append(f'{label}: `{key}` is not settable from project-scoped config')
        elif key not in KNOWN_SECURITY_KEYS and key not in OTHER_KNOWN_KEYS:
            if SECURITY_KEY_PATTERN.search(key):
                errors.append(f'{label}: unknown security-relevant key `{key}`')
            else:
                warnings.append(f'{label}: unmodeled key `{key}` (not validated)')
    policy = config.get('approval_policy')
    if 'approval_policy' in config:
        if isinstance(policy, str) and policy in APPROVAL_POLICIES: pass
        elif isinstance(policy, str) and policy in DEPRECATED_APPROVAL_POLICIES:
            warnings.append(f'{label}: approval_policy "{policy}" is deprecated')
        elif isinstance(policy, str) and policy in UNSUPPORTED_APPROVAL_POLICIES:
            errors.append(f'{label}: approval_policy "{policy}" is unsupported')
        elif isinstance(policy, dict) and set(policy) == {'granular'} and isinstance(policy['granular'], dict):
            granular = policy['granular']
            for gkey, gvalue in granular.items():
                if gkey not in GRANULAR_APPROVAL_KEYS: errors.append(f'{label}: unknown granular approval key `{gkey}`')
                elif not isinstance(gvalue, bool): errors.append(f'{label}: granular approval `{gkey}` must be a boolean')
        else:
            errors.append(f'{label}: unsupported approval_policy {policy!r}')
    if 'sandbox_mode' in config:
        mode = config['sandbox_mode']
        if mode not in SANDBOX_MODES: errors.append(f'{label}: unsupported sandbox_mode {mode!r}')
        elif mode == 'danger-full-access': warnings.append(f'{label}: sandbox_mode "danger-full-access" disables sandboxing')
    features = config.get('features', {})
    if not isinstance(features, dict):
        errors.append(f'{label}: features must be a table')
        features = {}
    for name, value in features.items():
        if not isinstance(value, bool): errors.append(f'{label}: features.{name} must be a boolean')
        elif name in DEPRECATED_FEATURES: warnings.append(f'{label}: features.{name} is deprecated')
        elif value and name in EXPERIMENTAL_FEATURES: warnings.append(f'{label}: features.{name} is experimental')
    agents = config.get('agents', {})
    if not isinstance(agents, dict):
        errors.append(f'{label}: agents must be a table')
        agents = {}
    for name, value in agents.items():
        if name in AGENT_INT_KEYS:
            if not is_int(value) or value < AGENT_INT_KEYS[name]:
                errors.append(f'{label}: agents.{name} must be an integer >= {AGENT_INT_KEYS[name]} (booleans rejected)')
            elif name == 'max_threads':
                warnings.append(f'{label}: agents.max_threads is a deprecated alias of agents.max_concurrent_threads_per_session')
            elif name == 'max_depth':
                warnings.append(f'{label}: agents.max_depth is schema-valid but undocumented; not proof of isolation')
        elif name in AGENT_SCALAR_KEYS:
            if not isinstance(value, AGENT_SCALAR_KEYS[name]): errors.append(f'{label}: agents.{name} must be {AGENT_SCALAR_KEYS[name].__name__}')
        elif isinstance(value, dict):
            for rkey, rvalue in value.items():
                if rkey not in ('description','config_file'): errors.append(f'{label}: unknown key agents.{name}.{rkey}')
                elif not isinstance(rvalue, str): errors.append(f'{label}: agents.{name}.{rkey} must be a string')
        else:
            errors.append(f'{label}: agents.{name} must be a role table or a supported agents key')
    if 'max_threads' in agents and 'max_concurrent_threads_per_session' in agents:
        warnings.append(f'{label}: both agents.max_threads and agents.max_concurrent_threads_per_session are set')
    if role_file:
        for key in ROLE_REQUIRED:
            if not isinstance(config.get(key), str) or not config[key].strip():
                errors.append(f'{label}: role file requires non-empty string `{key}`')
    return errors, warnings

def validate_codex_files(root):
    """Validate .codex/config.toml, its role tables and agent files. Returns (errors, warnings)."""
    import tomllib
    errors, warnings = [], []
    config_path = root / '.codex' / 'config.toml'
    if not config_path.is_file(): return errors, warnings
    config = tomllib.loads(config_path.read_text(encoding='utf-8'))
    e, w = validate_codex_config(config, '.codex/config.toml')
    errors += e; warnings += w
    roots = {p.resolve() for p in [root / '.codex' / 'agents']}
    for name, role in config.get('agents', {}).items():
        if not isinstance(role, dict) or 'config_file' not in role or not isinstance(role['config_file'], str): continue
        target = (config_path.parent / role['config_file']).resolve()
        target_alt = (root / role['config_file']).resolve()
        found = next((t for t in (target, target_alt) if t.is_file()), None)
        if found is None: errors.append(f'agents.{name}.config_file not found: {role["config_file"]}')
        elif found.parent not in roots: errors.append(f'agents.{name}.config_file is outside .codex/agents: {role["config_file"]}')
    for path in sorted((root / '.codex' / 'agents').glob('*.toml')):
        label = path.relative_to(root).as_posix()
        e, w = validate_codex_config(tomllib.loads(path.read_text(encoding='utf-8')), label, role_file=True)
        errors += e; warnings += w
    return errors, warnings

def repository_trust(root, codex_home=None):
    """Return 'trusted' or 'untrusted' only when the user config positively says so; otherwise 'unknown'."""
    import tomllib
    home = Path(codex_home) if codex_home else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    try:
        projects = tomllib.loads((home / 'config.toml').read_text(encoding='utf-8')).get('projects', {})
    except (OSError, ValueError):
        return 'unknown'
    target = os.path.normcase(os.path.normpath(str(root.resolve())))
    for key, value in projects.items() if isinstance(projects, dict) else ():
        if not isinstance(value, dict) or value.get('trust_level') not in ('trusted', 'untrusted'): continue
        candidate = os.path.normcase(os.path.normpath(str(key)))
        if target == candidate or target.startswith(candidate.rstrip(os.sep) + os.sep):
            return value['trust_level']
    return 'unknown'

def distributable_inventory(root):
    """SHA-256 of every distributable file. Excludes VCS metadata (`.git`), bytecode caches and MANIFEST.json itself."""
    result = {}
    for p in sorted(root.rglob('*')):
        parts = p.relative_to(root).parts
        if not p.is_file() or p == root / 'MANIFEST.json' or '.git' in parts or '__pycache__' in parts: continue
        result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def manifest_errors(root):
    """Compare MANIFEST.json with the distributable inventory; works for extracted ZIPs and Git working trees."""
    manifest = root / 'MANIFEST.json'
    if not manifest.is_file(): return []
    try:
        recorded = json.loads(manifest.read_text(encoding='utf-8'))['files']
        actual = distributable_inventory(root)
    except Exception as exc:
        return [f'manifest validation: {exc}']
    errors = []
    for path in sorted(recorded.keys() | actual.keys()):
        if path not in recorded: errors.append(f'unmanifested file: {path}')
        elif path not in actual: errors.append(f'manifested file absent: {path}')
        elif recorded[path] != actual[path]: errors.append(f'checksum mismatch: {path}')
    return errors

def main():
    errors = []
    for file in REQUIRED:
        if not (ROOT / file).is_file(): errors.append(f'missing: {file}')
    for name in SKILLS:
        p = ROOT / '.agents' / 'skills' / name / 'SKILL.md'
        if not p.is_file():
            errors.append(f'missing skill: {name}')
            continue
        content = p.read_text(encoding='utf-8')
        if not re.search(r'\A---\nname: '+re.escape(name)+r'\ndescription: .+\n---\n', content):
            errors.append(f'invalid skill frontmatter: {name}')
        if not (p.parent / 'references' / 'checklist.md').is_file(): errors.append(f'missing checklist: {name}')
    for name in ('mcp-researcher','mcp-implementer','mcp-security','mcp-verifier'):
        if not (ROOT / '.codex' / 'agents' / f'{name}.toml').is_file(): errors.append(f'missing agent: {name}')
    try:
        import tomllib
        for p in [ROOT/'.codex/config.toml', *(ROOT/'.codex/agents').glob('*.toml')]:
            if p.is_file(): tomllib.loads(p.read_text(encoding='utf-8'))
    except ImportError:
        print('SKIP: TOML syntax and Codex config checks require Python 3.11+')
    except Exception as exc:
        errors.append(f'TOML parsing: {exc}')
    else:
        config_errors, config_warnings = validate_codex_files(ROOT)
        errors += config_errors
        for warning in config_warnings: print('WARN:', warning)
        trust = repository_trust(ROOT)
        print(f'INFO: repository trust: {trust}' + ('' if trust == 'trusted' else
              ' (project .codex/ config and agents may not load; not established offline)'))
    errors += manifest_errors(ROOT)
    if errors:
        for error in errors: print('FAIL:', error)
        return 1
    print('PASS: control-plane structural checks. MCP server behavior NOT TESTED. '
          'Codex config: offline partial coverage (security-relevant keys only); host-level validation NOT TESTED.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
