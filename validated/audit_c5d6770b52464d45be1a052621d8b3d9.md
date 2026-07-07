### Title
Fee-Free Slow Mode Queue Inflation via `depositCollateralWithReferral` — (File: `core/contracts/Endpoint.sol`)

---

### Summary

The `depositCollateralWithReferral` function enqueues `DepositCollateral` slow mode transactions without charging the `SLOW_MODE_FEE` ($1). Any unprivileged caller can inflate the slow mode queue at a cost of only `MIN_DEPOSIT_AMOUNT` ($0.1 per entry after the first deposit), bypassing the protocol's spam-prevention metering entirely. Since deposited tokens are credited to the subaccount and recoverable via withdrawal, the attacker's net cost is gas only.

---

### Finding Description

The slow mode queue is the protocol's censorship-resistance escape hatch. To prevent spam, `submitSlowModeTransactionImpl` charges a `SLOW_MODE_FEE` of $1 for every user-submitted slow mode transaction: [1](#0-0) 

`DepositCollateral` is explicitly blocked from this path with a hard revert: [2](#0-1) 

The only way to enqueue a `DepositCollateral` slow mode transaction is through `depositCollateralWithReferral`, which is `public` and writes directly to `slowModeTxs` with **no fee charge**: [3](#0-2) 

The sole cost gate is `isValidDepositAmount`, which enforces `MIN_DEPOSIT_AMOUNT = $0.1` for existing subaccounts and `MIN_FIRST_DEPOSIT_AMOUNT = $5` for new ones: [4](#0-3) 

The `SLOW_MODE_FEE` is $1: [5](#0-4) 

Because `handleDepositTransfer` routes tokens to the clearinghouse and the slow mode transaction credits them to the subaccount, the attacker's tokens are not destroyed — they are deposited and later withdrawable. The net attacker cost is gas only.

---

### Impact Explanation

An attacker can flood the slow mode queue with `DepositCollateral` entries at $0.1 per entry (after the initial $5 first-deposit), bypassing the $1 slow mode fee:

1. **Sequencer resource drain without compensation**: The sequencer must process every enqueued slow mode transaction. Deposit entries generated this way carry no fee revenue, so the sequencer bears processing cost without the $1-per-entry compensation the fee mechanism was designed to provide.
2. **Delayed legitimate slow mode transactions**: The queue is consumed FIFO via `txUpTo`. Legitimate `WithdrawCollateral` or `LinkSigner` slow mode transactions submitted after the spam batch are pushed behind the inflated queue, extending their effective delay beyond the 3-day `SLOW_MODE_TX_DELAY`. [6](#0-5) 

---

### Likelihood Explanation

High. `depositCollateralWithReferral` is `public` with no caller restriction. The attacker recovers their deposited tokens (minus gas) by later submitting a `WithdrawCollateral` slow mode transaction. The attack is economically rational whenever the gas cost of N deposit calls is less than the $0.9 × N fee savings relative to the $1 slow mode fee.

---

### Recommendation

Enforce a minimum deposit amount equal to or greater than `SLOW_MODE_FEE` when `depositCollateralWithReferral` is called by an address other than a registered `DirectDepositV1` contract, or charge the slow mode fee inside `depositCollateralWithReferral` in addition to the token transfer. Alternatively, track a per-address deposit-enqueue rate limit to bound queue inflation.

---

### Proof of Concept

```
// Step 1: First deposit to register subaccount (cost: $5 + gas)
endpoint.depositCollateralWithReferral(
    attackerSubaccount, QUOTE_PRODUCT_ID, 5_000_000, "-1"
);

// Step 2: Repeat N times (cost: $0.10 + gas each, vs $1 slow mode fee)
for (uint i = 0; i < N; i++) {
    endpoint.depositCollateralWithReferral(
        attackerSubaccount, QUOTE_PRODUCT_ID, 100_000, "-1"
    );
}
// Result: slowModeConfig.txCount increased by N+1 with zero slow mode fee paid.
// Legitimate WithdrawCollateral slow mode txs submitted after this are
// delayed behind N+1 queued deposit entries.

// Step 3 (optional): Recover tokens after 3-day delay via WithdrawCollateral slow mode tx.
```

The `slowModeTxs` mapping grows by N+1 entries, each a `DepositCollateral` transaction, with `slowModeFees` unchanged — confirming zero fee was collected. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/EndpointTx.sol (L343-344)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

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

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
```
