"""Step 1 of the tutorial: a weak suite. 83% line coverage, but mutation
testing shows it catches fewer than half of the planted bugs."""
import pytest
from discount import discount


def test_member_big_order():
    assert discount(200, True) == 180.0


def test_non_member():
    assert discount(200, False) == 200
