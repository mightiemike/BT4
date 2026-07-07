Based on my analysis of the exact code, this is a real vulnerability. Here is the full finding:

---

### Title
Stale `lastLiquidationFees` Causes Under-Coverage of Insurance in `_finalizeSubaccount` — (`core/contracts/ClearinghouseLiq.sol`)

### Summary

`lastLiquidationFees` is a global storage variable that is written by `_handleLiquidationPayment` and read (but never reset) by `_finalizeSubaccount`. When a subaccount is finalized whose liquidation sequence did not include a prior `_handleLiquidationPayment` call in the same sequence, `_finalizeSubaccount` uses a stale fee value from a prior, unrelated liquidation. This causes the insurance fund to under-cover the liquidatee's negative quote balance, leaving bad debt that should have been absorbed by insurance to instead be socialized across all users.

### Finding Description

`lastLiquidationFees` is declared as a plain `int128` storage slot in `ClearinghouseStorage`: [1](#0-0) 

It is written unconditionally at the end of every `_handleLiquidationPayment` call, regardless of which subaccount is being liquidated: [2](#0-1) 

In `_finalizeSubaccount`, the variable is read and deducted from `v.insurance` before socialization, then added back after: [3](#0-2) 

Critically, `_finalizeSubaccount` **never writes** `lastLiquidationFees`. After finalizing subaccount A, the storage slot retains A's fee value. When subaccount B is subsequently finalized via `liquidateSubaccountImpl(productId=uint32.max)` — without any intervening `_handleLiquidationPayment` call for B — the stale value `X` (from A) is used:

```
v.insurance = insurance          // e.g., I
v.insurance -= lastLiquidationFees  // v.insurance = I - X  (X is stale, from A)
...
insuranceCover = min(I - X, -quoteBalance.amount)  // under-covers by up to X
...
v.insurance += lastLiquidationFees  // v.insurance = (I - X - insuranceCover) + X
insurance = v.insurance              // insurance retains X more than it should
```

The design intent (per the inline comment) is that `lastLiquidationFees` holds back the fees from the **last step of the current liquidatee** so that those freshly-added fees do not block socialization. That invariant is broken when the finalization is the first action on a new liquidatee. [4](#0-3) 

### Impact Explanation

Let `I` = insurance after A's finalization, `D` = magnitude of B's negative quote balance, `X` = stale `lastLiquidationFees` from A.

Suppose `I >= D` (insurance is sufficient to fully cover B) but `I - X < D`:

- **Correct behavior**: `insuranceCover = D`, `insurance = I - D`, no socialization.
- **Buggy behavior**: `insuranceCover = I - X`, socialization triggered for `D - (I - X)`, `insurance = X`.

The protocol socializes `D - I + X` of bad debt across all users even though the insurance fund had enough to cover it. The insurance fund incorrectly retains `X` instead of `I - D`. This is a direct, quantifiable asset accounting error: real user losses are socialized that should have been absorbed by the insurance fund.

### Likelihood Explanation

The precondition is straightforward and reachable through normal sequencer-submitted liquidation transactions:

1. Any liquidation of any subaccount A sets `lastLiquidationFees = X > 0`.
2. A second subaccount B exists with zero non-quote positions and a negative quote balance (e.g., a subaccount that accumulated negative PnL and had all positions closed by prior liquidation steps, but whose finalization `productId=uint32.max` call is the first transaction touching B in this sequence).
3. The sequencer submits `liquidateSubaccountImpl` for B with `productId=uint32.max`.

No special privileges, admin access, or reentrancy is required. The sequencer processes liquidations in batches, making this ordering trivially achievable.

### Recommendation

Reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount`, or track it per-liquidatee (keyed by `txn.liquidatee`). The simplest fix:

```solidity
// At the top of _finalizeSubaccount, before line 368:
int128 feesToRestore = lastLiquidationFees;
// use feesToRestore in place of lastLiquidationFees throughout,
// and set lastLiquidationFees = 0 after restoring insurance.
```

Alternatively, store `lastLiquidationFees` as a `mapping(bytes32 => int128)` keyed by subaccount, and clear the entry after finalization.

### Proof of Concept

```solidity
// 1. Liquidate subaccountA (any product), generating lastLiquidationFees = X
liquidateSubaccountImpl({liquidatee: subaccountA, productId: someProduct, amount: N, ...});
// insurance += X, lastLiquidationFees = X

// 2. Finalize subaccountA
liquidateSubaccountImpl({liquidatee: subaccountA, productId: type(uint32).max, ...});
// lastLiquidationFees is still X after this call (never reset)

// 3. subaccountB has: all positions zero, quoteBalance = -D, where I >= D but I - X < D
// 4. Finalize subaccountB — first action on B
liquidateSubaccountImpl({liquidatee: subaccountB, productId: type(uint32).max, ...});
// v.insurance = I - X  (stale deduction)
// insuranceCover = I - X  (under-covers by X)
// spotEngine.socializeSubaccount(subaccountB) called  (bad debt socialized)
// insurance = X  (should be I - D)

// Assert: insurance == I - D  // FAILS: insurance == X
// Assert: no socialization occurred  // FAILS: socialization was triggered
``` [5](#0-4)

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
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
