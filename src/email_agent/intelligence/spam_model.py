"""Camada 2: modelo estatístico local incremental (spam vs ham).

HashingVectorizer + SGDClassifier(loss="log_loss") com partial_fit,
persistido via joblib. Treina apenas com eventos confiáveis
(feedback explícito, Label Studio e eventos implícitos do usuário com peso).
"""
import os

import joblib
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier

from email_agent.config import get_settings

CLASSES = np.array([0, 1])  # 0=ham, 1=spam


class SpamModel:
    def __init__(self, path: str | None = None):
        self.path = path or get_settings().spam_model_path
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

    def partial_fit(self, texts: list[str], labels: list[int], weights: list[float]) -> None:
        X = self.vectorizer.transform(texts)
        if self.clf is None:
            self.clf = SGDClassifier(loss="log_loss", alpha=1e-5, random_state=42)
            self.clf.partial_fit(X, labels, classes=CLASSES, sample_weight=weights)
        else:
            self.clf.partial_fit(X, labels, sample_weight=weights)
        self.save()

    def predict_proba_spam(self, text: str) -> float | None:
        """Probabilidade de spam, ou None se o modelo ainda não foi treinado."""
        if self.clf is None:
            return None
        X = self.vectorizer.transform([text])
        idx = list(self.clf.classes_).index(1)
        return float(self.clf.predict_proba(X)[0][idx])
