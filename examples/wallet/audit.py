from implementations import (
    CorrectWallet,
    DoubleDepositWallet,
    MintingTransferWallet,
    NegativeWithdrawalWallet,
    ZeroTransferFeeWallet,
)
from tlaw.book import Lawbook


evidence = Lawbook(__file__, tests=("laws.py",))
evidence.baseline("correct", CorrectWallet)
evidence.known_wrong(
    "double-deposit",
    DoubleDepositWallet,
    breaks="Depositing changes the balance by exactly the deposited amount.",
)
evidence.known_wrong(
    "negative-withdrawal",
    NegativeWithdrawalWallet,
    breaks="A refused withdrawal changes nothing and balances stay non-negative.",
)
evidence.known_wrong(
    "minting-transfer",
    MintingTransferWallet,
    breaks="Transfers preserve total money and move the exact amount.",
)
evidence.known_wrong(
    "zero-transfer-fee",
    ZeroTransferFeeWallet,
    breaks="Transferring zero changes nothing.",
)
