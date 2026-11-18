import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "saas_bump.py"
spec = importlib.util.spec_from_file_location("saas_bump", SCRIPT)
saas_bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(saas_bump)

REQUIREMENTS = """# Email
# mailko[django]==0.0.9 is commented out and never touched
mailko[django]==0.1.1  # transactional email
requests==2.32.0
"""


def test_moves_the_pin_and_keeps_the_rest():
    text, previous = saas_bump.bump_pin(REQUIREMENTS, "v0.2.0")
    assert previous == "v0.1.1"
    assert "mailko[django]==0.2.0  # transactional email" in text
    assert "# mailko[django]==0.0.9 is commented out" in text
    assert "requests==2.32.0" in text


def test_a_pin_without_extras_and_with_markers():
    text, _ = saas_bump.bump_pin("mailko==0.1.0; python_version >= '3.10'\n", "v0.1.1")
    assert text == "mailko==0.1.1; python_version >= '3.10'\n"


@pytest.mark.parametrize("tag", ["v0.1.1", "v0.1.0"])
def test_nothing_to_do_when_there_or_newer(tag):
    assert saas_bump.bump_pin(REQUIREMENTS, tag) == (REQUIREMENTS, None)


def test_says_so_when_the_pin_is_gone_or_hashed():
    with pytest.raises(saas_bump.NoPinError):
        saas_bump.bump_pin("mailko-other==1.0.0\nmailkoo==1.0.0\n", "v0.2.0")
    with pytest.raises(saas_bump.HashPinnedError):
        saas_bump.bump_pin("mailko==0.1.0 \\\n    --hash=sha256:abc\n", "v0.2.0")
    with pytest.raises(ValueError):
        saas_bump.bump_pin(REQUIREMENTS, "0.2.0")


def test_the_cli_exit_codes(tmp_path, capsys):
    path = tmp_path / "base.txt"
    path.write_text(REQUIREMENTS, newline="")
    assert saas_bump.main(["x", "pin", str(path), "v0.2.0"]) == 0
    assert capsys.readouterr().out.strip() == "v0.1.1"
    assert saas_bump.main(["x", "pin", str(path), "v0.2.0"]) == saas_bump.UNCHANGED
    path.write_text("django==5.2\n")
    assert saas_bump.main(["x", "pin", str(path), "v0.2.0"]) == saas_bump.NO_PIN


def test_release_notes_are_the_versions_section():
    changelog = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text()
    notes = saas_bump.release_notes(changelog, "v0.1.1")
    assert "ConfigurationError" in notes
    assert "## [0.1.0]" not in notes
    assert saas_bump.release_notes(changelog, "v9.9.9") == ""


def test_another_package_with_a_hash_is_not_ours():
    text = "mailko-otro==1.0 --hash=sha256:abc\nmailko[django]==0.1.0\n"
    new_text, previous = saas_bump.bump_pin(text, "v0.2.0")
    assert previous == "v0.1.0"
    assert "mailko-otro==1.0 --hash=sha256:abc" in new_text
