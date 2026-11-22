#!/usr/bin/env python3
"""Local public-Instagram scanner -> review-only Draft PR on an experimental base.

Never writes menu.json/promotions.json, never merges PRs, and never publishes.
Runs in a fresh temporary clone so the user's local worktree remains untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

REPO = "Ed-Lam-11111010010/sushiro-counter"
BASE = "experiment/instagram-auto-publish-plan-b"
REMOTE = f"https://github.com/{REPO}.git"
CANDIDATES = Path("data/instagram_candidates.json")
BRANCH_PREFIX = "automation/instagram-local-"


def require_executables(submit: bool) -> None:
    missing = [name for name in (["git", "gh"] if submit else ["git"]) if not shutil.which(name)]
    if missing:
        raise RuntimeError("Missing executable(s) on PATH: " + ", ".join(missing))


def command(*argv: str, cwd: Path | None = None, timeout: int = 240, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    try:
        result = subprocess.run(
            list(argv), cwd=cwd, env=env, text=True, encoding="utf-8",
            capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out: {argv[0]}") from exc
    if check and result.returncode:
        # Deliberately avoid echoing command output (which might contain credentials).
        raise RuntimeError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed (exit {result.returncode})")
    return result


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return data


def atomic_json(path: Path, data: dict) -> None:
    target = path.with_name(path.name + ".tmp")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(target, path)


def candidate_signature(data: dict) -> str:
    """Ignore diagnostics/observedAt so identical posts don't produce daily PRs."""
    rows = []
    for post in data.get("posts", []):
        if not isinstance(post, dict):
            continue
        rows.append({
            "postId": post.get("postId"),
            "url": post.get("url"),
            "contentFingerprint": post.get("contentFingerprint"),
            "items": post.get("items", []),
        })
    rows.sort(key=lambda x: (str(x["postId"]), str(x["url"])))
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def discovery_healthy(report: dict) -> bool:
    return (
        report.get("profileFetchStatus") == "success"
        and report.get("postDiscoveryStatus") == "found"
        and report.get("sourceStatus") == "posts_found"
        and report.get("discoveryComplete") is True
        and report.get("sourceFailures") == []
        and isinstance(report.get("posts"), list)
        and bool(report["posts"])
    )


def review_branch(signature: str) -> str:
    return BRANCH_PREFIX + signature[:20]


def remote_branch_document(repo_dir: Path, branch: str) -> dict | None:
    result = command("git", "ls-remote", "--exit-code", "--heads", "origin", branch, cwd=repo_dir, check=False)
    if result.returncode == 2:
        return None
    if result.returncode:
        raise RuntimeError("Could not check remote review branch")
    command("git", "fetch", "--quiet", "origin", branch, cwd=repo_dir)
    document = command("git", "show", "FETCH_HEAD:data/instagram_candidates.json", cwd=repo_dir)
    return json.loads(document.stdout)


def existing_pr(branch: str) -> dict | None:
    result = command(
        "gh", "pr", "list", "--repo", REPO, "--head", branch, "--state", "all",
        "--json", "number,url,state,isDraft",
    )
    entries = json.loads(result.stdout)
    return entries[0] if entries else None


@contextmanager
def exclusive_lock(directory: Path):
    lock_file = directory / "scan.lock"
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"A scan is already running, or a stale lock exists: {lock_file}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock_file.unlink(missing_ok=True)


def execute(state_dir: Path, *, submit: bool) -> dict:
    require_executables(submit)
    state_dir.mkdir(parents=True, exist_ok=True)
    if submit:
        command("gh", "auth", "status")
    with exclusive_lock(state_dir):
        with tempfile.TemporaryDirectory(prefix="sushiro-instagram-") as tmp:
            repo_dir = Path(tmp) / "repo"
            command("git", "clone", "--quiet", "--depth", "1", "--branch", BASE, REMOTE, str(repo_dir), timeout=180)
            candidate_file = repo_dir / CANDIDATES
            saved_candidates = state_dir / "instagram_candidates.json"
            # Preserve last scan's review candidates in a disposable clone.
            if saved_candidates.is_file():
                previous = read_json(saved_candidates)
                if previous.get("requiresReview") is not True:
                    raise ValueError("Saved candidate document is not review-only")
                shutil.copyfile(saved_candidates, candidate_file)

            state_file = state_dir / "instagram_scan_state.json"
            report_file = state_dir / "latest_scan_report.json"
            command(
                sys.executable, "scripts/detect_instagram_menu.py",
                "--output", str(candidate_file), "--state", str(state_file),
                "--report", str(report_file), cwd=repo_dir, timeout=300,
            )
            command(
                sys.executable, "scripts/detect_instagram_menu.py", "--check",
                "--output", str(candidate_file), "--state", str(state_file), cwd=repo_dir,
            )
            report = read_json(report_file)
            if not discovery_healthy(report):
                return {
                    "status": "source-incomplete", "submitted": False,
                    "postsSeen": len(report.get("posts", [])),
                    "reason": "Source not fully available; no GitHub PR created",
                }
            candidates = read_json(candidate_file)
            if candidates.get("requiresReview") is not True or not candidates.get("posts"):
                raise ValueError("Missing or unsafe review-only candidate document")
            # A healthy scan is useful even when no new candidate is found.
            shutil.copyfile(candidate_file, saved_candidates)

            preview_path = state_dir / "latest_autopublish_preview.json"
            diff_path = state_dir / "latest_autopublish_diff.json"
            command(
                sys.executable, "scripts/instagram_autopublish.py",
                "--candidates", str(candidate_file), "--preview", str(preview_path),
                "--diff", str(diff_path), "--dry-run", cwd=repo_dir, timeout=300,
            )
            preview = read_json(preview_path)
            if preview.get("dryRunOnly") is not True or preview.get("productionAutoPublishEnabled") is not False:
                raise RuntimeError("Autopublish preview unexpectedly left dry-run mode")

            signature = candidate_signature(candidates)
            receipt_file = state_dir / "last_submitted.json"
            receipt = read_json(receipt_file) if receipt_file.exists() else {}
            summary = preview.get("summary", {})
            result = {
                "status": "preview-ready", "submitted": False,
                "postsSeen": len(report["posts"]), "candidateItems": summary.get("candidateItems", 0),
                "eligiblePreviewItems": summary.get("eligibleItems", 0),
                "quarantinedItems": summary.get("quarantinedItems", 0),
                "signature": signature, "branch": review_branch(signature),
                "previewPath": str(preview_path),
            }
            if receipt.get("signature") == signature:
                result["status"] = "already-submitted"
                return result
            if not submit:
                return result

            branch = review_branch(signature)
            prior_pr = existing_pr(branch)
            remote_doc = remote_branch_document(repo_dir, branch)
            if remote_doc is not None and candidate_signature(remote_doc) != signature:
                raise RuntimeError("Remote review branch has unexpected content; refusing to overwrite")
            if remote_doc is None:
                command("git", "switch", "-c", branch, cwd=repo_dir)
                command("git", "add", "--", str(CANDIDATES).replace("\\", "/"), cwd=repo_dir)
                staged = command("git", "diff", "--cached", "--quiet", cwd=repo_dir, check=False)
                if staged.returncode == 0:
                    result["status"] = "no-change-versus-experiment"
                    return result
                if staged.returncode != 1:
                    raise RuntimeError("Cannot determine staged candidate changes")
                command(
                    "git", "-c", "user.name=Sushiro local scan", "-c",
                    "user.email=sushiro-scan@users.noreply.github.com",
                    "commit", "-m", "chore: record local public Instagram candidates",
                    cwd=repo_dir,
                )
                command("git", "push", "--set-upstream", "origin", branch, cwd=repo_dir, timeout=120)

            if prior_pr is None:
                body = (
                    "Local public-Instagram scan, review-only. **Do not merge automatically.**\n\n"
                    f"- Scanned posts: {result['postsSeen']}\n"
                    f"- Candidate items: {result['candidateItems']}\n"
                    f"- Preview eligible: {result['eligiblePreviewItems']}\n"
                    f"- Preview quarantined: {result['quarantinedItems']}\n\n"
                    "Only data/instagram_candidates.json is submitted. "
                    "No promotions, menu data, executable files or credentials. "
                    "This is a best-effort public-HTML scan, not guaranteed exhaustive."
                )
                command(
                    "gh", "pr", "create", "--repo", REPO, "--draft",
                    "--base", BASE, "--head", branch,
                    "--title", "review: Windows public Instagram menu candidates",
                    "--body", body, cwd=repo_dir,
                )
                prior_pr = existing_pr(branch)
            if prior_pr is None:
                raise RuntimeError("Draft PR creation was not verifiable; submission not recorded")
            if not prior_pr.get("isDraft") and prior_pr.get("state") == "OPEN":
                raise RuntimeError("Candidate PR is unexpectedly not draft; no receipt recorded")
            atomic_json(receipt_file, {"signature": signature, "url": prior_pr.get("url"), "branch": branch})
            result.update(status="review-pr-created", submitted=True, prUrl=prior_pr.get("url"))
            return result


def main() -> int:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SushiroCounterInstagram"
    parser = argparse.ArgumentParser(description="Windows-friendly Instagram local scan -> optional Draft PR")
    parser.add_argument("--state-dir", type=Path, default=root)
    parser.add_argument("--submit-pr", action="store_true", help="Submit review candidates to an experimental Draft PR")
    args = parser.parse_args()
    try:
        result = execute(args.state_dir, submit=args.submit_pr)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Local Instagram scan stopped safely: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
