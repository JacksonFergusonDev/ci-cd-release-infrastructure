import pytest


@pytest.fixture(autouse=True)
def _isolate_github_actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as a local run, even on a GitHub Actions runner.

    The scripts switch output format on GITHUB_ACTIONS and append to the files
    named by GITHUB_OUTPUT / GITHUB_STEP_SUMMARY. Tests that need Actions
    behavior set these explicitly.
    """
    for var in ("GITHUB_ACTIONS", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(var, raising=False)
