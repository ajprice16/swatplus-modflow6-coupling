"""Guard: the package must not contain study-area-specific content.

The repository is meant to work for any watershed. This test scans every
text file for

* place names, dataset names and machine/user names from previous
  applications. They are stored only as truncated SHA-256 hashes of
  lower-cased words and word pairs, so the guard itself does not publish
  them. To add a term: ``hashlib.sha256(term.lower().encode()).hexdigest()[:16]``;
* absolute paths into a user's home directory or onto a Windows drive;
* USGS-style 8-digit station numbers (leading zero), which are basin
  specific when used as defaults.

If a hit is a genuine false positive, rephrase the text rather than
weakening the guard.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "build", "dist",
             "Academic Papers", ".mypy_cache", "node_modules"}
SKIP_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gpkg", ".tif", ".pyc", ".DS_Store"}

FORBIDDEN_HASHES = {
    "03a8f0dd8edb3378",
    "054da1e8bc1cb20b",
    "17a2019afc0d5d1b",
    "1e099516f790eba7",
    "1f6b505b7429fd17",
    "1fdce33b04fa29cc",
    "2157db6d182dfce9",
    "22eeedab454c7ecb",
    "290d4cdd64a06b4e",
    "29e52f8bd667e2b2",
    "2f1f6a879d3f46d9",
    "2f22948d8e92f31f",
    "3416b6c9e4b08137",
    "358e9ea14c4f7fb5",
    "449627b41f6a67e2",
    "4f8678147e6a53d1",
    "501370b3439177dd",
    "52b35ac50e0f6e78",
    "59032d13d2c68f43",
    "5a31b2b7d5bb03a8",
    "5d112b437b91fdaa",
    "5e12afeaaf89408e",
    "60e900578353e24d",
    "655f09716b4685ee",
    "6620dae25be8c616",
    "6b789b1a4397905f",
    "78d2117a4ddf72d4",
    "7b181c5db18e1ce7",
    "80aeff4d4c9b0441",
    "869dd56b79a736a0",
    "880dbbb802a9c244",
    "8e55fb373ce766e7",
    "8e680b912f0991e9",
    "916006eb23918b0b",
    "9dfe5c34b6259843",
    "a309ac16ce1299ea",
    "cf9d8e53c6740a4b",
    "d05dc094cc50a6b0",
    "ec061fafb777f294",
    "ecddec17712e4973",
    "f0fb651b5582fc1c",
}

PATH_PATTERNS = [
    re.compile(r"/(?:Users|home)/(?!<|\{|user\b|username\b|you\b|me\b)[A-Za-z0-9_.-]+/"),
    re.compile(r"\b[A-Za-z]:[\\/](?:Users|[A-Za-z]+[\\/])", re.IGNORECASE),
    re.compile(r"/Volumes/"),
]
STATION_PATTERN = re.compile(r"(?<![\d.])0\d{7}(?![\d.])")

_WORD = re.compile(r"[a-z0-9]+")


def _h(term: str) -> str:
    return hashlib.sha256(term.encode()).hexdigest()[:16]


def _text_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix in SKIP_SUFFIXES or p.name in SKIP_SUFFIXES:
            continue
        if SKIP_DIRS.intersection(p.relative_to(root).parts):
            continue
        if p.resolve() == Path(__file__).resolve():
            continue  # this file holds the guard's own patterns and samples
        try:
            yield p, p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def scan(root: Path, extra_hashes: frozenset = frozenset()):
    """Return a list of (relative_path, line_number, reason) findings."""
    forbidden = FORBIDDEN_HASHES | set(extra_hashes)
    hits = []
    for path, text in _text_files(root):
        rel = path.relative_to(root)
        # Line 0 = the file's own path, so names in filenames are caught too.
        lines = [str(rel).replace("_", " ")] + text.splitlines()
        for n, line in enumerate(lines):
            words = _WORD.findall(line.lower())
            grams = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
            grams += [f"{a} {b} {c}" for a, b, c in zip(words, words[1:], words[2:])]
            for g in grams:
                if _h(g) in forbidden:
                    hits.append((str(rel), n, f"study-area term (hash {_h(g)})"))
            for pat in PATH_PATTERNS:
                if pat.search(line):
                    hits.append((str(rel), n, "absolute machine path"))
            if STATION_PATTERN.search(line):
                hits.append((str(rel), n, "8-digit station-number-like value"))
    return hits


def test_repository_has_no_study_area_content():
    hits = scan(REPO)
    assert not hits, "Study-area content found:\n" + "\n".join(f"  {p}:{n}: {why}" for p, n, why in hits)


@pytest.mark.parametrize("line, reason", [
    ("model_dir = '/home/someone/project/model'", "absolute machine path"),
    (r"out = 'D:\data\model.gpkg'", "absolute machine path"),
    ("GAUGE = '01234567'", "8-digit station-number-like value"),
    ("probe zzleakprobe term", "study-area term"),
])
def test_guard_detects_planted_leaks(tmp_path, line, reason):
    (tmp_path / "planted.py").write_text(line + "\n")
    hits = scan(tmp_path, extra_hashes=frozenset({_h("zzleakprobe")}))
    assert any(reason in why for _, _, why in hits), hits


def test_guard_allows_placeholders(tmp_path):
    (tmp_path / "ok.md").write_text(
        "--swat-dir /path/to/TxtInOut\nexecutable: /home/<user>/bin/mf6\nversion 0.1.0, 1.0e-4, 10000000\n"
    )
    assert scan(tmp_path) == []
