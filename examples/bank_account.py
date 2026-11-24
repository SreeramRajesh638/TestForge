"""A bank account with overdraft protection and a transaction history."""


class InsufficientFundsError(Exception):
    pass


class BankAccount:
    def __init__(self, owner: str, balance: float = 0.0, overdraft_limit: float = 0.0):
        if balance < 0:
            raise ValueError("initial balance cannot be negative")
        self.owner = owner
        self.balance = balance
        self.overdraft_limit = overdraft_limit
        self.history: list[tuple[str, float]] = []

    def deposit(self, amount: float) -> float:
        if amount <= 0:
            raise ValueError("deposit must be positive")
        self.balance += amount
        self.history.append(("deposit", amount))
        return self.balance

    def withdraw(self, amount: float) -> float:
        if amount <= 0:
            raise ValueError("withdrawal must be positive")
        if self.balance - amount < -self.overdraft_limit:
            raise InsufficientFundsError("insufficient funds")
        self.balance -= amount
        self.history.append(("withdraw", amount))
        return self.balance

    def transfer(self, other: "BankAccount", amount: float) -> None:
        self.withdraw(amount)
        other.deposit(amount)

    def apply_interest(self, rate: float) -> float:
        if rate < 0:
            raise ValueError("rate must be non-negative")
        if self.balance > 0:
            self.balance = round(self.balance * (1 + rate), 2)
        return self.balance
