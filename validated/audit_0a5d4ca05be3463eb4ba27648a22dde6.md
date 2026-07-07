### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` When `openInterest == 0` Permanently Blocks Bad-Debt Finalization — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when socializing residual negative `vQuoteBalance`. No zero-guard exists. `MathSD21x18.div` explicitly reverts with `"DBZ"` when the divisor is zero. Because `_finalizeSubaccount` in `ClearinghouseLiq.sol` calls `socializeSubaccount` as part of the liquidation finalization path, a revert here permanently blocks resolution of the liquidatee's bad debt.

---

### Finding Description

**Root cause — `PerpEngine.socializeSubaccount`, lines 164–168:**

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest          // ← no zero-check
    );
``` [1](#0-0) 

`MathSD21x18.div` enforces `require(y != 0, ERR_DIV_BY_ZERO)`:

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
``` [2](#0-1) 

**How `openInterest` reaches zero:**

`_updateBalance` tracks `openInterest` as the sum of absolute position sizes across all participants:

```solidity
state.openInterest -= balance.amount.abs();   // pre-update
...
state.openInterest += balance.amount;          // post-update (long)
// or
state.openInterest -= balance.amount;          // post-update (short)
``` [3](#0-2) 

When every participant closes their position (`balance.amount == 0`), `openInterest` reaches exactly `0`. The `updateStates` function already handles this case with an early `continue`:

```solidity
if (state.openInterest == 0) {
    continue;
}
``` [4](#0-3) 

…but `socializeSubaccount` has no equivalent guard.

**How the finalization path reaches the divide:**

`_finalizeSubaccount` (triggered when `txn.productId == type(uint32).max`) first requires that all perp `balance.amount == 0`:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [5](#0-4) 

Then unconditionally calls:

```solidity
v.insurance = perpEngine.socializeSubaccount(
    txn.liquidatee,
    v.insurance
);
``` [6](#0-5) 

The liquidatee's `balance.amount == 0` contributes nothing to `openInterest`. If all other participants have also closed their positions, `state.openInterest == 0`. If insurance is insufficient to fully cover the liquidatee's negative `vQuoteBalance`, the inner `if (balance.vQuoteBalance < 0)` branch is entered and the division reverts.

The revert propagates through `liquidateSubaccountImpl`:

```solidity
if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
``` [7](#0-6) 

…and through `liquidateSubaccount` in `Clearinghouse.sol`:

```solidity
(bool success, bytes memory result) = clearinghouseLiq.delegatecall(
    liquidateSubaccountCall
);
require(success, string(result));
``` [8](#0-7) 

---

### Impact Explanation

The liquidatee's bad debt is permanently unresolvable as long as `openInterest` remains zero for that perp product. The protocol cannot:
- Socialize the loss across other participants (there are none)
- Zero out the negative `vQuoteBalance`
- Complete finalization and close the subaccount

The insurance fund cannot be applied either, because the revert occurs even after the insurance-cover step, when residual negative balance remains. The bad debt is frozen on-chain with no protocol-level escape hatch.

---

### Likelihood Explanation

The preconditions are realistic:
1. A liquidatee holds `balance.amount == 0` with negative `vQuoteBalance` — this is the normal state after a position is closed at a loss (PnL settles into `vQuoteBalance`).
2. All other participants close their positions — plausible in low-liquidity or end-of-lifecycle markets, or if the liquidatee was the last active participant.
3. Insurance is insufficient — the insurance fund is finite and can be depleted.

No admin access, governance capture, or privileged key is required. Any liquidator calling `liquidateSubaccount` with `productId == type(uint32).max` triggers the path.

---

### Recommendation

Add a zero-check on `state.openInterest` before the division in `socializeSubaccount`. If `openInterest == 0`, there are no other participants to socialize the loss to; the bad debt should be absorbed entirely by the insurance fund or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest != 0) {
        int128 fundingPerShare = -balance.vQuoteBalance.div(
            state.openInterest
        );
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
    }
    // If openInterest == 0, no one to socialize to; loss is written off
    balance.vQuoteBalance = 0;
}
``` [9](#0-8) 

---

### Proof of Concept

1. Deploy the protocol with one perp product (e.g., `productId = 2`).
2. Liquidatee opens a long position, price drops, liquidatee closes the position. Result: `balance.amount == 0`, `balance.vQuoteBalance < 0`.
3. All other participants close their positions. Result: `state.openInterest == 0`.
4. Drain or set insurance to a value less than `|balance.vQuoteBalance|`.
5. Call `liquidateSubaccount` with `productId = type(uint32).max` (finalization path).
6. Observe revert with `"DBZ"` from `MathSD21x18.div` inside `socializeSubaccount`.
7. Assert: no subsequent call to `liquidateSubaccount` with `productId = type(uint32).max` can succeed while `openInterest == 0` and residual negative `vQuoteBalance` exists.

### Citations

**File:** core/contracts/PerpEngine.sol (L164-172)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
                }
```

**File:** core/contracts/libraries/MathSD21x18.sol (L62-65)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
```

**File:** core/contracts/PerpEngineState.sol (L30-51)
```text
        state.openInterest -= balance.amount.abs();
        int128 cumulativeFundingAmountX18 = (balance.amount > 0)
            ? state.cumulativeFundingLongX18
            : state.cumulativeFundingShortX18;
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);

        // apply delta
        balance.amount += balanceDelta;

        // apply vquote
        balance.vQuoteBalance += deltaQuote;

        // post update
        if (balance.amount > 0) {
            state.openInterest += balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingLongX18;
        } else {
            state.openInterest -= balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingShortX18;
        }
```

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```

**File:** core/contracts/ClearinghouseLiq.sol (L319-319)
```text
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L620-620)
```text
        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
```

**File:** core/contracts/Clearinghouse.sol (L658-661)
```text
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
```
