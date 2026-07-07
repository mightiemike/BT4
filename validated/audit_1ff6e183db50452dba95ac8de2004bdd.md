### Title
Removed Fund-Return Logic in Slow Mode Catch Block Permanently Locks User Deposit Collateral — (`core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint._executeSlowModeTransaction` wraps `processSlowModeTransaction` in a try/catch. When the inner call reverts, the catch block does nothing — a comment at line 226 explicitly reads `// try return funds now removed`. For `DepositCollateral` slow mode transactions, the user's tokens are transferred to the `Clearinghouse` at deposit time (before the slow mode tx is ever executed). If the deferred execution later fails silently, those tokens remain in the `Clearinghouse` with no accounting entry and no recovery path.

---

### Finding Description

The deposit flow has two distinct phases separated by up to a 3-day `SLOW_MODE_TX_DELAY`:

**Phase 1 — `depositCollateralWithReferral` (Endpoint.sol lines 123–167):**
`handleDepositTransfer` immediately moves tokens from the user into the `Clearinghouse`. A `SlowModeTx` of type `DepositCollateral` is then queued. [1](#0-0) 

**Phase 2 — `_executeSlowModeTransaction` (Endpoint.sol lines 185–229):**
When the sequencer (or anyone after the timeout) executes the queued tx, it calls `this.processSlowModeTransaction` inside a try/catch. On failure the catch block is empty — the comment `// try return funds now removed` confirms the recovery code was deliberately deleted. [2](#0-1) 

**Phase 2 inner call — `processSlowModeTransactionImpl` (EndpointTx.sol lines 209–216):**
For `DepositCollateral`, this calls `clearinghouse.depositCollateral(txn)`, which is the only step that credits the user's subaccount balance. [3](#0-2) 

If `clearinghouse.depositCollateral` reverts for any reason, the catch block swallows the error, `nSubmissions` is still incremented, the `SlowModeTx` entry is already deleted (`delete slowModeTxs[_slowModeConfig.txUpTo++]` at line 194), and the user's tokens remain in the `Clearinghouse` with no corresponding balance entry and no mechanism to reclaim them. [4](#0-3) 

The same pattern applies to `DepositInsurance` slow mode transactions, where `handleDepositTransfer` is called in `submitSlowModeTransactionImpl` before the tx is queued. [5](#0-4) 

---

### Impact Explanation

A user's deposited ERC-20 collateral is permanently locked inside the `Clearinghouse` contract. The user's subaccount balance is never credited, so they cannot trade, withdraw, or recover the funds through any protocol-supported path. The `SlowModeTx` record is deleted on execution, so the deposit cannot be replayed. There is no admin sweep function scoped to this scenario.

---

### Likelihood Explanation

The 3-day `SLOW_MODE_TX_DELAY` between token transfer and subaccount credit creates a meaningful window for state changes that can cause `clearinghouse.depositCollateral` to revert:

- The product can be delisted between deposit and execution (a `DelistProduct` slow mode tx can be submitted by the owner and processed by the sequencer within that window).
- Any new validation added to `clearinghouse.depositCollateral` in an upgrade during the delay window.
- A user becoming sanctioned between deposit and execution (sanction checks exist throughout the clearinghouse path).

The entry point `depositCollateralWithReferral` is callable by any unprivileged user, and `executeSlowModeTransaction` is also callable by anyone after the timeout, making the trigger fully reachable without privileged access. [6](#0-5) 

---

### Recommendation

Restore fund-return logic in the catch block of `_executeSlowModeTransaction`. When a `DepositCollateral` slow mode transaction fails, the `Clearinghouse` should transfer the deposited tokens back to the original depositor. The `SlowModeTx` struct already stores the `sender` address, which can be used as the refund target. Alternatively, re-encode the original deposit amount in the queued transaction payload so the catch block can issue a precise refund without re-reading clearinghouse state.

---

### Proof of Concept

1. User calls `depositCollateralWithReferral(subaccount, productId, amount, referral)`.
2. `handleDepositTransfer` moves `amount` tokens from the user to `Clearinghouse`. A `SlowModeTx` is queued at index `txCount`.
3. Owner submits a `DelistProduct` slow mode tx for `productId` and the sequencer processes it before the user's deposit tx matures.
4. After 3 days, anyone calls `executeSlowModeTransaction()`.
5. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(sender, tx)`.
6. Inside, `clearinghouse.depositCollateral(txn)` reverts because `productId` is delisted.
7. The catch block executes — `// try return funds now removed` — nothing happens.
8. `slowModeTxs[txUpTo]` was already deleted at line 194; `nSubmissions` is incremented at line 234.
9. The user's `amount` tokens remain in `Clearinghouse` with no subaccount credit and no recovery path. [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L144-165)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
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

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointTx.sol (L345-354)
```text
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
```
