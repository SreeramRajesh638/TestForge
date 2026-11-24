"""Download the benchmark modules listed in manifest.json.

Each module is taken from its package's source distribution on PyPI at a
pinned version, checked against its SHA-256, and written to
benchmark/modules/<name>.py together with the package's license text.
Nothing from the archives is executed.

    python benchmark/fetch.py
    python experiments/run.py --modules benchmark/modules --configs mock_baseline
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "modules"
LICENSE_NAMES = ("license", "licence", "copying", "mit-license", "license-mit")


def _members(archive: Path):
    """Yield (name, reader) for every file in an sdist (.tar.gz or .zip)."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                yield name, (lambda n=name: z.read(n))
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if m.isfile():
                    yield m.name, (lambda m=m: t.extractfile(m).read())


def fetch(entry: dict, cache: Path) -> tuple[str, str]:
    spec = f"{entry['package']}=={entry['version']}"
    dest = cache / entry["package"]
    dest.mkdir(exist_ok=True)
    if not any(dest.iterdir()):
        subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--no-binary", ":all:",
                        "-q", "-d", str(dest), spec], check=True)
    archive = next(dest.iterdir())

    source = license_text = None
    for name, read in _members(archive):
        inner = name.split("/", 1)[1] if "/" in name else name  # strip "pkg-1.0/"
        if inner == entry["path"]:
            source = read()
        elif "/" not in inner and inner.lower().split(".")[0] in LICENSE_NAMES and license_text is None:
            license_text = read()
    if source is None:
        return "missing", f"{entry['path']} not found in {archive.name}"
    digest = hashlib.sha256(source).hexdigest()
    if digest != entry["sha256"]:
        return "mismatch", f"sha256 {digest} != {entry['sha256']}"

    (OUT / f"{entry['name']}.py").write_bytes(source)
    lic_dir = OUT / "LICENSES"
    lic_dir.mkdir(exist_ok=True)
    note = f"{entry['package']} {entry['version']} - license: {entry['license']}\n\n"
    body = license_text.decode(errors="replace") if license_text else \
        "(no license file in the source distribution; see the package metadata on PyPI)\n"
    (lic_dir / f"{entry['name']}.txt").write_text(note + body)
    return "ok", f"{len(source.splitlines())} lines"


def main() -> int:
    manifest = json.loads((HERE / "manifest.json").read_text())
    OUT.mkdir(exist_ok=True)
    failures = 0
    with tempfile.TemporaryDirectory(prefix="tf_bench_") as tmp:
        for entry in manifest["modules"]:
            try:
                status, detail = fetch(entry, Path(tmp))
            except subprocess.CalledProcessError as e:
                status, detail = "error", f"pip download failed ({e.returncode})"
            failures += status != "ok"
            print(f"{status:>8}  {entry['name']:<12} {entry['package']} {entry['version']}  {detail}")
    print(f"\n{len(manifest['modules']) - failures}/{len(manifest['modules'])} modules in {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
