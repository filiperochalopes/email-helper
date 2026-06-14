import logging
from types import SimpleNamespace

from email_agent.connectors import gmail_client


def test_oauth_flow_suppresses_duplicate_library_log(monkeypatch, tmp_path):
    oauth_logger = logging.getLogger("google_auth_oauthlib.flow")
    oauth_logger.setLevel(logging.INFO)
    observed_levels = []

    class Flow:
        def run_local_server(self, **_kwargs):
            observed_levels.append(oauth_logger.level)
            return SimpleNamespace(to_json=lambda: '{"token": "ok"}')

    monkeypatch.setattr(
        gmail_client.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *_args: Flow(),
    )
    monkeypatch.setattr(
        gmail_client,
        "get_settings",
        lambda: SimpleNamespace(
            gmail_oauth_client_secret_file="client.json",
            gmail_token_storage_path=str(tmp_path),
        ),
    )

    gmail_client.run_oauth_flow("filipe@noharm.ai")

    assert observed_levels == [logging.WARNING]
    assert oauth_logger.level == logging.INFO
    assert (tmp_path / "filipe@noharm.ai.json").read_text() == '{"token": "ok"}'
