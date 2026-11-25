"""Step 2 of the tutorial: the weak suite plus tests aimed at the surviving
mutants (boundaries, the error message, rounding). Mutation score: 100%."""
import pytest
from discount import discount

def test_member_big_order():
    assert discount(200, True) == 180.0

def test_non_member():
    assert discount(200, False) == 200

def test_member_exactly_100_gets_discount():      # kills #3, #4 (boundary)
    assert discount(100, True) == 90.0

def test_member_99_no_discount():
    assert discount(99, True) == 99

def test_zero_price_is_allowed():                 # kills #0, #1
    assert discount(0, False) == 0

def test_negative_price_raises():                 # kills #2 (checks the message)
    with pytest.raises(ValueError, match="negative price"):
        discount(-1, True)

def test_rounds_to_two_decimals():                # kills #8
    assert discount(123.45, True) == 111.11
