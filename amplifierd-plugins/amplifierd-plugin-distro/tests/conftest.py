import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distro_plugin.config import DistroPluginSettings


@pytest.fixture
def settings(tmp_path):
    """DistroPluginSettings with temp directories for isolation."""
    return DistroPluginSettings(
        distro_home=tmp_path / "distro",
        amplifier_home=tmp_path / "amplifier",
    )


# ---------------------------------------------------------------------------
# Route-testing fixtures
# ---------------------------------------------------------------------------


class MockState:
    """Minimal app-state object carrying settings for route handlers."""

    def __init__(self, settings: DistroPluginSettings) -> None:
        self.settings = settings


@pytest.fixture
def app(settings):
    """FastAPI app wired with distro routes and a MockState."""
    from distro_plugin.routes import create_routes

    application = FastAPI()
    application.state.distro = MockState(settings)
    application.include_router(create_routes())
    return application


@pytest.fixture
def client(app):
    """Synchronous test client for the route-test app."""
    return TestClient(app)
