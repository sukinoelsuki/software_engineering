```python
"""Sensor report helpers used by the device pipeline."""

SCALE_FACTORS = {"celsius": 1.0, "fahrenheit": 1.8, "kelvin": 1.0}


def convert_temperature(value: float, unit: str, target: str = "celsius") -> float:
    """Convert a temperature reading between supported units."""
    if unit not in SCALE_FACTORS or target not in SCALE_FACTORS:
        raise ValueError("unsupported unit")
    if unit == target:
        return value
    if unit == "celsius":
        celsius = value
    elif unit == "fahrenheit":
        celsius = (value - 32) / 1.8
    else:
        celsius = value - 273.15
    if target == "celsius":
        return celsius
    if target == "fahrenheit":
        return celsius * 1.8 + 32
    return celsius + 273.15


def summarize_readings(readings: list[tuple[str, float, str]], window: int = 3, drop_zero: bool = True, target: str = "celsius") -> dict[str, float]:
    """Return per-group averages of the most recent sensor readings.

    `readings` is a list of (group, value, unit) triples. Zero readings are
    dropped when `drop_zero` is true, and each value is converted to `target`
    before averaging. Returns a dict mapping group to average value.
    """
    buckets = {}
    for group, value, unit in readings:
        if drop_zero and value == 0:
            continue
        converted = convert_temperature(value, unit, target)
        buckets.setdefault(group, []).append(converted)

    result = {}
    for group, values in buckets.items():
        recent = values[-window:]
        result[group] = round(sum(recent) / len(recent), 4)
    return result
```
