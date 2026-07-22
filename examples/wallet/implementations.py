from __future__ import annotations


class CorrectWallet:
    def __init__(self, balance: int = 0) -> None:
        if balance < 0:
            raise ValueError("a wallet cannot start with a negative balance")
        self.balance = balance

    def deposit(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("a deposit cannot be negative")
        self.balance += amount

    def withdraw(self, amount: int) -> bool:
        if amount < 0:
            raise ValueError("a withdrawal cannot be negative")
        if amount > self.balance:
            return False
        self.balance -= amount
        return True

    def transfer_to(self, target: CorrectWallet, amount: int) -> bool:
        if not self.withdraw(amount):
            return False
        target.deposit(amount)
        return True


class DoubleDepositWallet(CorrectWallet):
    def deposit(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("a deposit cannot be negative")
        self.balance += amount * 2


class NegativeWithdrawalWallet(CorrectWallet):
    def withdraw(self, amount: int) -> bool:
        if amount < 0:
            raise ValueError("a withdrawal cannot be negative")
        self.balance -= amount
        return True


class MintingTransferWallet(CorrectWallet):
    def transfer_to(self, target: CorrectWallet, amount: int) -> bool:
        if not self.withdraw(amount):
            return False
        target.deposit(amount + 1)
        return True


class ZeroTransferFeeWallet(CorrectWallet):
    def transfer_to(self, target: CorrectWallet, amount: int) -> bool:
        if amount == 0:
            if self.balance > 0:
                self.balance -= 1
            return True
        return super().transfer_to(target, amount)
