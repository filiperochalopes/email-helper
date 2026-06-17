from email_agent.intelligence.features import email_features


def test_includes_subject_and_body():
    f = email_features("Assunto X", "corpo do email")
    assert "Assunto X" in f
    assert "corpo do email" in f


def test_sender_domain_becomes_single_token():
    f = email_features("oi", "texto", from_email="x@stetnet.com.br", from_name="Registro BR")
    assert "dom_stetnet_com_br" in f  # domínio vira token único p/ o modelo aprender
    assert "Registro BR" in f


def test_handles_missing_sender():
    f = email_features("a", "b", from_email=None, from_name=None)
    assert "a" in f and "b" in f
