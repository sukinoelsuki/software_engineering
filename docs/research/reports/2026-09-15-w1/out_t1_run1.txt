```python
"""Sensor report helpers used by the device pipeline."""

SCALE_FACTORS = {"celsius": 1.0, "fahrenheit": 1.8, "kelvin": 1.0}


def convert_temperature(value: float, unit: str, target: str = "celsius") -> float:
    """Convert a temperature reading between supported units."""
    if unit not in SCALE_FACTORS or target not in SCALE_FACTORS:
        raise ValueError("unsupported unit")
    if unit == target:
        return value
    if unit == "celsius" and target == "fahrenheit":
        return value * 1.8 + 32
    if unit == "fahrenheit" and target == "celsius":
        return (value - 32) / 1.8
    if target == "kelvin":
        celsius = convert_temperature(value, unit, "celsius")
        return celsius + 273.15
    return convert_temperature(value, "kelvin", "celsius") - 273.15


def summarize_readings(readings: list, window: int = 3, drop_zero: bool = True, target: str = "celsius") -> dict:
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
