from qijing_spike.models import Rect


def test_rect_intersection_and_translation():
    a = Rect(10, 20, 110, 120)
    b = Rect(50, 0, 150, 80)
    assert a.intersect(b) == Rect(50, 20, 110, 80)
    assert a.translated(-10, 5) == Rect(0, 25, 100, 125)
    assert a.area == 10_000
