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


@pytest.fixture
def replayed(client):
    """POST one dataset case with no key and return the recorded response, failing plainly if there is none."""
    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, f"{path} {body}: {r.status_code} {r.text[:200]}"
        got = r.json()
        assert got.get("mode") == "replay", f"{path} {body}: mode={got.get('mode')!r}"
        return got
    return post
