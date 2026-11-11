import pytest

from similar_sound import models


@pytest.fixture(autouse=True)
def models_run_in_process(monkeypatch):
    """Tests that exercise the real models do so in this process; they must not start a worker of their own.
    (test_models_and_store.py turns this off to test the worker itself.)"""
    monkeypatch.setattr(models, "IN_PROCESS", True)
    yield
    models.unload()
