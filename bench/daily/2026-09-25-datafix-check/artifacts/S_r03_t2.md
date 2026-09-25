```python
"""Pagination helper (contains a bug that the failing tests expose)."""


def chunk(items, size):
    """Split `items` into consecutive chunks of at most `size` elements."""
    if size <= 0:
        raise ValueError("size must be positive")
    chunks = []
    for start in range(0, len(items), size):
        chunks.append(items[start:start + size])
    return chunks
```
