"""Tests run in Replay with no key: keep the developer's .env out of the test process."""
import os

import dotenv
import pytest

os.environ.pop("MISTRAL_API_KEY", None)
dotenv.load_dotenv = lambda *args, **kwargs: False   # dock/app.py would otherwise find the repo .env


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.index import app
    return TestClient(app)
