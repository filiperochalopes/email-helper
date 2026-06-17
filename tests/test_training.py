from types import SimpleNamespace

from email_agent.intelligence import training
from email_agent.intelligence.taxonomy import (
    LABEL_AGUARDANDO,
    LABEL_FISCAL,
    LABEL_SPAM_SUSPEITO,
)


def _event(event_type, previous_labels=None, new_labels=None):
    return SimpleNamespace(
        event_type=event_type,
        previous_labels=previous_labels,
        new_labels=new_labels,
    )


def test_label_changed_spam_added_becomes_spam():
    ev = _event(
        "label_changed",
        previous_labels=["INBOX"],
        new_labels=["INBOX", LABEL_SPAM_SUSPEITO],
    )
    assert training._training_for_event(ev, set()) == ("spam_suspeito", 0.9)


def test_label_changed_spam_removed_becomes_ham():
    ev = _event(
        "label_changed",
        previous_labels=["INBOX", LABEL_SPAM_SUSPEITO],
        new_labels=["INBOX"],
    )
    assert training._training_for_event(ev, set()) == ("ham", 0.8)


def test_label_changed_fiscal_added_becomes_documento_fiscal():
    ev = _event(
        "label_changed",
        previous_labels=[],
        new_labels=[LABEL_FISCAL],
    )
    assert training._training_for_event(ev, set()) == ("documento_fiscal", 0.9)


def test_label_changed_aguardando_added_becomes_aguardando_resposta():
    ev = _event(
        "label_changed",
        previous_labels=[],
        new_labels=[LABEL_AGUARDANDO],
    )
    assert training._training_for_event(ev, set()) == ("aguardando_resposta", 0.9)


def test_trashed_spam_suspeito_is_full_weight_confirmation():
    # Excluiu sem remover o label AI/Spam Suspeito = confirmou que o agente
    # acertou: reforço positivo com peso máximo.
    ev = _event("moved_to_trash")
    assert training._training_for_event(ev, {LABEL_SPAM_SUSPEITO}) == ("spam_suspeito", 1.0)


def test_label_changed_irrelevant_delta_is_ignored():
    ev = _event(
        "label_changed",
        previous_labels=["INBOX"],
        new_labels=["INBOX", "IMPORTANT"],
    )
    assert training._training_for_event(ev, set()) == (None, 0)
