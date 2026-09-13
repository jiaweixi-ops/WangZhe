import numpy as np

from qijing_spike.viewport import detect_content_viewport


def test_detects_letterbox():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[10:90, 20:180] = 120
    result = detect_content_viewport(img)
    rect = result.rect
    assert (rect.left, rect.top, rect.right, rect.bottom) == (20, 10, 180, 90)
    assert result.mode == "BLACK_BORDER"
    assert result.confidence > 0.8


def test_all_black_is_explicit_failure_not_silent_fallback():
    img = np.zeros((40, 80, 3), dtype=np.uint8)
    result = detect_content_viewport(img)
    assert result.rect.as_region() == (0, 0, 80, 40)
    assert result.mode == "FAILED_ALL_BLACK"
    assert result.confidence == 0.0


def test_aspect_prior_is_explicit_degraded_mode():
    img = np.full((100, 220, 3), 80, dtype=np.uint8)
    result = detect_content_viewport(img, expected_aspect_ratio=16 / 9)
    assert result.mode == "ASPECT_PRIOR"
    assert abs(result.rect.width / result.rect.height - 16 / 9) < 0.03
