"""
Shared pytest plumbing for the AEVE test suite.

Today this only registers the `--update-golden` flag used by
`tests/test_golden.py` to (re)seed the golden frame fixture. Add other
suite-wide fixtures or hooks here as the suite grows.
"""

from __future__ import annotations


def pytest_addoption(parser):
    """CLI flags that test files can read via `request.config.getoption`."""
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Re-render the golden frame and overwrite expected.png "
             "(used by tests/test_golden.py).",
    )
