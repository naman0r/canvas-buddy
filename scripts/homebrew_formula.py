#!/usr/bin/env python3
"""Generate a checksummed tap formula from a published tag and uv.lock (Python 3.11+)."""
import hashlib
from pathlib import Path
import re
import sys
import tomllib
from urllib.request import urlopen


def main():
    version, destination = sys.argv[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("Expected a release version like 0.2.0")
    root = Path(__file__).resolve().parents[1]
    packages = {p["name"]: p for p in tomllib.loads((root / "uv.lock").read_text())["package"]}
    if packages["canvas-buddy"]["version"] != version:
        raise SystemExit("Release version does not match uv.lock")
    selected, expanded = set(), set()
    def visit(name, extras=()):
        key = (name, tuple(sorted(extras)))
        if key in expanded:
            return
        expanded.add(key)
        selected.add(name)
        dependencies = list(packages[name].get("dependencies", []))
        for extra in extras:
            dependencies += packages[name].get("optional-dependencies", {}).get(extra, [])
        for dependency in dependencies:
            visit(dependency["name"], dependency.get("extra", []))
    visit("canvas-buddy")
    url = f"https://github.com/naman0r/canvas-buddy/archive/refs/tags/v{version}.tar.gz"
    with urlopen(url, timeout=90) as response:
        checksum = hashlib.sha256(response.read()).hexdigest()
    lines = ['class CanvasBuddy < Formula', '  include Language::Python::Virtualenv', '',
             '  desc "Local terminal companion for Canvas LMS"',
             '  homepage "https://github.com/naman0r/canvas-buddy"',
             f'  url "{url}"', f'  sha256 "{checksum}"', '  license "MIT"', '',
             '  depends_on "python@3.13"', '']
    for name in sorted(selected - {"canvas-buddy"}):
        source = packages[name]["sdist"]
        if not source["url"].startswith("https://files.pythonhosted.org/"):
            raise SystemExit(f"Unexpected source host for {name}")
        lines += [f'  resource "{name}" do', f'    url "{source["url"]}"',
                  f'    sha256 "{source["hash"].removeprefix("sha256:")}"', '  end', '']
    lines += ['  def install', '    virtualenv_install_with_resources', '  end', '',
              '  def caveats', '    <<~EOS', '      Run canvas-buddy to connect Canvas and choose courses.',
              '      For answers, install/login to Codex or OpenCode, or choose an Ollama model.',
              '      Ollama embeddings are optional. Run canvas-buddy doctor for setup help.',
              '    EOS', '  end', '', '  test do',
              '    assert_match version.to_s, shell_output("#{bin}/canvas-buddy --version")',
              '    assert_match "Self-test passed", shell_output("#{bin}/canvas-buddy self-test")',
              '  end', 'end', '']
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    print(f"Wrote {path} with {len(selected) - 1} pinned resources")


if __name__ == "__main__":
    main()
