"""Blind Spot Intelligence demonstration module.

Coverage versus fault detection: the starting tests (ages 15 and 25) execute
every line of ``is_adult`` (100% line coverage), yet they cannot tell
``age >= 18`` from ``age > 18``. Only age 18 itself separates the two.
"""


def is_adult(age: int) -> bool:
    """True when ``age`` is 18 or more."""
    return age >= 18
