"""The two steps of ``.github/workflows/saas-bump.yml`` that read and write
files: move a SaaS's pin of this library to a version just published on
PyPI, and pull that version's notes out of the CHANGELOG for the PR.

    python3 scripts/saas_bump.py pin <requirements file> <tag>
        rewrites every ``mailko[<extras>]==X.Y.Z`` to the tag's version and
        prints the version it had (``vX.Y.Z``). Exit 3 when there is nothing
        to do (already there, or already on a newer version); exit 4 when the
        file has no pin of this library (the line changed shape: the workflow
        fails so somebody hears of it); exit 5 when the pin carries a
        ``--hash`` (a new version needs its own, by hand).
    python3 scripts/saas_bump.py notes <CHANGELOG.md> <tag>
        prints the section of ``<tag>`` (``v0.2.0`` or ``0.2.0``).

Standard library only: it runs on a bare runner.
"""

# Standard library
import re
import sys

#: A requirement line (never a comment) that pins this library from PyPI:
#: ``mailko==0.1.1`` or ``mailko[django]==0.1.1``.
PIN = re.compile(
    r"^(?P<name>mailko(?:\[[^\]\n]*\])?)\s*==\s*(?P<version>\d+\.\d+\.\d+)(?=[\s;#\\]|$)",
    re.M | re.I,
)
TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
#: A pin checked by hash, on its line or a ``\`` continuation.
HASHED = re.compile(r"^mailko(?:\[[^\]\n]*\])?\s*==[^\n]*(?:\\\n[^\n]*)*--hash", re.M | re.I)
UNCHANGED = 3
NO_PIN = 4
HASH_PINNED = 5


class NoPinError(Exception):
    """The file pins this library nowhere (or in a shape not known)."""


class HashPinnedError(Exception):
    """The pin carries a ``--hash``: a new version needs its own, by hand."""


def _version(tag):
    found = TAG.match(tag or "")
    return tuple(int(part) for part in found.groups()) if found else None


def bump_pin(text, tag):
    """``(new text, the version it had)``. ``previous`` is ``None`` when
    there is nothing to do: every pin is already that version, or one is on
    a NEWER one (a hotfix of an older line never takes a SaaS back)."""
    version = _version(tag)
    if version is None or not tag.startswith("v"):
        raise ValueError(f"Not a release tag: {tag!r}")
    number = tag.removeprefix("v")
    pins = list(PIN.finditer(text))
    if not pins:
        raise NoPinError()
    if HASHED.search(text):
        raise HashPinnedError()
    if any(_version(match["version"]) > version for match in pins):
        return text, None
    stale = [match for match in pins if match["version"] != number]
    if not stale:
        return text, None
    new_text = PIN.sub(lambda match: f"{match['name']}=={number}", text)
    return new_text, f"v{stale[0]['version']}"


#: A CHANGELOG's link reference (``[0.2.0]: https://…``): not notes.
LINK_REFERENCE = re.compile(r"^\[[^\]]+\]:\s+\S+\s*$", re.M)


def release_notes(changelog, tag):
    """The body of ``## [<version>]`` in the CHANGELOG, or ``""``."""
    version = tag.removeprefix("v")
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*\n", re.M)
    found = heading.search(changelog)
    if found is None:
        return ""
    rest = changelog[found.end() :]
    following = re.search(r"^## \[", rest, re.M)
    section = rest[: following.start()] if following else rest
    return LINK_REFERENCE.sub("", section).strip()


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if (command, len(argv)) not in (("pin", 4), ("notes", 4)):
        print(__doc__, file=sys.stderr)
        return 2
    path, tag = argv[2], argv[3]
    # ``newline=""``: a file with CRLF keeps them (a whole-file diff otherwise).
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    if command == "notes":
        print(release_notes(text, tag))
        return 0
    try:
        new_text, previous = bump_pin(text, tag)
    except NoPinError:
        print(f"{path} does not pin mailko as mailko[...]==X.Y.Z", file=sys.stderr)
        return NO_PIN
    except HashPinnedError:
        print(f"{path} pins mailko with --hash: update it by hand", file=sys.stderr)
        return HASH_PINNED
    if previous is None:
        return UNCHANGED
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
    print(previous)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
