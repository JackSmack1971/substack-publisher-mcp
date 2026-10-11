# Worked examples

Walk-throughs built from this repository's state as of 2026-10-10. They show the shape of a run; they are not recorded model runs. Paths and line numbers can drift; re-localize rather than trust them.

## Small task, concise path

Task: reword the launcher error for `--skip-confirm` without `--check-only`.

1. Preserve: tree has uncommitted work in `scripts/launch_codex_profile.py` and its test; coordinate before touching them.
2. Discover: no extra docs needed; test command is `python -B -m unittest discover -s tests`.
3. Localize: grep the message text, found once at `scripts/launch_codex_profile.py:253`.
4. Oracle: `tests/test_launch_codex_profile.py` `test_check_only_never_launches_and_skip_confirm_requires_it` covers the failing argument path. It asserts the exit, not the wording, so the wording needs no test change.
5. Scope: allow `scripts/launch_codex_profile.py`; protect tests, docs, `MANIFEST.json` (checksum refresh only if the file is distributed).
6. Edit one string, re-run the oracle.

Five stages, one line each, zero whole-file surveys. Stage 5 flags overlap with dirty files, which is the cue to ask first.

## Large task, expanded path

Task: change the repository `approval_policy` default so the `never` tier can launch.

1. Preserve: record dirty tree; `.codex/config.toml` is already modified.
2. Discover: `AGENTS.md`, `docs/governance/operating-procedures.md`, and `docs/architecture/consequence-tier-profiles.md` section "Unresolved policy decisions". That section says PD-2 (this default) has no owner decision. **Stop: the decision is not the agent's.** Ask, or proceed only on a recorded decision.
3. Localize (after the decision): grep `approval_policy` lists `.codex/config.toml`, the launcher, the validator, the profile doc and three test files. Candidate owners are more than one, so expanded path.
4. Oracle: baseline first. Run the full suite plus `CP008_RUNTIME=1 ... -p "test_*profile*.py"`; record pass/skip counts. Add a characterization test for the `never`-tier result in a trusted checkout before changing it.
5. Scope: allow `.codex/config.toml`, the launcher tests, the profile doc, `MANIFEST.json`. Protect `.codex/agents/*.toml`, sandbox settings and production-sensitive tiers. Consumers of the contract: the launcher's higher-layer comparison.
6. Edit, re-run baseline, diff review, handoff with `Not read` (managed config, other CODEX_HOME) and `Limitation` (sandbox enforcement not tested).

The large task still reads about eight files, never the repository.
