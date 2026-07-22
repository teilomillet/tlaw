from hypothesis import settings
from hypothesis import strategies as st

import tlaw
from implementations import CorrectWallet


wallet = tlaw.system(
    CorrectWallet,
    observe=lambda value: value.balance,
    name="wallet",
)
wallets = tlaw.system(
    CorrectWallet,
    start=lambda Wallet, source, destination: (
        Wallet(source),
        Wallet(destination),
    ),
    observe=lambda pair: (pair[0].balance, pair[1].balance),
    name="wallets",
)

balances = st.integers(min_value=0, max_value=200)
amounts = st.integers(min_value=0, max_value=100)
positive_amounts = st.integers(min_value=1, max_value=100)
law_settings = settings(max_examples=80, derandomize=True, database=None)


@law_settings
@tlaw.law("deposit adds exactly the amount", balance=balances, amount=amounts)
def deposit_is_exact(balance: int, amount: int):
    return wallet(balance).call("deposit", amount).same_as(wallet(balance + amount))


@law_settings
@tlaw.law(
    "withdrawal is exact or changes nothing",
    balance=balances,
    amount=amounts,
)
def withdrawal_is_exact_or_unchanged(balance: int, amount: int):
    expected = balance - amount if amount <= balance else balance
    return wallet(balance).call("withdraw", amount).same_as(wallet(expected))


@law_settings
@tlaw.law(
    "transfer moves the amount without creating money",
    source=balances,
    destination=balances,
    amount=positive_amounts,
)
def transfer_is_exact(source: int, destination: int, amount: int):
    if amount <= source:
        expected = wallets(source - amount, destination + amount)
    else:
        expected = wallets(source, destination)

    return (
        wallets(source, destination)
        .do(
            lambda pair: pair[0].transfer_to(pair[1], amount),
            name=f"transfer({amount})",
        )
        .same_as(expected)
    )


@law_settings
@tlaw.law(
    "transferring zero changes nothing",
    source=balances,
    destination=balances,
)
def zero_transfer_is_identity(source: int, destination: int):
    start = wallets(source, destination)
    return start.do(
        lambda pair: pair[0].transfer_to(pair[1], 0),
        name="transfer(0)",
    ).same_as(start)
