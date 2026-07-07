Now I have all the code I need. Let me trace the exact state mutation.

### Title
Stale `lastLiquidationFees` Causes Insurance Understatement During Socialization of Unrelated Subaccounts — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a single global `int128` in `ClearinghouseStorage`. It is written only inside `_handleLiquidationPayment` and is never cleared by `_finalizeSubaccount`. When a subaccount is finalized via `productId == type(uint32).max` without a preceding normal liquidation of that same subaccount in the current sequence, `_finalizeSubaccount` subtracts a stale `lastLiquidationFees` (belonging to a prior, unrelated liquidation) from the insurance amount passed to `perpEngine.socializeSubaccount`. This causes the protocol to socialize more loss onto other participants than is necessary, violating the invariant that all available insurance must be exhausted before any socialization occurs.

---

### Finding Description

`lastLiquidationFees` is declared as a single global slot: [1](#0-0) 

It is written unconditionally at the end of every normal liquidation: [2](#0-1) 

Inside `_finalizeSubaccount`, the effective insurance passed to `perpEngine.socializeSubaccount` is computed as:

```
v.insurance = insurance;
v.insurance -= lastLiquidationFees;   // line 369
```

and then restored after socialization:

```
v.insurance += lastLiquidationFees;   // line 410
insurance = v.insurance;
``` [3](#0-2) 

`_finalizeSubaccount` never writes to `lastLiquidationFees`. After it returns, the variable retains whatever value was set by the most recent call to `_handleLiquidationPayment`, regardless of which subaccount that liquidation was for. [4](#0-3) 

The design comment at line 581–585 makes the intent clear: `lastLiquidationFees` is supposed to represent the fees from the last liquidation **of the subaccount currently being finalized**, so that those freshly-added fees do not falsely make insurance appear sufficient and block socialization. The implementation, however, stores a single global value that is shared across all subaccounts.

---

### Impact Explanation

**Concrete scenario:**

1. Subaccount A is liquidated normally. `_handleLiquidationPayment` sets `lastLiquidationFees = F1` and `insurance += F1`.
2. Subaccount A is finalized (`productId == type(uint32).max`). `_finalizeSubaccount` correctly uses `insurance - F1` for socialization, then restores `F1`. `lastLiquidationFees` is still `F1`.
3. Subaccount B is now under maintenance with all perp positions zero and all spot assets ≤ 0 (e.g., only a negative USDC balance). No normal liquidation of B has occurred since step 1.
4. Subaccount B is finalized. `_finalizeSubaccount` computes `v.insurance = insurance - F1`. It passes this understated amount to `perpEngine.socializeSubaccount(B, insurance - F1)`.

Inside `perpEngine.socializeSubaccount`, for each product where B has negative `vQuoteBalance`, the available `insurance` cover is `F1` less than the true available amount: [5](#0-4) 

The deficit `F1` is not covered by insurance and is instead socialized — spread as a funding-rate adjustment across all open-interest holders. After `socializeSubaccount` returns, `F1` is added back to `v.insurance` and written to `insurance`, so the insurance fund balance is arithmetically restored. But the socialization is irreversible: the `cumulativeFundingLongX18` / `cumulativeFundingShortX18` adjustments have already been applied to all participants.

The invariant broken: **insurance must be fully applied before any socialization occurs**. Here, up to `lastLiquidationFees` worth of insurance is withheld from covering B's losses, and that amount is instead borne by all other open-interest participants.

---

### Likelihood Explanation

The condition is reachable in normal protocol operation:

- Any subaccount that has had all its perp positions closed (e.g., via prior normal liquidations) but still carries a negative USDC quote balance can be finalized directly with `productId == type(uint32).max`.
- The sequencer processes liquidation transactions in order; a normal liquidation of any other subaccount between the last normal liquidation of B and B's finalization is sufficient to leave `lastLiquidationFees` stale.
- No special privileges, governance access, or oracle manipulation are required. The attacker only needs to submit a valid `LiquidateSubaccount` transaction with `productId == type(uint32).max` for an eligible subaccount.

---

### Recommendation

Reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount` (or immediately after it is consumed), so that a finalization call never inherits fees from an unrelated liquidation:

```solidity
// At the top of _finalizeSubaccount, after the productId guard:
int128 _lastFees = lastLiquidationFees;
lastLiquidationFees = 0;   // consume and clear

// Then use _lastFees in place of lastLiquidationFees throughout the function,
// and do NOT add it back to insurance at line 410 unless it was legitimately
// the fee from the most recent liquidation of txn.liquidatee.
```

Alternatively, track `lastLiquidationFees` per subaccount (`mapping(bytes32 => int128)`) so that each finalization only subtracts the fees that were added for that specific subaccount.

---

### Proof of Concept

```
State before:
  insurance = 1000
  lastLiquidationFees = 50   // set by a prior normal liquidation of subaccount A

Step 1: liquidateSubaccountImpl called for subaccount B, productId = type(uint32).max
  _finalizeSubaccount enters
  v.insurance = 1000
  v.insurance -= 50  => v.insurance = 950   // stale F1 withheld

Step 2: perpEngine.socializeSubaccount(B, 950) called
  B has vQuoteBalance = -100 on product P
  insuranceCover = min(950, 100) = 100
  insurance -= 100 => 850
  balance.vQuoteBalance = 0
  // No socialization needed IF full 1000 were passed.
  // With only 950, same result here — but if B's deficit > 950:

Alternative: B has vQuoteBalance = -980
  insuranceCover = min(950, 980) = 950
  insurance -= 950 => 0
  balance.vQuoteBalance = -30
  // Socialize 30 across all open-interest holders  <-- WRONG
  // With full 1000: insuranceCover = 980, no socialization needed

Step 3: v.insurance += 50 => 50; insurance = 50
  // Insurance fund is arithmetically restored, but 30 was already socialized
```

The 30-unit socialization loss is permanent and was caused entirely by the stale `lastLiquidationFees = 50` withholding insurance that was genuinely available. [3](#0-2) [2](#0-1) [6](#0-5)

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L23-26)
```text
    int128 internal insurance;

    int128 internal lastLiquidationFees;

```

**File:** core/contracts/ClearinghouseLiq.sol (L368-412)
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
        return true;
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

**File:** core/contracts/PerpEngine.sol (L154-172)
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
```
