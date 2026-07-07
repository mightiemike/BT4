### Title
Missing `latestUnlockTime` Deadline in `MintNlp` Allows NLP Lock Period to Extend Beyond User Expectation — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

The `MintNlp` transaction struct contains no deadline or `latestUnlockTime` parameter. The NLP lock expiry is stamped at **execution time** as `getOracleTime() + NLP_LOCK_PERIOD`. When a user submits `MintNlp` via the slow mode path — a permissionless fallback that imposes a mandatory 3-day queue delay — the resulting lock expiry is 3 days later than the user intended, with no way to cancel or bound the outcome.

---

### Finding Description

**Lock time is set at execution, not at signing.**

In `SpotEngine.handleNlpLockedBalance`, every positive NLP balance delta creates a queue entry whose unlock timestamp is computed at the moment the transaction executes:

```solidity
// core/contracts/SpotEngine.sol  lines 162-165
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD   // stamped at execution time
});
``` [1](#0-0) 

`NLP_LOCK_PERIOD` is 4 days and `getOracleTime()` returns the sequencer-provided oracle time at the moment of on-chain execution:

```solidity
// core/contracts/common/Constants.sol  line 52
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
``` [2](#0-1) 

```solidity
// core/contracts/EndpointGated.sol  lines 21-23
function getOracleTime() internal view returns (uint128) {
    return IEndpoint(endpoint).getTime();
}
``` [3](#0-2) 

**The `MintNlp` struct carries no deadline field.**

```solidity
// core/contracts/interfaces/IEndpoint.sol  lines 112-116
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64 nonce;
    // ← no latestUnlockTime / deadline
}
``` [4](#0-3) 

**`MintNlp` is accepted by the slow mode queue.**

`submitSlowModeTransactionImpl` explicitly reverts only for `DepositCollateral`, restricts a fixed set of admin types to `owner()`, and routes everything else — including `MintNlp` — into the default branch that charges a slow mode fee and enqueues the transaction with a **mandatory 3-day delay**:

```solidity
// core/contracts/EndpointTx.sol  lines 376-380
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
    sender: sender,
    tx: transaction
});
``` [5](#0-4) 

`SLOW_MODE_TX_DELAY` is 3 days:

```solidity
// core/contracts/common/Constants.sol  line 50
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
``` [6](#0-5) 

**Concrete execution path:**

1. User signs `MintNlp` at time **T**, expecting NLP to unlock at **T + 4 days**.
2. User calls `submitSlowModeTransaction` with the encoded `MintNlp` payload, paying the slow mode fee.
3. Transaction is queued with `executableAt = T + 3 days`.
4. Sequencer processes the slow mode queue at time **T + 3 days** (earliest possible).
5. `processTransactionImpl` → `clearinghouse.mintNlp()` → `spotEngine.updateBalance(NLP_PRODUCT_ID, sender, nlpAmount)` → `handleNlpLockedBalance`.
6. `unlockedAt` is stamped as `getOracleTime() + NLP_LOCK_PERIOD ≈ (T + 3 days) + 4 days = T + 7 days`.
7. User's NLP is locked until **T + 7 days** — 3 days beyond their expectation — with no recourse.

The call chain is:

```
EndpointTx.submitSlowModeTransactionImpl (queues)
  → EndpointTx.processTransactionImpl [ExecuteSlowMode]
    → Clearinghouse.mintNlp
      → SpotEngine.updateBalance (NLP_PRODUCT_ID)
        → SpotEngine.handleNlpLockedBalance
          → unlockedAt = getOracleTime() + NLP_LOCK_PERIOD   ← stamped too late
``` [7](#0-6) [8](#0-7) 

---

### Impact Explanation

`burnNlp` enforces that only unlocked NLP can be redeemed:

```solidity
// core/contracts/Clearinghouse.sol  lines 498-501
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [9](#0-8) 

A user whose NLP is locked 3 days longer than expected cannot burn it during that window. If the user minted NLP to provide short-term liquidity and planned to exit at T + 4 days (e.g., to meet a margin call, repay a loan, or respond to market conditions), the unexpected extension to T + 7 days constitutes a **temporary freeze of funds beyond the user's control**. The user cannot cancel the queued slow mode transaction after submission.

---

### Likelihood Explanation

The slow mode path is a permissionless, documented fallback that any user can invoke — it requires only paying the `SLOW_MODE_FEE` ($1). Any user who mints NLP via slow mode (e.g., when the sequencer is unavailable or slow) will deterministically experience the 3-day extension. The impact is not probabilistic; it is guaranteed for every slow-mode `MintNlp` submission. Likelihood is **medium** (requires use of the slow mode path) with **high** impact (funds locked beyond user intent with no cancellation mechanism).

---

### Recommendation

Add a `latestUnlockTime` field to `MintNlp` and enforce it before stamping the lock:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64 nonce;
    uint64 latestUnlockTime; // user-specified upper bound on unlockedAt
}
```

In `handleNlpLockedBalance` (or `mintNlp`), revert if the computed unlock time would exceed the user's bound:

```solidity
require(
    getOracleTime() + NLP_LOCK_PERIOD <= txn.latestUnlockTime,
    "MintNlp: lock would expire too late"
);
```

This mirrors the standard deadline pattern used in AMM swap functions and directly addresses the root cause.

---

### Proof of Concept

```
T+0:  User signs MintNlp{sender, quoteAmount=1000e18, nonce=N}
      User calls submitSlowModeTransaction(encodedMintNlp), pays $1 fee
      → slowModeTxs[k] = {executableAt: T + 3 days, tx: encodedMintNlp}

T+3d: Sequencer calls processTransaction(ExecuteSlowMode)
      → processTransactionImpl(MintNlp)
      → clearinghouse.mintNlp(txn, oraclePrice, pools, rebalance)
      → spotEngine.updateBalance(NLP_PRODUCT_ID, sender, nlpAmount)
      → handleNlpLockedBalance(sender, nlpAmount)
      → unlockedAt = getOracleTime() + NLP_LOCK_PERIOD
                   = (T + 3 days) + 4 days
                   = T + 7 days   ← user expected T + 4 days

T+4d: User attempts burnNlp → reverts ERR_UNLOCKED_NLP_INSUFFICIENT
T+7d: NLP finally unlocks — 3 days late, no compensation
```

### Citations

**File:** core/contracts/SpotEngine.sol (L139-174)
```text
    function handleNlpLockedBalance(bytes32 subaccount, int128 amountDelta)
        internal
    {
        _assertInternal();

        // N_ACCOUNT is not limited by lock period
        if (subaccount == N_ACCOUNT) return;

        tryUnlockNlpBalance(subaccount);
        if (amountDelta > 0) {
            NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
                subaccount
            ];
            if (
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
                queue
                    .balances[queue.balanceCount - 1]
                    .balance
                    .amount += amountDelta;
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
            }
        } else if (amountDelta < 0) {
            Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
                .unlockedBalanceSum;
            balanceSum.amount += amountDelta;
            nlpLockedBalanceQueues[subaccount].unlockedBalanceSum = balanceSum;
        }
    }
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```

**File:** core/contracts/EndpointGated.sol (L21-23)
```text
    function getOracleTime() internal view returns (uint128) {
        return IEndpoint(endpoint).getTime();
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L112-116)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }
```

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```
