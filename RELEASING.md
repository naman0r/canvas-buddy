# Release process

1. Update the version in `pyproject.toml`, run `uv sync`, tests, Ruff, and `uv build`.
2. Install the built wheel into a fresh environment outside the checkout; run `canvas-buddy --version`, `self-test`, `doctor`, and the first-run TUI.
3. Commit and push the source. Tag the verified commit as `vX.Y.Z`; never move a published tag. Publish the source archive and wheel as a GitHub release.
4. Run `python3.13 scripts/homebrew_formula.py X.Y.Z /path/to/homebrew-tap/Formula/canvas-buddy.rb` to generate the formula from `uv.lock` and the tagged source archive. Inspect the URLs/checksums and review the diff.
5. In the tap checkout, update the bottle release version in `.github/workflows/test.yml`. Run `brew style`, commit and push the formula. Wait for both Apple Silicon and Intel workflow jobs to pass before announcing the release. Each job builds from source, tests local cache/search and first-run setup without credentials, and uploads bottle archives and JSON metadata.
6. Download both workflow artifacts using `gh run download RUN_ID --repo naman0r/homebrew-tap`. Verify each archive against the SHA-256 in its JSON. Rename each archive from `local_filename` to `filename` specified there (Homebrew uses different names locally and remotely).
7. Create a tap release named `canvas-buddy-X.Y.Z` targeting the tested tap commit, then upload both renamed bottle archives. Run `brew bottle --merge --write --no-commit` with both JSON paths. This updates the installed tap checkout; copy the resulting formula to your development checkout if different. Review the bottle URLs/hashes, run `brew style`, and commit/push the bottle block.
8. Verify the published fully qualified install command from a fresh tap checkout. Run the installed app's `--version`, `self-test`, `doctor`, and first-run TUI outside the source checkout with a temporary `CANVAS_BUDDY_HOME`. Document tested platforms in release notes. Current prebuilt packages target Apple Silicon macOS 14+ and Intel macOS 15+.

Older Apple Command Line Tools can prevent source builds and `brew test`, even when bottle installation and the app itself work. Run the equivalent installed-app checks directly in that case; keep the hosted formula tests passing. Do not announce an installation command until its release assets and formula are public and the command has been verified.

The tap uses Homebrew's isolated Python virtualenv helper and checksummed source resources. Python dependencies come from the runtime dependency graph in `uv.lock`; development dependencies are excluded. Model CLIs, authentication, and Ollama models are user choices, not installation side effects.

Source CI runs tests on macOS and Linux with Python 3.11/3.13. The tap workflow builds and tests on macOS. A successful platform job does not establish that every Canvas institution or model provider works.
