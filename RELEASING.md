# Release process

1. Update the version in `pyproject.toml`, run `uv sync`, tests, Ruff, and `uv build`.
2. Install the built wheel into a fresh environment outside the checkout; run `canvas-buddy --version`, `self-test`, `doctor`, and the first-run TUI.
3. Commit and push the source. Tag the verified commit as `vX.Y.Z`; never move a published tag. Publish the source archive and wheel as a GitHub release.
4. Run `python3.13 scripts/homebrew_formula.py X.Y.Z /path/to/homebrew-tap/Formula/canvas-buddy.rb` to generate the formula from `uv.lock` and the tagged source archive. Inspect the URLs/checksums and review the diff.
5. In the tap checkout, run `brew style`, then install/reinstall the formula and run `brew test`. The formula's test exercises local cache/search without credentials or network access. Test first-run setup with a temporary `CANVAS_BUDDY_HOME`.
6. Commit/push the tap only after checks pass. Verify the published fully qualified install command from a fresh tap checkout. Document the tested platform and any untested platforms in release notes.

The tap uses Homebrew's isolated Python virtualenv helper and checksummed source resources. Python dependencies come from the runtime dependency graph in `uv.lock`; development dependencies are excluded. Model CLIs, authentication, and Ollama models are user choices, not installation side effects.

Source CI runs tests on macOS and Linux with Python 3.11/3.13. The tap workflow builds and tests on macOS. A successful platform job does not establish that every Canvas institution or model provider works.
