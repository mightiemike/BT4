### Title
Division-by-Zero in `socializeSubaccount` Permanently Blocks Finalization When Sole Position Holder Closes — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when a subaccount's `vQuoteBalance` is still negative after insurance coverage. If the subaccount being finalized was the sole holder of open interest in a product and their position was already closed (`balance.amount == 0`), `state.openInterest` is zero, causing `MathSD21x18.div` to revert with `"DBZ"`. This permanently blocks finalization of the subaccount and locks any remaining collateral.

---

### Finding Description

**Root cause — `MathSD21x18.div` explicit zero-check:**

`MathSD21x18.div` explicitly guards against zero denominators: [1](#0-0) 

**The division in `socializeSubaccount`:** [2](#0-1) 

`state.openInterest` is passed as the denominator with no zero-guard. If it is zero, the call reverts with `"DBZ"`.

**How `openInterest` reaches zero while `vQuoteBalance` stays negative:**

`_updateBalance` maintains `openInterest` as the sum of absolute position sizes: [3](#0-2) 

When a subaccount closes its position (`balance.amount` → 0), the pre-update step subtracts `balance.amount.abs()` from `openInterest`, and the post-update step adds `0`. If this subaccount was the sole holder, `state.openInterest` drops to exactly `0`. However, `balance.vQuoteBalance` retains the realized loss from the closed position — it is not zeroed out by closing.

**Why `_finalizeSubaccount` reaches `socializeSubaccount` in this state:**

`_finalizeSubaccount` only checks that `balance.amount == 0` for all perp products: [4](#0-3) 

A subaccount with `amount == 0` and `vQuoteBalance < 0` passes this check. The negative-PnL settlement loop (lines 346–366) only offsets `vQuoteBalance` against a positive quote balance; if the quote balance is also zero or insufficient, `vQuoteBalance` remains negative. Then: [5](#0-4) 

`socializeSubaccount` is called unconditionally, and if insurance is also exhausted, the division by zero fires.

---

### Impact Explanation

- **Permanent finalization DoS**: The only path to finalize a subaccount is `_finalizeSubaccount → socializeSubaccount`. Once this reverts, no liquidator can ever finalize the subaccount. It is permanently stuck in a liquidatable-but-unfinalizable state.
- **Locked collateral**: Any remaining quote balance in the subaccount is inaccessible — it cannot be distributed to the insurance fund or returned to the owner.
- **Broken loss socialization**: The protocol's mechanism for spreading unrecoverable losses across other participants is completely bypassed for the affected product.

This matches the Critical scope: permanent lock of collateral and protocol-controlled assets.

---

### Likelihood Explanation

The preconditions are realistic and reachable through normal protocol usage:

1. A subaccount opens a leveraged perp position and is the only participant in that product (possible for low-liquidity or newly listed products).
2. The position is closed (e.g., via an offchain exchange fill), leaving `amount = 0` and `vQuoteBalance < 0` (realized loss).
3. The subaccount's overall health falls below maintenance (e.g., no remaining quote balance to offset the negative vQuoteBalance).
4. A liquidator submits `LiquidateSubaccount` with `productId = type(uint32).max` to trigger finalization.
5. `socializeSubaccount` reverts with `"DBZ"` — permanently.

No privileged access, governance capture, or external dependency failure is required.

---

### Recommendation

Add a zero-guard before the division in `socializeSubaccount`:

```solidity
// core/contracts/PerpEngine.sol, inside socializeSubaccount
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No other participants to socialize against;
        // absorb the loss into the insurance fund or write it off.
        state.availableSettle += balance.vQuoteBalance; // or handle as protocol loss
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

The exact accounting treatment for the zero-OI case (write off to protocol, absorb into insurance, etc.) should be decided by the protocol team, but the division must not execute when `openInterest == 0`.

---

### Proof of Concept

```solidity
// Hardhat/Foundry state-machine test (pseudocode)
// 1. Deploy protocol with one perp product (productId = 2)
// 2. Alice opens a long position: amount = 1e18, vQuoteBalance = -1000e18
// 3. Alice is the ONLY participant → state.openInterest = 1e18
// 4. Alice closes her position via offchain exchange fill:
//    updateBalance(2, alice, -1e18, +900e18)
//    → balance.amount = 0, balance.vQuoteBalance = -100e18
//    → state.openInterest = 0
// 5. Alice's quote balance = 0 → health < 0 (under maintenance)
// 6. Bob (liquidator) calls liquidateSubaccount({
//      liquidatee: alice,
//      productId: type(uint32).max,  // finalization path
//      amount: 0
//    })
// 7. _finalizeSubaccount passes balance.amount == 0 check ✓
// 8. socializeSubaccount called with insurance = 0
//    → balance.vQuoteBalance = -100e18 < 0
//    → insuranceCover = min(0, 100e18) = 0
//    → balance.vQuoteBalance still -100e18
//    → -(-100e18).div(0) → MathSD21x18.div reverts "DBZ" ✗
// 9. Assert: tx reverts with "DBZ"
// 10. Assert: alice's subaccount is permanently unfinalizable
```

### Citations

**File:** core/contracts/libraries/MathSD21x18.sol (L62-65)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
```

**File:** core/contracts/PerpEngine.sol (L164-168)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
```

**File:** core/contracts/PerpEngineState.sol (L29-51)
```text
        // pre update
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

**File:** core/contracts/ClearinghouseLiq.sol (L313-320)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```
