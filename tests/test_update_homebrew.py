import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import update_homebrew


def test_get_pypi_metadata_success(mocker):
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"info": "test"}).encode("utf-8")

    mock_urlopen = mocker.patch("urllib.request.urlopen")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    result = update_homebrew.get_pypi_metadata(
        "protostar", "0.1.0", max_retries=1, delay=0
    )
    assert result == {"info": "test"}


def test_get_pypi_metadata_timeout(mocker):
    mock_urlopen = mocker.patch("urllib.request.urlopen")
    import email.message

    mock_urlopen.side_effect = urllib.error.HTTPError(
        "url", 404, "Not Found", email.message.Message(), None
    )

    with pytest.raises(TimeoutError, match="Timed out waiting"):
        update_homebrew.get_pypi_metadata("protostar", "0.1.0", max_retries=1, delay=0)


def test_extract_sdist_info_success():
    metadata = {
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "url": "wheel_url",
                "digests": {"sha256": "wrong"},
            },
            {
                "packagetype": "sdist",
                "url": "sdist_url",
                "digests": {"sha256": "correct"},
            },
        ]
    }
    url, sha = update_homebrew.extract_sdist_info(metadata)
    assert url == "sdist_url"
    assert sha == "correct"


def test_extract_sdist_info_failure():
    metadata = {"urls": [{"packagetype": "bdist_wheel"}]}
    with pytest.raises(ValueError, match="sdist information not found"):
        update_homebrew.extract_sdist_info(metadata)


def test_main_missing_formula(mocker, tmp_path):
    mocker.patch(
        "sys.argv",
        [
            "update_homebrew.py",
            "--version",
            "0.1.0",
            "--formula-path",
            str(tmp_path / "missing.rb"),
        ],
    )
    with pytest.raises(SystemExit):
        update_homebrew.main()


def test_main_happy_path(mocker, tmp_path):
    formula_path = tmp_path / "testpkg.rb"
    formula_path.write_text("class Test < Formula\nend", encoding="utf-8")

    mocker.patch(
        "sys.argv",
        [
            "update_homebrew.py",
            "--version",
            "v0.1.0",
            "--formula-path",
            str(formula_path),
            "--package",
            "testpkg",
        ],
    )

    mocker.patch(
        "scripts.update_homebrew.get_pypi_metadata",
        return_value={
            "urls": [
                {"packagetype": "sdist", "url": "url", "digests": {"sha256": "sha"}}
            ]
        },
    )

    def mock_run_cmd(args, cwd=None):
        if "compile" in args:
            output_file = Path(args[args.index("-o") + 1])
            output_file.write_text("dep==1.0.0\ntestpkg==0.1.0\n", encoding="utf-8")
        return ""

    mocker.patch("scripts.update_homebrew.run_cmd", side_effect=mock_run_cmd)

    mocker.patch(
        "scripts.update_homebrew.get_pypi_sdist", return_value=("dep_url", "dep_sha")
    )

    mock_splice = mocker.patch("scripts.update_homebrew.splice_formula")

    update_homebrew.main()

    mock_splice.assert_called_once()
    args, _ = mock_splice.call_args
    assert args[0] == formula_path
    assert args[1] == "url"
    assert args[2] == "sha"
    assert 'resource "dep" do' in args[3]
    assert 'url "dep_url"' in args[3]
    assert 'sha256 "dep_sha"' in args[3]


def test_main_inferred_from_pyproject(mocker, tmp_path):
    caller_dir = tmp_path / "caller_repo"
    caller_dir.mkdir()
    (caller_dir / "pyproject.toml").write_text(
        '[project]\nname = "my-tool"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    tap_dir = tmp_path / "tap_repo"
    formula_dir = tap_dir / "Formula"
    formula_dir.mkdir(parents=True)
    formula_path = formula_dir / "my-tool.rb"
    formula_path.write_text("class MyTool < Formula\nend", encoding="utf-8")

    mocker.patch(
        "sys.argv",
        [
            "update_homebrew.py",
            "--version",
            "0.1.0",
            "--caller-dir",
            str(caller_dir),
            "--tap-dir",
            str(tap_dir),
        ],
    )

    mocker.patch(
        "scripts.update_homebrew.get_pypi_metadata",
        return_value={
            "urls": [
                {"packagetype": "sdist", "url": "url", "digests": {"sha256": "sha"}}
            ]
        },
    )

    def mock_run_cmd(args, cwd=None):
        if "compile" in args:
            output_file = Path(args[args.index("-o") + 1])
            output_file.write_text("my-tool==0.1.0\n", encoding="utf-8")
        return ""

    mocker.patch("scripts.update_homebrew.run_cmd", side_effect=mock_run_cmd)
    mock_splice = mocker.patch("scripts.update_homebrew.splice_formula")

    update_homebrew.main()

    mock_splice.assert_called_once()
    args, _ = mock_splice.call_args
    assert args[0] == formula_path


def test_main_package_mismatch_error(mocker, tmp_path):
    caller_dir = tmp_path / "caller_repo"
    caller_dir.mkdir()
    (caller_dir / "pyproject.toml").write_text(
        '[project]\nname = "actual-package"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    mocker.patch(
        "sys.argv",
        [
            "update_homebrew.py",
            "--version",
            "0.1.0",
            "--caller-dir",
            str(caller_dir),
            "--package",
            "copy-pasted-package",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        update_homebrew.main()

    err = str(exc_info.value)
    assert "Package name mismatch!" in err
    assert "Caller repository defines project: 'actual-package'" in err
    assert "Workflow input 'package_name' was:  'copy-pasted-package'" in err
    assert "You can remove 'package_name' from the workflow 'with:' block" in err


def test_main_custom_formula_path_success(mocker, tmp_path):
    formula_path = tmp_path / "custom-name.rb"
    formula_path.write_text("class CustomName < Formula\nend", encoding="utf-8")

    mocker.patch(
        "sys.argv",
        [
            "update_homebrew.py",
            "--version",
            "0.1.0",
            "--formula-path",
            str(formula_path),
            "--package",
            "my-package",
        ],
    )

    mocker.patch(
        "scripts.update_homebrew.get_pypi_metadata",
        return_value={
            "urls": [
                {"packagetype": "sdist", "url": "url", "digests": {"sha256": "sha"}}
            ]
        },
    )

    def mock_run_cmd(args, cwd=None):
        if "compile" in args:
            output_file = Path(args[args.index("-o") + 1])
            output_file.write_text("my-package==0.1.0\n", encoding="utf-8")
        return ""

    mocker.patch("scripts.update_homebrew.run_cmd", side_effect=mock_run_cmd)
    mock_splice = mocker.patch("scripts.update_homebrew.splice_formula")

    update_homebrew.main()

    mock_splice.assert_called_once()
    args, _ = mock_splice.call_args
    assert args[0] == formula_path
