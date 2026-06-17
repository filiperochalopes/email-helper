from email_agent.intelligence.category_model import CategoryModel


def test_untrained_predicts_none(tmp_path):
    m = CategoryModel(path=str(tmp_path / "c.joblib"))
    assert not m.is_trained
    assert m.predict("qualquer texto") is None


def test_train_and_predict(tmp_path):
    m = CategoryModel(path=str(tmp_path / "c.joblib"))
    m.partial_fit(
        ["ganhe dinheiro clique agora premio"] * 3 + ["nota fiscal boleto fatura"] * 3,
        ["spam_suspeito"] * 3 + ["documento_fiscal"] * 3,
        [1.0] * 6,
    )
    assert m.is_trained
    pred = m.predict("segue a nota fiscal e o boleto para pagamento")
    assert pred is not None
    cat, conf = pred
    assert cat in ("documento_fiscal", "spam_suspeito")
    assert 0.0 <= conf <= 1.0


def test_persisted_model_reloads(tmp_path):
    path = str(tmp_path / "c.joblib")
    CategoryModel(path=path).partial_fit(
        ["promocao desconto"] * 3 + ["importante urgente"] * 3,
        ["promocao"] * 3 + ["importante_p1"] * 3,
        [1.0] * 6,
    )
    # nova instância deve carregar o modelo salvo
    assert CategoryModel(path=path).is_trained
