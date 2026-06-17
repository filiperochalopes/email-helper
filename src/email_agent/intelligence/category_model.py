"""Camada 2 (multiclasse): modelo estatístico local de categoria.

Espelha o ``SpamModel`` binário, mas prevê **todas as categorias** (não só
spam/ham): ``HashingVectorizer + SGDClassifier(loss="log_loss")`` com
``partial_fit``, persistido via joblib. Treina só com eventos confiáveis.

O ``predict`` devolve ``(categoria, confiança)`` onde a confiança é a maior
probabilidade do ``predict_proba`` — é esse número que o classificador usa para
decidir se confia no modelo e **dispensa a LLM**.
"""
import os

import joblib
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier

from email_agent.config import get_settings
from email_agent.intelligence.taxonomy import CATEGORIES

# Conjunto fixo de classes (precisa ser passado já no primeiro partial_fit).
CLASSES = np.array(sorted(CATEGORIES))


class CategoryModel:
    def __init__(self, path: str | None = None):
        self.path = path or get_settings().category_model_path
        self.vectorizer = HashingVectorizer(n_features=2**20, alternate_sign=False)
        self.clf: SGDClassifier | None = None
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            self.clf = joblib.load(self.path)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        joblib.dump(self.clf, self.path)

    @property
    def is_trained(self) -> bool:
        return self.clf is not None

    def partial_fit(self, texts: list[str], labels: list[str], weights: list[float]) -> None:
        X = self.vectorizer.transform(texts)
        if self.clf is None:
            self.clf = SGDClassifier(loss="log_loss", alpha=1e-5, random_state=42)
            self.clf.partial_fit(X, labels, classes=CLASSES, sample_weight=weights)
        else:
            self.clf.partial_fit(X, labels, sample_weight=weights)
        self.save()

    def predict(self, text: str) -> tuple[str, float] | None:
        """(categoria mais provável, confiança), ou None se ainda não treinado."""
        if self.clf is None:
            return None
        X = self.vectorizer.transform([text])
        proba = self.clf.predict_proba(X)[0]
        idx = int(np.argmax(proba))
        return str(self.clf.classes_[idx]), float(proba[idx])
