from email_agent.parsing.html_cleaner import clean_html_to_text


def test_removes_scripts_and_styles():
    html = "<html><head><style>a{color:red}</style></head><body><script>x()</script><p>Olá mundo</p></body></html>"
    assert clean_html_to_text(html) == "Olá mundo"


def test_removes_tracking_pixel():
    html = '<body><img src="http://track.io/p.gif" width="1" height="1"><p>Texto</p></body>'
    out = clean_html_to_text(html)
    assert "track.io" not in out
    assert "Texto" in out


def test_preserves_relevant_links():
    html = '<body><a href="https://exemplo.com/fatura">Ver fatura</a></body>'
    out = clean_html_to_text(html)
    assert "Ver fatura" in out
    assert "https://exemplo.com/fatura" in out


def test_max_chars():
    html = "<p>" + "x" * 50000 + "</p>"
    assert len(clean_html_to_text(html, max_chars=100)) == 100
