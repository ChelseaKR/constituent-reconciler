"""The version this project declares, held to the releases that exist.

`pyproject.toml` declares `0.8.0`. `git tag -l` prints nothing: no `v*` tag has
ever been cut, `release.yml` has never fired, and there is no GitHub Release
and no PyPI record. A pre-release project is entitled to a version number that
no artifact carries — that is what a version under development is — but every
*other* place the number is restated then has to say so, and two of them did
not:

* `CITATION.cff` carried `date-released: "2026-09-02"`. Nothing was released on
  that date. GitHub renders that field in its "Cite this repository" panel and
  Zenodo reads it on import, so the claim reached readers who never see the
  README that contradicts it.
* The README's Status line said `Beta (v0.7)` while the manifest, the changelog
  and the citation file had all moved to 0.8.0.

So the checks here are the three questions worth asking of a declared version,
answered against the repository rather than against another copy of the number:

1. does any tag carry it, and if none does, does the repository say so where a
   reader sees it;
2. does every restatement of it agree with `pyproject.toml`;
3. is there a changelog section behind it.

A missing tag and an unfetched tag are indistinguishable from inside a
checkout, so nothing below concludes "no tag exists" from a checkout that would
not have shown one — see `_why_tags_are_unreadable`. Reading an empty tag list
out of a shallow clone and calling it evidence is absence rendered as a value.
`test_ci_fetches_the_tags_these_checks_read` is the other half of that: without
it these checks would skip in the one run that gates a merge.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CITATION = ROOT / "CITATION.cff"
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: The top-level ``version:`` and ``date-released:`` of the citation file.
#: Matched line by line rather than parsed, because a YAML parser is not a
#: dependency of this project and each field is one line.
CITATION_VERSION = re.compile(r'^version:\s*"?([^"\s#]+)"?\s*$', re.MULTILINE)
CITATION_DATE = re.compile(r'^date-released:\s*"?(\d{4}-\d{2}-\d{2})"?\s*$', re.MULTILINE)

#: A release tag, with or without the ``v``. Anything else under ``refs/tags``
#: is not a release and is not counted as one.
RELEASE_TAG = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+.].+)?)$")

#: The blockquoted ``> **Status: ...**`` paragraph under the title, which is
#: where a reader who reads nothing else learns what this is.
README_STATUS = re.compile(r"^> \*\*Status:(.*?)\*\*", re.MULTILINE | re.DOTALL)

#: What the README says about tags today, pinned so that cutting one makes this
#: sentence false loudly rather than quietly.
README_SAYS_NO_TAG = "No `v*` tag has been cut yet"


def _manifest_version() -> str:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    version = manifest["project"]["version"]
    assert isinstance(version, str)
    return version


def _git(*args: str) -> str | None:
    """Run git in the checkout. ``None`` means the answer is unavailable."""
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, resolved path, no shell
            [executable, "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError:  # pragma: no cover - git present but unusable
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def _why_tags_are_unreadable() -> str | None:
    """Why an empty tag list here would prove nothing, or ``None`` if it proves something."""
    if not (ROOT / ".git").exists():
        return f"no .git in {ROOT}: an installed tree carries no tags to read"
    if shutil.which("git") is None:
        return "no git executable on PATH"
    if _git("rev-parse", "--is-inside-work-tree") != "true":
        return "not a git work tree"
    if _git("rev-parse", "--is-shallow-repository") == "true":
        return "shallow checkout: tags are not fetched, so an empty tag list is not evidence"
    if (_git("config", "--get", "remote.origin.tagOpt") or "") == "--no-tags":
        return "clone configured with tagOpt=--no-tags, so tags were never fetched"
    return None


def _release_tags() -> list[str]:
    """Release tags, newest first."""
    listed = _git("tag", "--list", "--sort=-v:refname") or ""
    return [tag for tag in listed.splitlines() if RELEASE_TAG.match(tag.strip())]


def _tag_version(tag: str) -> str:
    matched = RELEASE_TAG.match(tag)
    assert matched is not None, tag
    return matched.group(1)


def _require_readable_tags() -> list[str]:
    reason = _why_tags_are_unreadable()
    if reason is not None:
        pytest.skip(f"cannot measure the repository's tags: {reason}")
    return _release_tags()


def test_the_declared_version_is_held_to_the_tags_that_exist() -> None:
    """No tag is a legitimate state. Not saying so is not.

    Nothing here demands a tag: this project is pre-release on purpose and the
    release workflow has never been exercised. What it demands is that the
    repository state which of the two situations it is in — no tag at all, or a
    declared version that ran ahead of the newest one — where a reader looking
    for something to install will see it.
    """
    tags = _require_readable_tags()
    declared = _manifest_version()
    readme = README.read_text(encoding="utf-8")

    if not tags:
        assert README_SAYS_NO_TAG in readme, (
            f"pyproject.toml declares {declared} and this repository has no release tag, "
            f"so there is no artifact carrying that version, and README.md no longer says "
            f"so ({README_SAYS_NO_TAG!r} is gone). A reader is left to assume a release."
        )
        return

    newest = tags[0]
    assert README_SAYS_NO_TAG not in readme, (
        f"README.md still says {README_SAYS_NO_TAG!r}, and {newest} exists"
    )
    assert declared in {_tag_version(tag) for tag in tags}, (
        f"pyproject.toml declares {declared} and no tag carries it. Newest tag: {newest}. "
        f"Tags: {', '.join(tags)}. Either the declared version is unreleased and the "
        f"README has to say so, or the tag is missing."
    )


def test_the_status_line_names_the_version_the_project_is_at() -> None:
    """The one line a reader takes the project's maturity from has to be current.

    It said `Beta (v0.7)` for the whole of 0.8.0.
    """
    declared = _manifest_version()
    status_match = README_STATUS.search(README.read_text(encoding="utf-8"))
    assert status_match is not None, "README.md has no `> **Status: ...**` line"
    status = " ".join(status_match.group(1).split())
    assert declared in status, (
        f"pyproject.toml declares {declared} and the README's Status line says {status!r}"
    )


def test_every_restatement_of_the_version_agrees_with_the_manifest() -> None:
    """One source of truth, and the copies of it derived or checked, never asserted."""
    declared = _manifest_version()

    # `constituent_reconciler.__version__` is read from installed metadata
    # rather than written down (REL-02), so this catches an environment
    # installed from a different manifest, not a hand-edited literal.
    assert metadata.version("constituent-reconciler") == declared

    cited = CITATION_VERSION.findall(CITATION.read_text(encoding="utf-8"))
    assert cited == [declared], f"CITATION.cff states version {cited}, pyproject.toml {declared}"

    heading = f"## [{declared}] - "
    assert heading in CHANGELOG.read_text(encoding="utf-8"), (
        f"CHANGELOG.md has no {heading!r} section, so the declared version has no record "
        f"of what is in it"
    )


def test_the_citation_dates_no_release_that_was_never_cut() -> None:
    """`date-released` is read by GitHub's citation panel and by Zenodo, not by a reader.

    It carried 2026-09-02 while `git tag -l` printed nothing, so a stranger
    citing this project cited a release that does not exist, and the README
    sentence that says no tag has been cut was nowhere near the claim.
    """
    tags = _require_readable_tags()
    dated = CITATION_DATE.findall(CITATION.read_text(encoding="utf-8"))
    if not tags:
        assert not dated, (
            f"CITATION.cff carries date-released {dated[0]!r} and this repository has no "
            f"release tag, so it dates a release that was never cut"
        )
        return
    assert dated, f"CITATION.cff carries no date-released and {tags[0]} exists"


def _jobs(workflow: str) -> dict[str, str]:
    """Split a workflow into its jobs. No YAML parser is a dependency here."""
    lines = workflow.splitlines()
    try:
        first = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:  # pragma: no cover - a workflow with no jobs
        return {}
    starts = [
        (index, matched.group(1))
        for index in range(first + 1, len(lines))
        if (matched := re.match(r"^  ([A-Za-z0-9_-]+):\s*$", lines[index])) is not None
    ]
    bounds = [*[index for index, _ in starts], len(lines)]
    return {name: "\n".join(lines[bounds[n] : bounds[n + 1]]) for n, (_, name) in enumerate(starts)}


def _runs(body: str, command: str) -> bool:
    """Does this job actually run the command, rather than mention it?

    Comments are prose. A job that only named the gate in a comment explaining
    itself was pulled into this check's scope by a raw text match.
    """
    lines = body.splitlines()
    return any(command in line for line in lines if not line.lstrip().startswith("#"))


def test_ci_fetches_the_tags_these_checks_read() -> None:
    """Otherwise the tag checks skip in CI and gate nothing.

    `actions/checkout` fetches one commit and no tags by default, which is
    exactly the shape `_why_tags_are_unreadable` refuses to draw a conclusion
    from. The job that runs `make verify` has to ask for the tags.
    """
    jobs = _jobs(CI_WORKFLOW.read_text(encoding="utf-8"))
    running = {name: body for name, body in jobs.items() if _runs(body, "make verify")}
    assert running, ".github/workflows/ci.yml has no job that runs `make verify`"
    for name, body in running.items():
        assert "actions/checkout" in body, f"job {name!r} runs make verify without a checkout"
        assert "fetch-depth: 0" in body, (
            f"job {name!r} checks out shallow, so tests/test_release_versions.py skips there"
        )
        assert "fetch-tags: true" in body, (
            f"job {name!r} does not fetch tags, so tests/test_release_versions.py skips there"
        )


#: A ``git+<url>@<ref>`` install pin, capturing the ref it pins to. This is the
#: form the README and the package docstrings use to tell someone how to
#: install without cloning, so the ref has to be one that resolves.
GIT_PIN = re.compile(r"git\+https?://[^\s`'\"]+?@([A-Za-z0-9._-]+)")

#: Which of those pinned refs are claims about a *release*. ``@main`` is a
#: branch and always resolves; a commit SHA is checked by whoever wrote it.
#: A version-shaped ref is a claim that a tag of that name was cut.
VERSION_REF = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+.].+)?$")

#: Where an install instruction can reach a reader from.
DOCUMENTED = ("README.md", "src/constituent_reconciler/demo.py")


def test_no_documented_install_pins_a_tag_that_does_not_exist() -> None:
    """An install command naming a tag nobody cut is absence rendered as a value.

    ``demo.py`` explained itself with ``uvx --from git+...@v0.8.0``, and the
    README told a reader that "the 0.8.0 wheel predates `reconcile demo`; with
    that tag, clone the repository". Neither artifact exists: `git tag -l`
    prints nothing. Someone following either instruction does not get the
    documented behaviour and then a helpful error — they get
    ``Could not find a version that satisfies`` from a ref that was never
    created, several steps before the sentence they were reading applies.

    The pin is the falsifiable half of that class, so it is what is checked
    here: every version-shaped ref in a documented install command has to name
    a tag this repository actually carries. ``@main`` and commit SHAs are not
    release claims and are out of scope.
    """
    tags = _require_readable_tags()
    carried = {tag.strip() for tag in tags} | {_tag_version(tag) for tag in tags}

    phantom: list[str] = []
    for relative in DOCUMENTED:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for ref in GIT_PIN.findall(text):
            if VERSION_REF.match(ref) and ref not in carried:
                phantom.append(f"{relative}: git+...@{ref}")

    assert not phantom, (
        "these documented install commands pin a tag that does not exist "
        f"({', '.join(phantom)}). Tags carried: {', '.join(tags) or 'none'}. "
        "Either cut the tag or stop telling a reader to install it."
    )
