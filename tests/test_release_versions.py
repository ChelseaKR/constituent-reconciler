"""The version this project declares, held to the releases that exist.

A pre-release project is entitled to a version number no artifact carries --
that is what a version under development is -- and it stays entitled to it
right up until a tag exists. What both worlds require is that every *other*
place the number is restated says which world it is in, and this module is
where that is enforced rather than trusted.

The failures it was written from, each of which reached a reader:

* `CITATION.cff` carried `date-released: "2026-09-02"` on a day nothing was
  released. GitHub renders that field in its "Cite this repository" panel and
  Zenodo reads it on import, so the claim travelled where the README that
  contradicted it does not.
* The README's Status line said `Beta (v0.7)` while the manifest, the changelog
  and the citation file had all moved to 0.8.0.
* Prose stating in the present tense that this repository had never released
  anything survived v0.9.0 in three files at once, including this docstring.
  Each had been checked against the code and none against the repository.

So the checks here are the four questions worth asking of a declared version,
answered against the repository rather than against another copy of the number:

1. does any tag carry it, and if none does, does the repository say so where a
   reader sees it;
2. does every restatement of it agree with `pyproject.toml`;
3. is there a changelog section behind it;
4. once a tag exists, has every sentence that said none did been retired.

A missing tag and an unfetched tag are indistinguishable from inside a
checkout, so nothing below concludes anything from a checkout that would not
have shown a tag -- see `_why_tags_are_unreadable`. Reading an empty tag list
out of a shallow clone and calling it evidence is absence rendered as a value.
`test_ci_fetches_the_tags_these_checks_read` is the other half of that: without
it these checks would skip in the one run that gates a merge.
"""

from __future__ import annotations

import ast
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


#: Sentences in this tree that assert, in the present tense, that this
#: repository has never released anything. Every one is verbatim from a file
#: here, and every one was true when it was written.
#:
#: They are matched case-insensitively as normalized substrings, which makes
#: this a denylist, with a denylist's one guarantee: it finds a phrasing
#: somebody has already written here, and it cannot find one nobody has thought
#: of yet. That is the whole of what it claims. The structural half of this
#: question is `test_the_citation_dates_no_release_that_was_never_cut`, which
#: compares a field against the repository's tags and needs no vocabulary at
#: all; this check exists because the same fact is *also* stated in prose, in
#: several files, and prose is where it outlived the release.
#:
#: The way this list fails is by being narrower than the repository's own
#: prose, so `test_every_claim_in_the_vocabulary_is_a_sentence_this_repository_wrote`
#: holds every entry to the tracked tree. An entry nothing says reads as
#: coverage while covering nothing.
#:
#: Entries carry no backtick, because `_normalized` strips them: the same
#: sentence appears here with Markdown single backticks and with reStructuredText
#: double backticks, and an entry spelled one way cannot see the other.
#:
#: What is deliberately absent: an instruction such as the one a runbook gives
#: for cutting a first tag. A denylist cannot tell an instruction from an
#: assertion, and a procedure that says how to release is correct prose on the
#: day a release happens. Only sentences asserting the repository's state
#: belong here.
CLAIMS_OF_NO_RELEASE: tuple[str, ...] = (
    "no v* tag has been cut",
    "no v* tag has ever been cut",
    "git tag -l prints nothing",
    "release.yml has never fired",
    "there is no GitHub Release",
    "no release has been tagged",
    "blocked on the first v* tag",
)

#: Suffixes worth reading. A binary, a lockfile or a fixture does not carry a
#: sentence a reader takes a fact from.
PROSE_SUFFIXES = frozenset({".md", ".cff", ".py", ".toml", ".yml", ".yaml", ".txt"})

#: Files exempt from the staleness scan because each is a dated record of what
#: was true on a particular day rather than a claim about today. Rewriting one
#: so a past sentence reads true now would destroy the thing the scan protects.
#: `CHANGELOG.md` holds a dated section per release;
#: `docs/audits/scorecard-2026-07.md` says of itself that it is a dated
#: snapshot and asks for the next posture to be committed under a new date.
#:
#: They are exempt from the *staleness* scan only. Both stay in the observation
#: universe below, because a phrasing preserved in a dated record is a phrasing
#: this project wrote, and that is where a retired one goes on being covered
#: after the live sentence is corrected.
#:
#: `tests/test_release_versions.py` is exempt as a *file* because the tuple
#: above puts every claim in it verbatim. Its docstrings are read instead, all
#: of them: reading `__doc__` alone covered one paragraph out of the module's
#: many, and the sentence this correction was written from sat in a function
#: docstring rather than the module one.
CLAIM_SCAN_EXEMPT = frozenset({"CHANGELOG.md", "docs/audits/scorecard-2026-07.md"})

THIS_FILE = Path(__file__).resolve()

#: Below this the docstring walk has stopped reading the file. An empty list is
#: what a parse that found nothing returns, and it passes every check
#: downstream.
MIN_DOCSTRINGS_IN_THIS_MODULE = 12

#: Markdown and Python both wrap prose, and a wrapped claim is invisible to a
#: plain substring match. Stripping line markers, dropping backticks and
#: collapsing whitespace turns "this file contains this sentence" into a
#: question about the sentence rather than about where the formatter put the
#: newline. Measured here: every sentence this scan was missing is wrapped, and
#: one is wrapped inside a reStructuredText docstring behind double backticks.
_LINE_MARKERS = re.compile(r"^\s*(?:[>#*\-]|//)*\s*", re.MULTILINE)


def _tracked_prose_files() -> list[Path]:
    """Every tracked file whose text a reader could take a fact from."""
    listed = _git("ls-files", "-z")
    if listed is None:  # pragma: no cover - git unusable; the caller skips first
        return []
    paths: list[Path] = []
    for name in listed.split("\0"):
        if not name or name in CLAIM_SCAN_EXEMPT:
            continue
        path = ROOT / name
        if path.suffix in PROSE_SUFFIXES and path.is_file():
            paths.append(path)
    return paths


def _normalized(text: str) -> str:
    """Line markers and backticks dropped, whitespace collapsed, lowercased."""
    return re.sub(r"\s+", " ", _LINE_MARKERS.sub(" ", text).replace("`", "")).lower()


def _claims_in(text: str) -> list[str]:
    normalized = _normalized(text)
    return [claim for claim in CLAIMS_OF_NO_RELEASE if _normalized(claim) in normalized]


def _own_docstrings() -> list[str]:
    """Every docstring in this module: the prose of the one file the scan skips.

    The module docstring is one of many. The rest are function docstrings,
    which is prose in exactly the sense this check is about: nothing reads it,
    so nothing corrects it. Parsing the source rather than walking the module
    object keeps this measuring the file on disk, which is what the scan around
    it measures.
    """
    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                found.append(text)
    return found


def _prose_of(path: Path) -> list[str]:
    """The prose of one tracked file: its text, or its docstrings if it is this one."""
    if path.resolve() == THIS_FILE:
        return _own_docstrings()
    return [path.read_text(encoding="utf-8")]


def _claims_this_repository_has_written() -> dict[str, list[str]]:
    """Where each vocabulary entry is written, over the whole tracked tree.

    The dated records are included even though the staleness scan skips them,
    and this module is read through its docstrings for the same reason the scan
    reads it that way: the tuple puts every entry in the file verbatim, so
    reading the file would make every entry vouch for itself.
    """
    where: dict[str, list[str]] = {claim: [] for claim in CLAIMS_OF_NO_RELEASE}
    universe = [*_tracked_prose_files(), *(ROOT / name for name in sorted(CLAIM_SCAN_EXEMPT))]
    for path in universe:
        if not path.is_file():  # pragma: no cover - an exempt name that left the tree
            continue
        for text in _prose_of(path):
            for claim in _claims_in(text):
                where[claim].append(str(path.relative_to(ROOT)))
    return where


def test_the_claim_vocabulary_is_real_and_not_self_matching() -> None:
    """The floor under the scan below, and the reason it may read its own docstrings.

    Four ways the next check could pass while examining nothing: an empty claim
    list, a claim list nothing in the tree has ever said, a normalization that
    has stopped finding a wrapped sentence, and a docstring walk that has
    stopped finding this file. The README's own sentence is in the vocabulary
    by construction, so at least one entry is a sentence this project really
    wrote; and no docstring in this module states one, which is what makes
    reading them a measurement rather than a way of exempting the file.
    """
    assert CLAIMS_OF_NO_RELEASE, "an empty claim list scans every file and finds nothing"
    assert _claims_in(README_SAYS_NO_TAG), (
        "the vocabulary does not cover the one sentence this repository already pins "
        "in both directions, so it is not a generalisation of anything"
    )
    own = _own_docstrings()
    assert len(own) >= MIN_DOCSTRINGS_IN_THIS_MODULE, (
        f"only {len(own)} docstring(s) parsed out of this module: the walk has stopped "
        "reading the file, and no docstring is what it returns either way"
    )
    for text in own:
        assert not _claims_in(text), (
            "a docstring in this module states, in the present tense, that nothing has "
            f"been released: {_claims_in(text)}. This file is exempt as a file, so "
            "nothing else reads it, and a docstring is the one piece of prose in a "
            "Python project that nobody opens. Describe the rule, not the day.\n"
            f"{' '.join(text.split())[:400]}"
        )
    assert _claims_in("# no release\n# has been tagged"), (
        "a claim wrapped across two commented lines is not found, so the normalization "
        "this scan depends on has stopped working. Every sentence it was missing was "
        "wrapped, and none of them is reachable without it"
    )
    assert _claims_in("``no release has been tagged``"), (
        "a claim written with reStructuredText double backticks is not found, so the "
        "backtick stripping has stopped working. The package docstrings use that form "
        "and the Markdown documents use the single-backtick one"
    )


def test_every_claim_in_the_vocabulary_is_a_sentence_this_repository_wrote() -> None:
    """A denylist entry that matches nothing is coverage that is not there.

    The way this list fails is by being narrower than the repository's prose,
    and nothing was measuring the gap. Requiring every entry to be observed
    somewhere tracked is the self-limiting refusal that makes the list's own
    coverage falsifiable: an entry stops earning its place the moment nothing
    says it, and this fails until somebody deletes it or corrects the wording.

    The dated records count as an observation even though the staleness scan
    skips them. That is deliberate and it is where a retired phrasing lives: a
    sentence corrected out of the tree is still a sentence this project once
    wrote, and keeping its wording covered is the only thing that catches it
    being written again.
    """
    _require_readable_tags()
    where = _claims_this_repository_has_written()
    unobserved = sorted(claim for claim, files in where.items() if not files)
    assert not unobserved, (
        f"these claim-vocabulary entries appear nowhere in this repository: {unobserved}. "
        "A denylist entry that matches nothing is not coverage; it reads as coverage. "
        "Either the sentence has been corrected and its wording is preserved nowhere -- "
        "record it in CHANGELOG.md or delete the entry -- or the wording here is not the "
        "wording the tree uses, in which case the real sentence is going unwatched."
    )


def test_no_document_says_this_repository_is_untagged_once_it_is() -> None:
    """A sentence saying nothing was ever released has to go when something is.

    `README_SAYS_NO_TAG` was pinned in both directions -- required while no tag
    exists, refused once one does -- and pinned *in one file*. The identical
    sentence sat in `docs/RESPONSIBLE-TECH-AUDITS.md`, a differently worded one
    in `CITATION.cff`'s header comment directly above a populated
    `date-released`, and a third in this module's own docstring. Cutting
    v0.9.0 made all three false at once and nothing said so, because each was
    checked against the code and never against the repository.

    This is the same two-directional rule applied to every tracked file. The
    other direction -- that *something* must say so while no tag exists -- is
    `test_the_declared_version_is_held_to_the_tags_that_exist`, which is why
    this half only runs once a tag exists.
    """
    tags = _require_readable_tags()
    scanned = _tracked_prose_files()
    assert len(scanned) > 20, (
        f"only {len(scanned)} file(s) to read: this scan has stopped finding the tree, "
        f"and a scan that reads nothing reports the same clean result as one that read "
        f"everything"
    )
    if not tags:
        return

    stale: list[str] = []
    for path in scanned:
        for text in _prose_of(path):
            for claim in _claims_in(text):
                stale.append(f"{path.relative_to(ROOT)}: {claim!r}")

    assert not stale, (
        f"{tags[0]} exists, and these still say nothing has ever been released: "
        f"{'; '.join(stale)}. Tags carried: {', '.join(tags)}. Either the sentence is "
        f"stale or the tag should not be there."
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
    that tag, clone the repository". Neither artifact was ever created, and the
    repository carried no tag at all when both sentences were written. Someone
    following either instruction does not get the documented behaviour and then
    a helpful error: they get ``Could not find a version that satisfies`` from a
    ref that does not resolve, several steps before the sentence they were
    reading applies.

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
