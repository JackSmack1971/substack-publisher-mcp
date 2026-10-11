import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_control_plane import REQUIRED, repository_trust

EXPECTED_REQUIRED = (
    "README.md",
    "AGENTS.md",
    ".codex/config.toml",
    "docs/architecture/architecture.md",
    "docs/contracts/server-contract.md",
    "docs/security/threat-model.md",
)


def _write_codex_config(home: Path, entries: list[tuple[Path, str]]) -> None:
    config = home / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for root, trust_level in entries:
        lines.extend((f"[projects.{json.dumps(str(root))}]", f'trust_level = "{trust_level}"', ""))
    config.write_text("\n".join(lines), encoding="utf-8")


def test_repository_trust_does_not_inherit_from_parent(tmp_path):
    parent = tmp_path / "workspace"
    repository = parent / "repository"
    repository.mkdir(parents=True)
    home = tmp_path / "codex-home"
    _write_codex_config(home, [(parent, "untrusted")])

    assert repository_trust(repository, home) == "unknown"


def test_repository_trust_uses_exact_root_when_parent_conflicts(tmp_path):
    parent = tmp_path / "workspace"
    repository = parent / "repository"
    repository.mkdir(parents=True)
    home = tmp_path / "codex-home"
    _write_codex_config(home, [(parent, "trusted"), (repository, "untrusted")])

    assert repository_trust(repository, home) == "untrusted"


def test_repository_trust_accepts_exact_root_without_parent_entry(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    home = tmp_path / "codex-home"
    _write_codex_config(home, [(repository, "trusted")])

    assert repository_trust(repository, home) == "trusted"


def test_all_required_control_plane_files_exist():
    assert REQUIRED == EXPECTED_REQUIRED
    assert all((ROOT / path).is_file() for path in REQUIRED)
