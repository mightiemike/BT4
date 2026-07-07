### Title
Sanctions Check Performed at Slow Mode Submission but Not Re-Enforced at Execution — (`File: core/contracts/EndpointTx.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

The `requireUnsanctioned` guard is applied once when a slow mode transaction is submitted, but is never re-evaluated when that transaction is executed up to three days later. A user who is added to the sanctions list after submitting a `WithdrawCollateral` slow mode transaction can still have that withdrawal executed, bypassing the protocol's sanctions enforcement entirely.

---

### Finding Description

`submitSlowModeTransactionImpl` calls `requireUnsanctioned(sender)` before queuing the transaction: [1](#0-0) 

This is the only point at which the sender's sanctions status is verified. The queued transaction is stored with a fixed `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (hardcoded to three days).

When the transaction is later dequeued and executed — either by the sequencer via `_executeSlowModeTransaction(fromSequencer=true)` or by any external caller via `executeSlowModeTransaction()` — no sanctions re-check is performed: [2](#0-1) 

`executeSlowModeTransaction()` has no access control and no sanctions guard: [3](#0-2) 

`processSlowModeTransactionImpl`, which handles the actual `WithdrawCollateral` logic, also contains no `requireUnsanctioned` call: [4](#0-3) 

The `requireUnsanctioned` definition confirms it reads live state from the external `sanctions` contract on every call, meaning a re-check at execution time would correctly reflect a post-submission designation: [5](#0-4) 

---

### Impact Explanation

A sanctioned user can withdraw collateral from the protocol after being designated. The invariant "sanctioned addresses cannot move funds through the protocol" holds only at submission time, not at the moment assets actually leave the clearinghouse. The corrupted state delta is a direct ERC20 transfer of collateral to a sanctioned address, which constitutes a compliance bypass with real asset movement.

---

### Likelihood Explanation

Medium. OFAC and similar designations can occur at any time and are not predictable. The three-day slow mode delay creates a concrete window during which a user's sanctions status can change. Because `executeSlowModeTransaction()` is permissionless, neither the sequencer nor the protocol can prevent execution once the delay expires. A user who anticipates a designation could also front-run it by submitting a withdrawal immediately before the designation is processed on-chain.

---

### Recommendation

Re-check `requireUnsanctioned` at execution time. In `processSlowModeTransactionImpl`, add a `requireUnsanctioned(sender)` call at the top of the function, mirroring the check already present in `submitSlowModeTransactionImpl`. This ensures that a change in sanctions status between submission and execution is respected before any funds are moved.

---

### Proof of Concept

1. User `A` (not yet sanctioned) calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload.
2. `submitSlowModeTransactionImpl` runs `requireUnsanctioned(A)` — passes. Transaction is queued with `executableAt = now + 3 days`.
3. `A` is added to the sanctions list (e.g., OFAC designation propagated to the on-chain `ISanctionsList`).
4. After three days, any address calls `executeSlowModeTransaction()`.
5. `_executeSlowModeTransaction` dequeues the transaction and calls `processSlowModeTransaction(A, tx)`.
6. `processSlowModeTransactionImpl` decodes the `WithdrawCollateral` payload and calls `clearinghouse.withdrawCollateral(A.sender, productId, amount, address(0), nSubmissions)` — no sanctions check — funds are transferred to the sanctioned address. [4](#0-3) [1](#0-0)

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

**File:** core/contracts/EndpointTx.sol (L374-380)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
