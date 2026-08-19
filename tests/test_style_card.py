from types import SimpleNamespace

from email_agent.corpus import style_card as sc


def _example(*, reply, incoming="Pergunta qualquer", account="me@example.com",
             overlap=False, history=None):
    return SimpleNamespace(
        account_email=account,
        reply_text=reply,
        incoming_text=incoming,
        history=history or [],
        self_overlap=overlap,
    )


def test_measured_numbers_come_from_counting_not_from_the_model():
    examples = [
        _example(reply="Ok."),
        _example(reply="Bom dia, segue o documento.\nAbraço"),
        _example(reply="Olá! Thanks, please send the regards would could report."),
    ]
    m = sc.measure(examples)
    assert m["respostas"] == 3
    assert m["pct_uma_linha"] == 67
    assert m["pct_com_saudacao"] == 67
    assert m["pct_em_ingles"] == 33
    assert m["chars_mediana"] > 0


def test_grouping_drops_self_overlap_and_needs_volume():
    big = [_example(reply="resposta", account="grande@x.com") for _ in range(sc.MIN_EXAMPLES_PER_ACCOUNT)]
    small = [_example(reply="resposta", account="pequena@x.com") for _ in range(3)]
    tainted = [_example(reply="resposta", account="grande@x.com", overlap=True) for _ in range(5)]

    groups = sc.group_examples(big + small + tainted)

    assert set(groups) == {sc.GLOBAL_CARD, "grande@x.com"}
    assert len(groups["grande@x.com"]) == sc.MIN_EXAMPLES_PER_ACCOUNT
    # O global agrega tudo que é utilizável e serve de fallback para as contas pequenas.
    assert len(groups[sc.GLOBAL_CARD]) == sc.MIN_EXAMPLES_PER_ACCOUNT + 3


def test_sampling_is_deterministic_and_spreads_over_the_list():
    examples = [_example(reply=f"r{i}") for i in range(100)]
    chosen = sc.sample_examples(examples, 10)
    assert len(chosen) == 10
    assert sc.sample_examples(examples, 10) == chosen
    assert chosen[0].reply_text == "r0"
    assert chosen[-1].reply_text == "r90"
    assert sc.sample_examples(examples, 500) == examples


def test_batches_are_sent_to_the_llm_and_reduced(monkeypatch):
    prompts = []

    def fake_generate(prompt, **kwargs):
        prompts.append(kwargs.get("task"))
        if kwargs.get("task") == "style-card-extract":
            return SimpleNamespace(data={"tom": "direto", "saudacoes": ["Oi"]}, error=None)
        return SimpleNamespace(data={"tom": "direto e curto", "saudacoes": ["Oi"]}, error=None)

    monkeypatch.setattr(sc, "generate_json", fake_generate)
    monkeypatch.setattr(sc, "get_settings", lambda: SimpleNamespace(llm_model="m"), raising=False)

    examples = [_example(reply=f"resposta {i}") for i in range(5)]
    card = sc.build_card("conta@x.com", examples, batch_size=2, max_examples=200)

    assert prompts.count("style-card-extract") == 3  # 5 exemplos em lotes de 2
    assert prompts.count("style-card-reduce") == 1
    assert card.traits["tom"] == "direto e curto"
    assert card.llm_errors == []


def test_failed_reduce_falls_back_to_local_merge(monkeypatch):
    def fake_generate(prompt, **kwargs):
        if kwargs.get("task") == "style-card-extract":
            return SimpleNamespace(data={"saudacoes": ["Oi"], "tom": "direto"}, error=None)
        return SimpleNamespace(data=None, error="timeout")

    monkeypatch.setattr(sc, "generate_json", fake_generate)
    examples = [_example(reply=f"resposta {i}") for i in range(4)]

    card = sc.build_card("conta@x.com", examples, batch_size=2)

    assert card.traits["saudacoes"] == ["Oi"]
    assert any("consolidação" in e for e in card.llm_errors)


def test_card_survives_total_llm_failure_keeping_measurements(monkeypatch):
    monkeypatch.setattr(
        sc, "generate_json",
        lambda prompt, **kw: SimpleNamespace(data=None, error="conexão recusada"),
    )
    card = sc.build_card("conta@x.com", [_example(reply="Bom dia, ok.")], batch_size=2)

    assert card.traits == {}
    assert card.measured["respostas"] == 1
    assert card.llm_errors


def test_markdown_is_editable_and_flags_missing_traits(monkeypatch):
    monkeypatch.setattr(
        sc, "generate_json",
        lambda prompt, **kw: SimpleNamespace(data={"tom": "direto"}, error=None),
    )
    card = sc.build_card("conta@x.com", [_example(reply="Bom dia, ok.")])
    text = sc.render_markdown(card)

    assert "# Cartão de estilo — conta@x.com" in text
    assert "corrigido à mão" in text
    assert "- direto" in text
    assert "(não observado)" in text  # campos que o modelo não devolveu


def test_write_card_sanitizes_the_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sc, "generate_json", lambda prompt, **kw: SimpleNamespace(data={"tom": "x"}, error=None)
    )
    card = sc.build_card(sc.GLOBAL_CARD, [_example(reply="ok, segue.")])
    path = sc.write_card(card, tmp_path / "style")
    assert path.name == "_global.md"
    assert path.read_text(encoding="utf-8").startswith("# Cartão de estilo — todas as contas")


def test_failed_batch_is_retried_split_in_half(monkeypatch):
    """Timeout e JSON malformado vêm de prompt/saída grandes: metade do lote resolve."""
    seen_sizes = []

    def fake_generate(prompt, **kwargs):
        size = prompt.count("--- exemplo ")
        seen_sizes.append(size)
        if size >= 4:  # lote grande falha, como na primeira rodada real
            return SimpleNamespace(data=None, error="timed out")
        return SimpleNamespace(data={"tom": "direto"}, error=None)

    monkeypatch.setattr(sc, "generate_json", fake_generate)
    examples = [_example(reply=f"resposta {i}") for i in range(4)]

    card = sc.build_card("conta@x.com", examples, batch_size=4)

    assert seen_sizes[0] == 4  # tentou inteiro
    assert 2 in seen_sizes  # e dividiu
    assert card.llm_errors == []
    assert card.traits


def test_retry_gives_up_and_reports_instead_of_looping(monkeypatch):
    calls = []

    def always_fail(prompt, **kwargs):
        calls.append(1)
        return SimpleNamespace(data=None, error="timed out")

    monkeypatch.setattr(sc, "generate_json", always_fail)
    card = sc.build_card("conta@x.com", [_example(reply=f"r{i}") for i in range(8)], batch_size=8)

    assert card.llm_errors
    assert len(calls) < 20  # a recursão tem profundidade limitada
    assert card.measured["respostas"] == 8


def test_local_merge_respects_the_same_cap_as_the_reduce_prompt():
    """Sem teto, o cartão de fallback saía com 20 aberturas em vez de 6."""
    batches = [{"saudacoes": [f"Saudação {i}"]} for i in range(20)]
    merged = sc._merge_batches_locally(batches)
    assert len(merged["saudacoes"]) == sc.MAX_ITEMS_PER_FIELD


def test_global_card_is_skipped_when_filtering_by_account():
    """Gerar o global a partir de uma conta só sobrescreveria o arquivo bom com um
    cartão de subconjunto rotulado "todas as contas"."""
    items = [_example(reply="r", account="uma@x.com") for _ in range(sc.MIN_EXAMPLES_PER_ACCOUNT)]

    filtered = sc.group_examples(items, include_global=False)
    assert set(filtered) == {"uma@x.com"}

    full = sc.group_examples(items)
    assert set(full) == {sc.GLOBAL_CARD, "uma@x.com"}
