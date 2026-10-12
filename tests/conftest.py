import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_json():
    return lambda name: json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text()


@pytest.fixture
def filters_cfg():
    from openapply.config import load_filters

    return load_filters()


@pytest.fixture(scope="session")
def browser():
    """One headless Chromium for the whole run. Tests that use it route every request to local
    fixtures and abort everything else, so nothing reaches the internet."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:  # chromium not installed
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()
