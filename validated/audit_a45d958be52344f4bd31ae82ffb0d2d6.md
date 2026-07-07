### Title
Global `lastLiquidationFees` Bleeds Across Subaccounts, Corrupting `v.canLiquidateMore` and Enabling Premature Socialization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a single global storage slot in `ClearinghouseStorage`. It is written by every non-finalization liquidation step and read by every finalization step. When a liquidator sequences a non-finalization liquidation of subaccount A immediately before a finalization of a different subaccount B, the finalization incorrectly subtracts A's fees from the available insurance, causing `v.canLiquidateMore` to be computed as `false` even when real insurance is sufficient. This skips the strict spot-balance-zero guard and triggers socialization with zero effective insurance, spreading bad debt to all open-interest holders.

---

### Finding Description

`ClearinghouseStorage` declares `lastLiquidationFees` as a single protocol-wide slot: [1](#0-0) 

Every call to `_handleLiquidationPayment` overwrites it unconditionally, regardless of which subaccount is being liquidated: [2](#0-1) 

The design intent (per the inline comment) is that when a subaccount is finalized, the fees just added to `insurance` for **that same subaccount** should be excluded from the available insurance pool, so that those fees do not block socialization. The assumption is that the finalization step always immediately follows the last non-finalization step for the **same** subaccount.

That assumption is never enforced. `_finalizeSubaccount` blindly subtracts whatever `lastLiquidationFees` currently holds: [3](#0-2) 

If the last non-finalization liquidation was for a **different** subaccount A, `lastLiquidationFees` carries A's fees into B's finalization. The result is that `v.insurance` is understated by exactly A's fee amount.

The conditional spot-balance-zero check is then gated on this corrupted value: [4](#0-3) 

When `v.canLiquidateMore` is incorrectly `false`, the `require(balance.amount == 0)` guard is skipped entirely, allowing subaccount B to be finalized while it still holds non-zero spot liabilities.

Downstream, `perpEngine.socializeSubaccount` and the insurance-cover logic both operate on the understated `v.insurance`: [5](#0-4) 

Because `v.insurance = 0` (in the minimal case), `insuranceCover = 0`, the quote deficit is not covered, and `spotEngine.socializeSubaccount` is called, spreading bad debt to all spot holders. The `v.insurance += lastLiquidationFees` at line 410 restores the `insurance` storage slot to its correct value, so the insurance fund itself is not drained — but the socialization has already occurred incorrectly.

---

### Impact Explanation

- **Spot-liability check bypassed:** Subaccount B can be finalized while holding non-zero spot liabilities (negative spot balances), violating the invariant that all liabilities must be cleared before finalization when insurance is sufficient.
- **Premature socialization:** `perpEngine.socializeSubaccount` and `spotEngine.socializeSubaccount` are called with zero effective insurance instead of the actual available amount, spreading bad debt to all open-interest holders that should have been absorbed by the insurance fund.
- **Broken solvency accounting:** The protocol incorrectly concludes it cannot cover B's deficit and forces loss mutualization on innocent counterparties.

---

### Likelihood Explanation

The exploit requires:
1. A non-finalization liquidation of any subaccount A (sets `lastLiquidationFees = X`, `insurance += X`).
2. A finalization liquidation of a different insolvent subaccount B submitted in the same or a subsequent transaction batch.
3. The pre-liquidation insurance being low enough that `insurance_before_A + X - X ≤ 0` (i.e., `insurance_before_A ≤ 0`).

The minimal triggering state is `insurance = 0` before step 1 — a realistic condition after prior insurance drawdowns. A liquidator can deliberately sequence these two signed `LiquidateSubaccount` transactions in a single `submitTransactions` batch. No privileged access is required; any liquidator can craft this sequence.

---

### Recommendation

Track `lastLiquidationFees` per-subaccount rather than globally, or reset it at the start of each finalization call. The simplest fix is to zero out `lastLiquidationFees` at the beginning of `_finalizeSubaccount` and only apply the exclusion when the finalization is for the same subaccount that generated the fees (i.e., when the finalization immediately follows a non-finalization step for the same liquidatee). Alternatively, pass the fees as a local parameter rather than persisting them in storage across subaccount boundaries.

---

### Proof of Concept

```
State setup:
  insurance = 0
  lastLiquidationFees = 0
  Subaccount A: under maintenance, has a perp position
  Subaccount B: insolvent, has negative spot balance (liability), all perps closed

Tx batch submitted by liquidator:
  [1] LiquidateSubaccount(liquidatee=A, productId=perpId, amount=X)
      → _handleLiquidationPayment runs
      → insurance += fees_A  (insurance = fees_A)
      → lastLiquidationFees = fees_A

  [2] LiquidateSubaccount(liquidatee=B, productId=type(uint32).max)
      → _finalizeSubaccount runs
      → v.insurance = insurance - lastLiquidationFees = fees_A - fees_A = 0
      → quoteBalance.amount < 0 (B is insolvent)
      → v.canLiquidateMore = (negative + 0) > 0 = false
      → spot-balance-zero check (lines 372-383) is SKIPPED
      → perpEngine.socializeSubaccount(B, 0) called with 0 insurance
      → insuranceCover = min(0, -quoteBalance) = 0
      → v.insurance = 0 ≤ 0 → spotEngine.socializeSubaccount(B) called
      → bad debt spread to all OI holders
      → v.insurance += fees_A; insurance = fees_A  (storage restored, damage done)

Assert:
  - B was finalized with a non-zero spot liability (should have been blocked)
  - Socialization occurred despite fees_A being available
  - All OI holders absorbed a loss that the insurance fund should have covered
```

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-370)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
```

**File:** core/contracts/ClearinghouseLiq.sol (L372-384)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-411)
```text
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
