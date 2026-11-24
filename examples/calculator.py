"""Simple arithmetic and number utilities."""


def add(a: int, b: int) -> int:
    return a + b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("cannot divide by zero")
    return a / b


def is_even(n: int) -> bool:
    return n % 2 == 0


def clamp(x: int, low: int, high: int) -> int:
    if low > high:
        raise ValueError("low must be <= high")
    if x < low:
        return low
    if x > high:
        return high
    return x


def grade(score: int) -> str:
    if score < 0 or score > 100:
        raise ValueError("score out of range")
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 50:
        return "C"
    return "F"


def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
