import json
import subprocess
import urllib.error
from unittest.mock import MagicMock

import pytest

from scripts import brew_utils


def test_get_pypi_sdist(mocker):
    mock_payload = {
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "url": "wheel_url",
                "digests": {"sha256": "wrong"},
            },
            {
                "packagetype": "sdist",
                "url": "https://sdist-url.tar.gz",
                "digests": {"sha256": "abc12345"},
            },
        ]
    }
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_payload).encode("utf-8")

    mock_urlopen = mocker.patch("urllib.request.urlopen")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    url, sha = brew_utils.get_pypi_sdist("markdownify", "0.11.0")

    assert url == "https://sdist-url.tar.gz"
    assert sha == "abc12345"


def test_get_pypi_sdist_http_error(mocker):
    mock_urlopen = mocker.patch("urllib.request.urlopen")
    mock_urlopen.side_effect = urllib.error.URLError("Not found")

    with pytest.raises(SystemExit, match="Failed to fetch PyPI metadata"):
        brew_utils.get_pypi_sdist("markdownify", "0.11.0")


def test_get_pypi_sdist_missing(mocker):
    mock_payload = {"urls": [{"packagetype": "bdist_wheel"}]}
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_payload).encode("utf-8")

    mock_urlopen = mocker.patch("urllib.request.urlopen")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    with pytest.raises(SystemExit, match="No sdist found"):
        brew_utils.get_pypi_sdist("markdownify", "0.11.0")


def test_run_cmd_success(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = MagicMock(stdout="success output\n")

    result = brew_utils.run_cmd(["echo", "hello"])
    assert result == "success output\n"
    mock_run.assert_called_once_with(
        ["echo", "hello"], capture_output=True, text=True, check=True, cwd=None
    )


def test_run_cmd_failure(mocker, capsys):
    mock_run = mocker.patch("subprocess.run")
    mock_run.side_effect = subprocess.CalledProcessError(
        1, ["false"], output="out", stderr="err"
    )

    with pytest.raises(subprocess.CalledProcessError):
        brew_utils.run_cmd(["false"])

    captured = capsys.readouterr()
    assert "Command failed: false" in captured.err
    assert "Stdout: out" in captured.err
    assert "Stderr: err" in captured.err


def test_splice_formula(tmp_path):
    formula_path = tmp_path / "formula.rb"
    formula_path.write_text(
        'class Test < Formula\n  url "old_url"\n  sha256 "old_sha"\n  # RESOURCE_BLOCK_START\n  # RESOURCE_BLOCK_END\nend',
        encoding="utf-8",
    )

    brew_utils.splice_formula(
        formula_path,
        "new_url",
        "new_sha",
        '  resource "dep" do\n    url "dep_url"\n    sha256 "dep_sha"\n  end',
    )

    content = formula_path.read_text(encoding="utf-8")
    assert 'url "new_url"' in content
    assert 'sha256 "new_sha"' in content
    assert 'resource "dep" do' in content


def test_get_caller_project_name_pep621(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pep621-pkg"\n', encoding="utf-8"
    )
    assert brew_utils.get_caller_project_name(tmp_path) == "pep621-pkg"


def test_get_caller_project_name_poetry(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "poetry-pkg"\n', encoding="utf-8"
    )
    assert brew_utils.get_caller_project_name(tmp_path) == "poetry-pkg"


def test_get_caller_project_name_flit(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.flit.metadata]\nmodule = "flit-pkg"\n', encoding="utf-8"
    )
    assert brew_utils.get_caller_project_name(tmp_path) == "flit-pkg"


def test_get_caller_project_name_missing_or_invalid(tmp_path):
    assert brew_utils.get_caller_project_name(tmp_path) is None

    (tmp_path / "pyproject.toml").write_text("invalid [ toml", encoding="utf-8")
    assert brew_utils.get_caller_project_name(tmp_path) is None


def test_resolve_and_validate_formula_full_inference(tmp_path):
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    (caller_dir / "pyproject.toml").write_text(
        '[project]\nname = "my-cli"\n', encoding="utf-8"
    )

    tap_dir = tmp_path / "tap"
    formula_dir = tap_dir / "Formula"
    formula_dir.mkdir(parents=True)
    formula_file = formula_dir / "my-cli.rb"
    formula_file.touch()

    pkg, formula, rel = brew_utils.resolve_and_validate_formula(
        caller_dir=caller_dir,
        tap_dir=tap_dir,
    )

    assert pkg == "my-cli"
    assert formula == formula_file.resolve()
    assert rel == "Formula/my-cli.rb"


def test_resolve_and_validate_formula_package_mismatch(tmp_path):
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    (caller_dir / "pyproject.toml").write_text(
        '[project]\nname = "actual-cli"\n', encoding="utf-8"
    )

    with pytest.raises(SystemExit) as exc_info:
        brew_utils.resolve_and_validate_formula(
            caller_dir=caller_dir,
            package_name="wrong-cli",
        )

    err = str(exc_info.value)
    assert "Package name mismatch!" in err
    assert "Caller repository defines project: 'actual-cli'" in err
    assert "Workflow input 'package_name' was:  'wrong-cli'" in err


def test_resolve_and_validate_formula_custom_formula_path(tmp_path):
    formula_path = tmp_path / "other-cli.rb"
    formula_path.touch()

    pkg, formula, rel = brew_utils.resolve_and_validate_formula(
        formula_path=formula_path,
        package_name="my-cli",
    )

    assert pkg == "my-cli"
    assert formula == formula_path.resolve()
    assert rel == str(formula_path)


def test_resolve_and_validate_formula_could_not_determine(tmp_path):
    caller_dir = tmp_path / "empty_caller"
    caller_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        brew_utils.resolve_and_validate_formula(caller_dir=caller_dir)

    assert "Could not determine package name!" in str(exc_info.value)


def test_resolve_and_validate_formula_not_found(tmp_path):
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    (caller_dir / "pyproject.toml").write_text(
        '[project]\nname = "my-cli"\n', encoding="utf-8"
    )

    tap_dir = tmp_path / "tap"
    tap_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        brew_utils.resolve_and_validate_formula(
            caller_dir=caller_dir,
            tap_dir=tap_dir,
        )

    err = str(exc_info.value)
    assert "Formula not found" in err
    assert "Formula/my-cli.rb" in err
