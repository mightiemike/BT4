### Title
Slow-Mode Transaction Fee Permanently Lost on Silent Execution Failure — (`core/contracts/Endpoint.sol`)

---

### Summary

When a user submits a slow-mode transaction, they pay `SLOW_MODE_FEE` upfront at submission time. If the transaction later fails silently during execution (e.g., a `WithdrawCollateral` that fails a health check after market conditions change), the fee is never returned. A comment in the code explicitly acknowledges that a prior refund mechanism was removed.

---

### Finding Description

In `submitSlowModeTransactionImpl`, the slow-mode fee is charged at **submission time**, before the transaction is queued:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [1](#0-0) 

The transaction is stored and executed up to 3 days later via `_executeSlowModeTransaction`. When execution fails, the catch block does nothing — and the code comment explicitly states that a prior refund path was removed:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [2](#0-1) 

The comment `// try return funds now removed` is a direct admission that the protocol previously refunded the fee on failure, and that behavior was deliberately removed. The fee is now permanently consumed by `sequencerFee` regardless of whether the transaction succeeds.

Slow-mode transactions that can fail include:
- `WithdrawCollateral` — fails if `getHealth(sender, INITIAL) < 0` after market movement during the 3-day delay
- `LinkSigner` — fails if `requireSubaccount` rejects the sender [3](#0-2) 

---

### Impact Explanation

A user who submits a slow-mode `WithdrawCollateral` transaction pays `SLOW_MODE_FEE` upfront. If the market moves adversely during the 3-day delay and the user's health drops below the initial threshold, the withdrawal fails silently. The user receives nothing — no withdrawal and no fee refund. The fee is permanently credited to `sequencerFee` and later claimed by the protocol via `DumpFees`. This is a direct, concrete loss of user funds through no fault of their own, analogous to the reported vulnerability where stop-order execution fees were lost when positions were force-closed.

---

### Likelihood Explanation

The 3-day slow-mode delay (`SLOW_MODE_TX_DELAY`) creates a meaningful window for market conditions to change. Any user who submits a `WithdrawCollateral` slow-mode transaction near their health boundary is at risk. Liquidation events or large price swings during the delay window can cause the transaction to fail. This is a realistic, non-adversarial scenario that can affect ordinary users. [4](#0-3) 

---

### Recommendation

Restore the fee refund on slow-mode execution failure. In the catch block of `_executeSlowModeTransaction`, return `SLOW_MODE_FEE` to the original `txn.sender` by crediting their quote balance (via `spotEngine.updateBalance`) and decrementing `slowModeFees` accordingly. This mirrors the behavior that was previously present before the refund path was removed.

---

### Proof of Concept

1. User calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload. `chargeSlowModeFee` deducts `SLOW_MODE_FEE` from the user's quote balance; `slowModeFees += SLOW_MODE_FEE`.
2. The transaction is stored in `slowModeTxs` with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days).
3. During the 3-day window, the user's collateral value drops due to price movement, pushing their health below the initial threshold.
4. The sequencer calls `_executeSlowModeTransaction`. Inside `processSlowModeTransactionImpl`, `clearinghouse.withdrawCollateral` is called, which reaches `require(getHealth(sender, INITIAL) >= 0, ERR_SUBACCT_HEALTH)` and reverts.
5. The outer `try/catch` in `_executeSlowModeTransaction` silently swallows the revert. No refund is issued.
6. The user has lost `SLOW_MODE_FEE` and received no withdrawal. The fee remains in `sequencerFee` and is later claimed by the protocol. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L332-385)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L185-229)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-420)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
```
