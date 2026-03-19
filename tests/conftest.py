import pytest

from koncile_mcp.config import Config
from koncile_mcp.client import KoncileClient

BASE_URL = "http://test-api.local"
API_KEY = "sk-test-123"


@pytest.fixture
def config():
    return Config(api_url=BASE_URL, api_key=API_KEY, request_timeout=10)


@pytest.fixture
def client(config):
    return KoncileClient(config)
