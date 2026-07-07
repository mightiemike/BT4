### Title
Silent Slow-Mode Execution Failure Permanently Consumes Pre-Paid Slow Mode Fee With No Refund Path — (`core/contracts/Endpoint.sol`)

---

### Summary

`_executeSlowModeTransaction` in `Endpoint.sol` silently swallows execution failures after the slow-mode fee has already been irrevocably transferred from the user's wallet at submission time. The comment `// try return funds now removed` (line 226) confirms a refund path once existed and was deliberately removed. Any user whose slow-mode `WithdrawCollateral` (or other user-submitted slow-mode transaction) fails during execution permanently loses their pre-paid fee with no on-chain recovery mechanism.

---

### Finding Description

The slow-mode flow is a two-phase process:

**Phase 1 — Submission (`submitSlowModeTransactionImpl`, `EndpointTx.sol` line 332–385):**
For any user-submitted slow-mode transaction (e.g., `WithdrawCollateral`, `LinkSigner`), the slow-mode fee is charged immediately from the user's wallet via an ERC20 `transferFrom`:

```
chargeSlowModeFee(_getQuote(), sender);   // line 370
slowModeFees += SLOW_MODE_FEE;            // line 371
```

`chargeSlowModeFee` (`EndpointStorage.sol` line 83–93) performs:
```
token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
```

The transaction is then queued with a 3-day delay (`SLOW_MODE_TX_DELAY`).

**Phase 2 — Execution (`_executeSlowModeTransaction`, `Endpoint.sol` line 185–228):**
The queued transaction is deleted from storage first (line 194), then executed inside a `try/catch`:

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];          // dequeued unconditionally

try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed                     // ← refund path explicitly removed
}
```

If `processSlowModeTransaction` reverts for any non-OOG reason (health check failure, utilization breach, overflow, etc.), the catch block silently discards the error. The slow-mode fee already transferred in Phase 1 is never returned. The transaction is permanently gone from the queue.

For `WithdrawCollateral` specifically, `processSlowModeTransactionImpl` calls `clearinghouse.withdrawCollateral` (line 223–229), which enforces a health check (`require(getHealth(...) >= 0, ERR_SUBACCT_HEALTH)`, `Clearinghouse.sol` line 419) and a utilization check (`spotEngine.assertUtilization`, line 413). Both can legitimately fail after the 3-day delay due to market movement, funding rate accrual, or position changes — conditions entirely outside the user's control at execution time.

---

### Impact Explanation

- The slow-mode fee is a real ERC20 quote-token amount transferred from the user's wallet at submission.
- If execution fails silently, the fee is consumed by the protocol (`slowModeFees` accounting) but the user receives no service.
- The transaction is deleted from the queue; there is no retry, no refund, and no on-chain recovery path.
- The user must submit a new slow-mode transaction and pay the fee again to retry.
- The comment `// try return funds now removed` is direct evidence that the refund invariant was intentionally broken.

**Corrupted asset delta:** The user's wallet balance is reduced by `SLOW_MODE_FEE` in quote tokens with no corresponding protocol credit or service rendered.

---

### Likelihood Explanation

- The 3-day `SLOW_MODE_TX_DELAY` is hardcoded. Any user who submits a `WithdrawCollateral` slow-mode transaction while their account is near the health boundary can find the execution failing at the 3-day mark due to normal market movement, funding rate accrual, or a partial liquidation in the interim.
- No adversarial action is required — this is a normal user flow that can fail due to protocol-internal state changes.
- The slow-mode path is the only permissionless withdrawal path available when the sequencer is unresponsive, making it a critical user-facing flow.

---

### Recommendation

Restore the refund path inside the `catch` block of `_executeSlowModeTransaction`. When `processSlowModeTransaction` reverts for a non-OOG reason, the slow-mode fee should be returned to `txn.sender`:

```solidity
catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // Refund slow-mode fee to sender
    _getQuote().safeTransfer(txn.sender, clearinghouse.getSlowModeFee());
    slowModeFees -= SLOW_MODE_FEE;
}
```

Additionally, consider emitting an event on silent failure so users can detect that their slow-mode transaction was dropped and act accordingly.

---

### Proof of Concept

1. User's subaccount has collateral with health ratio just above the initial margin requirement.
2. User calls `submitSlowModeTransaction(withdrawCollateralTx)` on `Endpoint.sol`.
3. `submitSlowModeTransactionImpl` charges `SLOW_MODE_FEE` quote tokens from the user's wallet via `chargeSlowModeFee` and queues the transaction.
4. Over the next 3 days, funding rate accrual or a small adverse price move reduces the subaccount's health below the initial margin threshold.
5. The sequencer (or anyone) calls `executeSlowModeTransaction()`.
6. `_executeSlowModeTransaction` deletes the queued tx and calls `processSlowModeTransaction` in a `try/catch`.
7. Inside, `clearinghouse.withdrawCollateral` reaches `require(getHealth(sender, INITIAL) >= 0, ERR_SUBACCT_HEALTH)` and reverts.
8. The `catch` block fires; the OOG heuristic does not trigger (normal revert, not OOG).
9. Execution continues silently. The slow-mode fee is permanently consumed. The user's withdrawal is not processed. The queue entry is gone.
10. User must pay another slow-mode fee to retry — with no guarantee the same failure won't recur.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/Endpoint.sol (L193-227)
```text
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
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L408-419)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
