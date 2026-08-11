from threading import Lock
from time import sleep
from types import SimpleNamespace

from email_agent.sync import service


def test_classify_many_respects_configured_concurrency(monkeypatch):
    active = peak = 0
    lock = Lock()

    def fake_classify(_db_id: int) -> dict:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        sleep(0.02)
        with lock:
            active -= 1
        return {}

    monkeypatch.setattr(service, "classify_message", fake_classify)
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(llm_max_concurrency=3)
    )

    assert service._classify_many(list(range(9))) == (9, 0)
    assert peak == 3


def test_classify_many_counts_individual_failures(monkeypatch):
    def fake_classify(db_id: int) -> dict:
        if db_id == 2:
            raise RuntimeError("falha esperada")
        return {}

    monkeypatch.setattr(service, "classify_message", fake_classify)
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(llm_max_concurrency=6)
    )

    assert service._classify_many([1, 2, 3]) == (2, 1)
