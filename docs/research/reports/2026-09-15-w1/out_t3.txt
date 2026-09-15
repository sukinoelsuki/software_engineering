```python
import pytest

def test_merge_intervals():
    # Test normal case
    assert merge_intervals([(1, 3), (2, 4), (5, 7)]) == [(1, 4), (5, 7)]
    assert merge_intervals([(1, 4), (2, 3)]) == [(1, 4)]
    assert merge_intervals([(1, 2), (5, 6), (8, 10)]) == [(1, 2), (5, 6), (8, 10)]
    assert merge_intervals([(1, 5), (2, 3), (4, 7)]) == [(1, 7)]

    # Test empty input
    assert merge_intervals([]) == []

    # Test single interval
    assert merge_intervals([(1, 2)]) == [(1, 2)]

    # Test overlapping intervals
    assert merge_intervals([(1, 5), (2, 3), (4, 7), (6, 8)]) == [(1, 8)]

    # Test non-overlapping intervals
    assert merge_intervals([(1, 2), (3, 4), (5, 6)]) == [(1, 2), (3, 4), (5, 6)]

    # Test edge case with start and end equal
    assert merge_intervals([(1, 1)]) == [(1, 1)]

    # Test edge case with multiple intervals that all overlap
    assert merge_intervals([(1, 5), (2, 6), (3, 7), (4, 8)]) == [(1, 8)]

    # Test edge case with intervals that only touch at endpoints
    assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]

    # Test edge case with intervals that only touch at endpoints but not overlapping
    assert merge_intervals([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
```
