### Title
Negative Insurance Passed to `perpEngine.socializeSubaccount` Inflates Insurance Fund and Over-Socializes Losses — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

When `lastLiquidationFees > insurance` at the time `_finalizeSubaccount` is called, `v.insurance` becomes negative before being passed to `perpEngine.socializeSubaccount`. Inside that function, a negative `insurance` argument causes `insuranceCover` to be negative, which worsens the liquidatee's `vQuoteBalance` and forces other participants to absorb extra losses. After returning, the `v.insurance += lastLiquidationFees` add-back inflates the stored `insurance` to `lastLiquidationFees`, a value larger than the actual insurance that existed.

---

### Finding Description

`ClearinghouseStorage` holds two persistent state variables: [1](#0-0) 

In `_handleLiquidationPayment`, both are written atomically: [2](#0-1) 

This guarantees `insurance >= lastLiquidationFees` immediately after a liquidation step. However, `withdrawInsurance` can reduce `insurance` between transactions with no lower bound relative to `lastLiquidationFees` — only relative to zero: [3](#0-2) 

So after a withdrawal, `insurance` can be any value in `[0, lastLiquidationFees)`.

When `_finalizeSubaccount` is subsequently called, lines 368–369 compute: [4](#0-3) 

If `insurance = W` and `lastLiquidationFees = F` with `W < F`, then `v.insurance = W - F < 0`. This negative value is passed directly to `perpEngine.socializeSubaccount`: [5](#0-4) 

Inside `PerpEngine.socializeSubaccount`, for any product where `balance.vQuoteBalance < 0`: [6](#0-5) 

With `insurance = -D` (negative) and `-balance.vQuoteBalance = L` (positive):
- `insuranceCover = min(-D, L) = -D` (negative)
- `insurance -= (-D)` → `insurance = 0`
- `balance.vQuoteBalance += (-D)` → `balance.vQuoteBalance = -(L + D)` (more negative by `D`)

The worsened `vQuoteBalance` then triggers socialization of `L + D` instead of `L`: [7](#0-6) 

`perpEngine.socializeSubaccount` returns `0`. Back in `_finalizeSubaccount`, the add-back at line 410 then inflates the stored insurance: [8](#0-7) 

`v.insurance = 0 + F = F`, but the actual insurance was `W < F`. The protocol now records `F` units of insurance when only `W` existed.

---

### Impact Explanation

Two concrete accounting corruptions occur simultaneously:

1. **Insurance fund inflation**: `insurance` is set to `lastLiquidationFees` (`F`) instead of the actual remaining value (`W`). The phantom `F - W` units are permanently recorded as available insurance, breaking the solvency invariant that `insurance` must reflect real backing.

2. **Unfair over-socialization**: Other market participants absorb `D = F - W` extra units of loss through inflated `cumulativeFundingLongX18` / `cumulativeFundingShortX18`, reducing their effective balances without any corresponding real deficit. [9](#0-8) 

---

### Likelihood Explanation

The precondition `lastLiquidationFees > insurance` is reachable through normal protocol operation:

1. Insurance starts at `I` (possibly near zero after prior socializations).
2. A liquidation step runs: `insurance = I + F`, `lastLiquidationFees = F`.
3. A `withdrawInsurance` transaction (routine admin operation) reduces `insurance` to `W` where `0 ≤ W < F`.
4. Any liquidator submits a finalization transaction (`txn.productId = type(uint32).max`) for any subaccount that passes the finalizability checks.

No special privileges are needed for step 4; any external liquidator can trigger it. Step 3 is a normal protocol operation, not an attack. The condition can also arise if insurance was already low before the liquidation (e.g., `I = 0`, `F = 5`, then any withdrawal of even 1 unit creates the condition).

---

### Recommendation

Clamp `v.insurance` to zero before passing it to `perpEngine.socializeSubaccount`:

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;
// Prevent negative insurance from corrupting socialization
if (v.insurance < 0) {
    v.insurance = 0;
}
v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
// ...
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
```

This ensures `perpEngine.socializeSubaccount` never receives a negative value, preserving the invariant that `insuranceCover >= 0` and that socialized losses are not artificially inflated. The add-back `v.insurance += lastLiquidationFees` at line 410 should also be bounded so that `insurance` cannot exceed its pre-call value.

---

### Proof of Concept

**Initial state:**
- `insurance = 3`
- `lastLiquidationFees = 5` (set by a prior liquidation step of any subaccount)
- Subaccount B has one perp product with `balance.vQuoteBalance = -10`, `balance.amount = 0`
- `quoteBalance.amount = 0` for subaccount B

**Call:** `liquidateSubaccountImpl({ liquidatee: B, productId: type(uint32).max, ... })`

**Trace through `_finalizeSubaccount`:**

```
v.insurance = 3 - 5 = -2

perpEngine.socializeSubaccount(B, -2):
  insuranceCover = min(-2, 10) = -2
  insurance = -2 - (-2) = 0
  balance.vQuoteBalance = -10 + (-2) = -12
  // socialize -12 instead of -10: extra 2 units charged to other participants
  balance.vQuoteBalance = 0
  return 0

v.insurance = 0
insuranceCover = min(0, 0) = 0  // no positive cover
v.insurance <= 0 → spotEngine.socializeSubaccount(B) called
v.insurance += 5  →  v.insurance = 5
insurance = 5     // was 3 before; inflated by 2
```

**Assertions that fail:**
- `insurance_after == insurance_before - actual_costs` → `5 ≠ 3 - 0` (insurance increased)
- `perpEngine.socializeSubaccount` was called with `-2`, not `>= 0`
- Other participants absorbed 12 units of loss instead of 10 [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L23-25)
```text
    int128 internal insurance;

    int128 internal lastLiquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-411)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;

        if (v.canLiquidateMore) {
            for (uint32 i = 1; i < v.spotIds.length; ++i) {
                uint32 spotId = v.spotIds[i];
                ISpotEngine.Balance memory balance = spotEngine.getBalance(
                    spotId,
                    txn.liquidatee
                );
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
        }

        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-586)
```text
        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/Clearinghouse.sol (L282-284)
```text
        int128 amount = int128(txn.amount) * int128(multiplier);
        require(amount <= insurance, ERR_NO_INSURANCE);
        insurance -= amount;
```

**File:** core/contracts/PerpEngine.sol (L154-177)
```text
            if (balance.vQuoteBalance < 0) {
                int128 insuranceCover = MathHelper.min(
                    insurance,
                    -balance.vQuoteBalance
                );
                insurance -= insuranceCover;
                balance.vQuoteBalance += insuranceCover;
                state.availableSettle += insuranceCover;

                // actually socialize if still not enough
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
                }
                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
        }
        return insurance;
```
