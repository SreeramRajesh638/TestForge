def discount(price, is_member):
    """Members get 10% off orders of 100 or more."""
    if price < 0:
        raise ValueError("negative price")
    if is_member and price >= 100:
        return round(price * 0.9, 2)
    return price
