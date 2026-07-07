### Title
`WithdrawCollateralV2` Slow-Mode (Permissionless) Withdrawal Always Silently Fails — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`processSlowModeTransactionImpl` handles `WithdrawCollateral` but omits `WithdrawCollateralV2`, while `processTransactionImpl` (fast/sequencer path) handles both. Any user who submits a `WithdrawCollateralV2` transaction through the permissionless slow-mode path pays the slow-mode fee, has their transaction queued, and then watches it silently fail when executed — permanently breaking the censorship-resistance guarantee for that transaction type.

---

### Finding Description

The Nado protocol exposes two execution paths for user transactions:

1. **Fast mode** (`processTransactionImpl`) — sequencer-submitted, handles all signed transaction types.
2. **Slow mode** (`submitSlowModeTransactionImpl` → `processSlowModeTransactionImpl`) — permissionless, intended as a censorship-resistance escape hatch.

`submitSlowModeTransactionImpl` accepts any transaction type that is not explicitly blocked or owner-gated, charging a slow-mode fee and queuing it: [1](#0-0) 

`WithdrawCollateralV2` is not in any of the explicit branches, so it falls into the `else` path, pays the fee, and is queued.

However, `processSlowModeTransactionImpl` handles `WithdrawCollateral` but has **no branch for `WithdrawCollateralV2`**: [2](#0-1) 

The function ends with `else { revert(); }`: [3](#0-2) 

When the queued `WithdrawCollateralV2` transaction is eventually executed via `_executeSlowModeTransaction`, the revert is silently swallowed by the surrounding `try/catch`: [4](#0-3) 

The transaction is deleted from the queue, the slow-mode fee is consumed, and the withdrawal never occurs.

By contrast, `processTransactionImpl` (fast mode) correctly handles **both** `WithdrawCollateral` and `WithdrawCollateralV2`: [5](#0-4) [6](#0-5) 

This is structurally identical to the ZetaChain M-03 bug: one path (`AddToOutTxTracker` / `processTransactionImpl`) handles both types; the other path (`AddToInTxTracker` / `processSlowModeTransactionImpl`) handles only one.

---

### Impact Explanation

- A user who is being censored by the sequencer and needs to withdraw using `WithdrawCollateralV2` (which adds `sendTo` and `appendix` fields absent from `WithdrawCollateral`) cannot do so via the permissionless slow-mode path.
- The user loses the slow-mode fee (a concrete asset delta).
- The withdrawal silently fails with no on-chain error, leaving the user's collateral locked with no recourse except to use the sequencer-controlled fast path — defeating the censorship-resistance design.

---

### Likelihood Explanation

Any user who:
1. Wants to withdraw to a custom `sendTo` address (only available in `WithdrawCollateralV2`), **and**
2. Is being censored by the sequencer (or simply prefers the permissionless path)

will trigger this. The entry point is the public `submitSlowModeTransaction` function, callable by any EOA with no special privileges.

---

### Recommendation

Add a `WithdrawCollateralV2` branch to `processSlowModeTransactionImpl` in `core/contracts/EndpointTx.sol`, mirroring the existing `WithdrawCollateral` branch but decoding `SignedWithdrawCollateralV2` and calling `clearinghouse.withdrawCollateral` with the `sendTo` field from the decoded struct.

---

### Proof of Concept

1. User calls `submitSlowModeTransaction` with a `WithdrawCollateralV2` transaction (type byte = `WithdrawCollateralV2`).
2. `submitSlowModeTransactionImpl` does not match any explicit branch → falls to `else` → charges `SLOW_MODE_FEE`, queues the transaction.
3. After `SLOW_MODE_TX_DELAY` (3 days), anyone calls `executeSlowModeTransaction`.
4. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(txn.sender, txn.tx)` inside a `try/catch`.
5. `processSlowModeTransactionImpl` reaches `else { revert(); }` — no `WithdrawCollateralV2` branch exists.
6. The `catch` block silently absorbs the revert (gas check passes for a normal revert).
7. The slow-mode entry is deleted, the fee is gone, and the withdrawal never executes.

### Citations

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

**File:** core/contracts/EndpointTx.sol (L327-329)
```text
        } else {
            revert();
        }
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L205-228)
```text
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
```
