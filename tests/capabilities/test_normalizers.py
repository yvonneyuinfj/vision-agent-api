"""标准化器回归：分类 score 语义（0.0 是合法分值，不得被 or 短路丢弃）。"""
from __future__ import annotations

from app.capabilities.normalizers.classification_normalizer import (
    normalize_classifications,
)


def test_classification_score_zero_is_preserved():
    """score=0.0 为有效分值，必须保留而非回落到 probability/None。"""
    # Arrange / Act
    out = normalize_classifications({"labels": [{"label": "pass", "score": 0.0}]})

    # Assert
    assert len(out) == 1
    assert out[0].label == "pass"
    assert out[0].score == 0.0


def test_classification_score_zero_not_overridden_by_probability():
    """score=0.0 存在时不应被 probability 覆盖。"""
    out = normalize_classifications(
        {"labels": [{"label": "pass", "score": 0.0, "probability": 0.9}]}
    )
    assert out[0].score == 0.0


def test_classification_falls_back_to_probability_when_score_missing():
    out = normalize_classifications({"labels": [{"label": "pass", "probability": 0.9}]})
    assert out[0].score == 0.9


def test_classification_missing_both_scores_is_none():
    out = normalize_classifications({"labels": [{"label": "pass"}]})
    assert out[0].score is None
