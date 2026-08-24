"""
Update provenance metadata in pytest_conda_solvers/conda-solver-tests/*.yaml
to a new conda/conda commit.

For each test, this tool:
  1. Fetches the test function source at both the old (stored) commit and the
     new (target) commit.
  2. Compares the two ASTs structurally (ignoring line numbers and formatting).
  3. If the ASTs are identical, updates `commit` and `url` (with fresh line
     ranges) in the YAML file.
  4. If the ASTs differ, it warns and skips the test -- the ported test may
     need manual review. Use --force to update anyway.

Usage:
    python tools/update_provenance.py --commit <SHA> [--force] [--dry-run]

Run from the repository root.
"""

# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///

import argparse
import ast
import asyncio
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent
YAML_DIR = REPO_ROOT / "pytest_conda_solvers" / "conda-solver-tests"

URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<org>[^/]+)/(?P<repo>[^/]+)/blob/"
    r"(?P<commit>[0-9a-f]+)/(?P<path>[^#]+)"
    r"(?:#L(?P<start>\d+)-L(?P<end>\d+))?$"
)

# Pre-populated by _fetch_all() before any parsing begins.
_source_cache: dict[tuple[str, str], str] = {}


@functools.cache
def _gh_token() -> str | None:
    """Return a GitHub token from `gh auth token`, or None if gh is unavailable."""
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
        )
        token = result.stdout.strip()
        return token if token else None
    except Exception:
        return None


async def _fetch_all(pairs: set[tuple[str, str]], token: str | None) -> None:
    """Fetch all (commit, filepath) pairs concurrently."""
    headers = {"Authorization": f"token {token}"} if token else {}

    async def _fetch_one(client: httpx.AsyncClient, commit: str, filepath: str) -> None:
        url = f"https://raw.githubusercontent.com/conda/conda/{commit}/{filepath}"
        print(f"  fetching {filepath} @ {commit[:12]}...")
        resp = await client.get(url)
        resp.raise_for_status()
        _source_cache[commit, filepath] = resp.text

    async with httpx.AsyncClient(headers=headers) as client:
        await asyncio.gather(*[_fetch_one(client, c, f) for c, f in pairs])


def _fetch_source(commit: str, filepath: str) -> str:
    return _source_cache[commit, filepath]


@functools.cache
def _parse_functions(commit: str, filepath: str) -> dict[str, tuple[int, int, ast.AST]]:
    """Return {qualified_name: (start_line, end_line, ast_node)} for all functions."""
    source = _fetch_source(commit, filepath)
    tree = ast.parse(source)
    funcs: dict[str, tuple[int, int, ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[f"{node.name}.{item.name}"] = (
                        item.lineno,
                        item.end_lineno,
                        item,
                    )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = (node.lineno, node.end_lineno, node)
    return funcs


def _ast_dump(node: ast.AST) -> str:
    """Dump an AST node without position attributes for structural comparison."""
    return ast.dump(node, include_attributes=False)


def _node_id_key(node_id: str) -> tuple[str, str]:
    """
    Return a (filepath, func_name) from a node ID, handling two formats:
    1. filepath::ClassName.method_name::N  (dot-separated class and method)
    2. filepath::ClassName::method_name::N (double-colon-separated, pytest-style)
    3. filepath::func_name::N              (top-level function with subtest index)

    Formats 2 and 3 both have parts[1] without a dot, but differ in whether
    parts[2] is a method name (format 2) or a numeric subtest index (format 3).
    """
    parts = node_id.split("::")
    filepath = parts[0]
    if len(parts) >= 3 and "." not in parts[1] and not parts[2].isdigit():
        func_name = f"{parts[1]}.{parts[2]}"
    else:
        func_name = parts[1]
    return filepath, func_name


def _collect_node_ids() -> dict[str, set[str]]:
    """Return {filepath: {func_name, ...}} grouped from all YAML node_ids."""
    file_funcs: dict[str, set[str]] = {}
    for yaml_path in sorted(YAML_DIR.glob("*.yaml")):
        for line in yaml_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s+node_id:\s+(.+)", line)
            if m:
                filepath, func_name = _node_id_key(m.group(1).strip())
                file_funcs.setdefault(filepath, set()).add(func_name)
    return file_funcs


def _update_yaml(
    yaml_path: Path,
    new_commit: str,
    new_lines: dict[tuple[str, str], tuple[int, int]],
    changed: set[tuple[str, str]],
    force: bool,
    dry_run: bool,
) -> list[dict]:
    """Patch a single YAML file. Returns one result dict per test encountered."""
    lines = yaml_path.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    current_node_id: str | None = None
    results: list[dict] = []

    for line in lines:
        nid_m = re.match(r"(\s+)node_id:\s+(.+)", line)
        if nid_m:
            current_node_id = nid_m.group(2).strip()

        url_m = re.match(r"(\s+)url:\s+(https://github\.com/conda/conda/blob/.+)", line)
        if url_m and current_node_id:
            indent = url_m.group(1)
            old_url = url_m.group(2).strip()
            key = _node_id_key(current_node_id)

            if key in changed and not force:
                print(
                    f"  SKIP  {current_node_id!r}: AST changed between commits "
                    f"(use --force to update anyway)"
                )
                results.append(
                    {
                        "node_id": current_node_id,
                        "status": "skipped",
                        "old_url": old_url,
                    }
                )
                current_node_id = None
            elif key in new_lines:
                start, end = new_lines[key]
                filepath = key[0]
                new_url = (
                    f"https://github.com/conda/conda/blob/{new_commit}"
                    f"/{filepath}#L{start}-L{end}"
                )
                out.append(f"{indent}url: {new_url}\n")
                old_m = URL_PATTERN.match(old_url)
                old_start = (
                    int(old_m.group("start"))
                    if old_m and old_m.group("start")
                    else None
                )
                old_end = (
                    int(old_m.group("end")) if old_m and old_m.group("end") else None
                )
                status = (
                    "updated" if (old_start != start or old_end != end) else "unchanged"
                )
                results.append(
                    {
                        "node_id": current_node_id,
                        "status": status,
                        "old_url": old_url,
                        "new_url": new_url,
                    }
                )
                current_node_id = None
                continue

        # Also update commit: lines
        commit_m = re.match(r"(\s+)commit:\s+([0-9a-f]{40})", line)
        if commit_m and current_node_id:
            # Only rewrite if we're going to update this test's url too
            key = _node_id_key(current_node_id)
            if key in new_lines and (key not in changed or force):
                indent = commit_m.group(1)
                out.append(f"{indent}commit: {new_commit}\n")
                continue

        out.append(line)

    if not dry_run and any(
        result["status"] in ("updated", "unchanged") for result in results
    ):
        yaml_path.write_text("".join(out), encoding="utf-8")

    return results


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        required=True,
        metavar="SHA",
        help="Target conda/conda commit SHA to update provenance to",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Update even if the function AST changed between commits",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "--update-lines",
        action="store_true",
        help="Refresh line-number ranges even for tests that already reference the target commit "
        "(fixes stale URLs left behind by earlier tool runs)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON summary to stdout and suppress all other output. "
        "Combine with --dry-run to preview what would change as JSON without writing files.",
    )
    args = parser.parse_args()

    if len(args.commit) != 40 or not re.fullmatch(r"[0-9a-f]+", args.commit):
        sys.exit(
            f"error: --commit must be a full 40-character lowercase SHA, got {args.commit!r}"
        )

    if args.json:
        _real_stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")

    print(f"Target commit: {args.commit}")
    if args.dry_run:
        print("(dry run — no files will be written)")
    print()

    # Collect all (filepath, func_name) pairs referenced in YAMLs, grouped by
    # the commit stored in each test's provenance block.
    yaml_files = sorted(YAML_DIR.glob("*.yaml"))

    # Build: {old_commit: {filepath: {func_name}}}
    by_old_commit: dict[str, dict[str, set[str]]] = {}
    for yaml_path in yaml_files:
        current_node_id = None
        current_commit = None
        for line in yaml_path.read_text(encoding="utf-8").splitlines():
            nid_m = re.match(r"\s+node_id:\s+(.+)", line)
            if nid_m:
                current_node_id = nid_m.group(1).strip()
            commit_m = re.match(r"\s+commit:\s+([0-9a-f]{40})", line)
            if commit_m:
                current_commit = commit_m.group(1)
            if current_node_id and current_commit:
                filepath, func_name = _node_id_key(current_node_id)
                by_old_commit.setdefault(current_commit, {}).setdefault(
                    filepath, set()
                ).add(func_name)
                current_node_id = None
                current_commit = None

    # Collect all unique (commit, filepath) pairs and compare
    pairs: set[tuple[str, str]] = set()
    for old_commit, file_funcs in by_old_commit.items():
        if old_commit == args.commit:
            if args.update_lines:
                for filepath in file_funcs:
                    pairs.add((args.commit, filepath))
            continue
        for filepath in file_funcs:
            pairs.add((old_commit, filepath))
            pairs.add((args.commit, filepath))

    if pairs:
        await _fetch_all(pairs, _gh_token())
        print()

    new_lines: dict[
        tuple[str, str], tuple[int, int]
    ] = {}  # (filepath, func) -> (start, end)
    changed: set[tuple[str, str]] = set()  # AST changed between old and new
    all_results: list[dict] = []

    for old_commit, file_funcs in by_old_commit.items():
        if old_commit == args.commit:
            if not args.update_lines:
                print(f"Commit {old_commit[:12]} already matches target, skipping.")
                continue
            print(f"Commit {old_commit[:12]} already matches target. Refreshing line numbers:")
            for filepath, funcs in file_funcs.items():
                new_parsed = _parse_functions(args.commit, filepath)
                for func_name in sorted(funcs):
                    key = (filepath, func_name)
                    if func_name not in new_parsed:
                        print(f"  MISSING  {func_name!r} not found in {filepath}")
                        continue
                    start, end, _ = new_parsed[func_name]
                    new_lines[key] = (start, end)
                    print(f"  LINE     {func_name!r} in {filepath} -> L{start}-L{end}")
            continue
        print(f"Comparing {old_commit[:12]} -> {args.commit[:12]}:")
        for filepath, funcs in file_funcs.items():
            old_parsed = _parse_functions(old_commit, filepath)
            new_parsed = _parse_functions(args.commit, filepath)
            for func_name in sorted(funcs):
                key = (filepath, func_name)
                if func_name not in new_parsed:
                    print(
                        f"  MISSING  {func_name!r} not found in {filepath} at new commit"
                    )
                    all_results.append(
                        {"node_id": f"{filepath}::{func_name}", "status": "missing"}
                    )
                    continue
                start, end, new_node = new_parsed[func_name]
                new_lines[key] = (start, end)
                if func_name in old_parsed:
                    _, _, old_node = old_parsed[func_name]
                    if _ast_dump(old_node) != _ast_dump(new_node):
                        changed.add(key)
                        print(f"  CHANGED  {func_name!r} in {filepath}")
                    else:
                        print(f"  OK       {func_name!r} in {filepath}")
                else:
                    print(
                        f"  NEW      {func_name!r} not found at old commit, will update"
                    )
    print()

    # Apply updates
    total = 0
    for yaml_path in yaml_files:
        file_results = _update_yaml(
            yaml_path,
            args.commit,
            new_lines,
            changed,
            force=args.force,
            dry_run=args.dry_run,
        )
        for r in file_results:
            r["yaml_file"] = yaml_path.name
        all_results.extend(file_results)
        n_updated = sum(1 for r in file_results if r["status"] == "updated")
        n_unchanged = sum(1 for r in file_results if r["status"] == "unchanged")
        if n_updated or n_unchanged:
            action = "would update" if args.dry_run else "updated"
            parts = []
            if n_updated:
                parts.append(f"{n_updated} shifted")
            if n_unchanged:
                parts.append(f"{n_unchanged} SHA-only")
            print(
                f"{action} {n_updated + n_unchanged} test(s) in {yaml_path.name} ({', '.join(parts)})"
            )
            total += n_updated + n_unchanged

    print()
    print(f"{'Would update' if args.dry_run else 'Updated'} {total} test(s) total.")
    if changed and not args.force:
        print(
            f"  {len(changed)} test(s) skipped due to AST changes. "
            f"Re-run with --force to update them anyway."
        )

    if args.json:
        sys.stdout = _real_stdout
        print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
