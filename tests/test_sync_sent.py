from types import SimpleNamespace

from email_agent.connectors.imap_client import discover_sent_folders
from email_agent.sync import gmail_sync, imap_sync


class _FakeIMAP:
    def __init__(self, folders):
        self._folders = folders

    def list_folders(self):
        return self._folders


def test_discover_sent_folders_returns_every_variant():
    client = _FakeIMAP(
        [
            ((b"\\HasNoChildren",), b".", "INBOX"),
            ((b"\\Sent",), b".", "Sent"),
            ((b"\\HasNoChildren",), b".", "Sent Items"),
            ((b"\\HasNoChildren",), b".", "INBOX.Enviados"),
            ((b"\\HasNoChildren",), b".", "Arquivo.Sent.2024"),
            ((b"\\Junk",), b".", "Junk"),
            ((b"\\Trash",), b".", "Trash"),
            ((b"\\Drafts",), b".", "Rascunhos"),
            ((b"\\HasNoChildren",), b".", "AI.Spam Suspeito"),
        ]
    )

    assert discover_sent_folders(client) == [
        "Sent",
        "Sent Items",
        "INBOX.Enviados",
        "Arquivo.Sent.2024",
    ]


def test_discover_sent_folders_does_not_match_sent_inside_a_word():
    """"Presentations" contém "sent"; entrar na varredura marcaria mensagens
    recebidas como enviadas pelo usuário e envenenaria o catálogo."""
    client = _FakeIMAP(
        [
            ((b"\\HasNoChildren",), b".", "Presentations"),
            ((b"\\HasNoChildren",), b".", "Consented"),
            ((b"\\HasNoChildren",), b".", "Sent"),
        ]
    )

    assert discover_sent_folders(client) == ["Sent"]


def test_discover_sent_folders_ignores_sent_shaped_trash_and_drafts():
    """Uma pasta marcada como lixo/rascunho não entra mesmo com nome parecido."""
    client = _FakeIMAP(
        [
            ((b"\\Trash",), b".", "Deleted Sent"),
            ((b"\\Drafts",), b".", "Sent Drafts"),
            ((b"\\Sent",), b".", "Enviados"),
        ]
    )

    assert discover_sent_folders(client) == ["Enviados"]


def test_gmail_sent_sweep_has_no_date_window_and_keeps_history_cursor(monkeypatch):
    queries: list[str] = []
    persisted: dict = {}

    monkeypatch.setattr(
        gmail_sync, "db_session", lambda: _ctx(SimpleNamespace(get=lambda *_a: _account()))
    )
    monkeypatch.setattr(gmail_sync, "get_service", lambda _account: "service")

    def fake_ids(_service, query):
        queries.append(query)
        return ["m1", "m2"]

    def fake_persist(_service, _account, ids, limit=None):
        persisted["ids"] = ids
        persisted["limit"] = limit
        return [11, 12], True

    monkeypatch.setattr(gmail_sync, "_ids_from_query", fake_ids)
    monkeypatch.setattr(gmail_sync, "_fetch_and_persist", fake_persist)
    monkeypatch.setattr(
        gmail_sync, "_ids_from_search", _fail("sync sent não pode usar a janela padrão")
    )

    assert gmail_sync.sync_sent(1) == [11, 12]
    assert queries == ["in:sent"]
    assert persisted == {"ids": ["m1", "m2"], "limit": None}


def test_gmail_sent_sweep_accepts_a_window(monkeypatch):
    queries: list[str] = []
    monkeypatch.setattr(
        gmail_sync, "db_session", lambda: _ctx(SimpleNamespace(get=lambda *_a: _account()))
    )
    monkeypatch.setattr(gmail_sync, "get_service", lambda _account: "service")
    monkeypatch.setattr(
        gmail_sync, "_ids_from_query", lambda _s, query: queries.append(query) or []
    )
    monkeypatch.setattr(gmail_sync, "_fetch_and_persist", lambda *_a, **_k: ([], True))

    gmail_sync.sync_sent(1, since_days=30)

    assert queries[0].startswith("in:sent after:")


def test_imap_sweep_ignores_cursor_and_skips_uids_already_stored(monkeypatch):
    searched: list = []
    fetched: dict = {}

    class Client:
        def select_folder(self, _folder, readonly=True):
            return {b"UIDVALIDITY": 42}

        def search(self, criteria):
            searched.append(criteria)
            return [1, 2, 3, 4]

    monkeypatch.setattr(imap_sync, "_known_uids", lambda *_a: {1, 3})

    def fake_fetch(_client, _account, role, folder, uidvalidity, uids, _settings):
        fetched.update(role=role, folder=folder, uidvalidity=uidvalidity, uids=uids)
        return [101, 102]

    monkeypatch.setattr(imap_sync, "_fetch_uids", fake_fetch)
    monkeypatch.setattr(
        imap_sync, "_get_cursor", _fail("a varredura não pode tocar no cursor incremental")
    )

    new_ids = imap_sync._sweep_sent_folder(
        Client(), _account(), "Sent", None, SimpleNamespace(max_email_text_chars=1000)
    )

    assert new_ids == [101, 102]
    assert searched == [["ALL"]]
    assert fetched["uids"] == [2, 4]
    assert fetched["role"] == "sent"


def test_imap_sweep_uses_since_when_a_window_is_given(monkeypatch):
    searched: list = []

    class Client:
        def select_folder(self, _folder, readonly=True):
            return {b"UIDVALIDITY": 42}

        def search(self, criteria):
            searched.append(criteria)
            return []

    monkeypatch.setattr(imap_sync, "_known_uids", lambda *_a: set())
    monkeypatch.setattr(imap_sync, "_fetch_uids", lambda *_a: [])

    imap_sync._sweep_sent_folder(
        Client(), _account(), "Sent", 30, SimpleNamespace(max_email_text_chars=1000)
    )

    assert searched[0][0] == "SINCE"


def _account():
    return SimpleNamespace(id=1, email_address="me@example.com", provider="imap")


def _fail(message):
    def _raise(*_args, **_kwargs):
        raise AssertionError(message)

    return _raise


class _ctx:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False
