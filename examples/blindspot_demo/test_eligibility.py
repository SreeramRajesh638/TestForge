"""Starting suite for the demonstration: full line coverage, but no test at the
boundary age of 18."""

import eligibility as m


def test_minor_is_not_adult():
    assert m.is_adult(15) is False


def test_adult_is_adult():
    assert m.is_adult(25) is True
