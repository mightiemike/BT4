### Title
Division by Zero in `socializeSubaccount` Permanently Blocks Liquidation Finalization When `openInterest` Is Zero - (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when a subaccount's `vQuoteBalance` is negative and insurance is insufficient. If `openInterest` is zero at the time of socialization, the call reverts with `"DBZ"`, permanently blocking the finalization of an insolvent subaccount and freezing the liquidation path.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, when `balance.vQuoteBalance < 0` and insurance cannot fully cover the deficit, the code attempts to spread the loss across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // <-- reverts if openInterest == 0
    );
``` [1](#0-0) 

`MathSD21x18.div` enforces `require(y != 0, ERR_DIV_BY_ZERO)`: [2](#0-1) 

`state.openInterest` can legitimately be zero. It is the sum of absolute position sizes across all participants, decremented in `_updateBalance` when positions are closed: [3](#0-2) 

A subaccount's `vQuoteBalance` accumulates funding payments independently of its position size. A subaccount can close its position (driving `openInterest` to zero for the whole market if it was the last participant) while retaining a negative `vQuoteBalance` from prior funding debits. When that subaccount later becomes insolvent and a liquidator calls `liquidateSubaccountImpl` → `_finalizeSubaccount` → `perpEngine.socializeSubaccount`, the division reverts.

Notably, `PerpEngineState.updateStates` already guards against this exact scenario for the funding-rate path:

```solidity
if (state.openInterest == 0) {
    continue;
}
``` [4](#0-3) 

But `socializeSubaccount` has no equivalent guard.

---

### Impact Explanation

`_finalizeSubaccount` is the only code path that calls `perpEngine.socializeSubaccount`: [5](#0-4) 

When the call reverts, the entire `liquidateSubaccountImpl` transaction reverts. The insolvent subaccount cannot be finalized. Its negative `vQuoteBalance` remains unresolved in the protocol's accounting, and the insurance fund cannot reclaim its contribution. If multiple such subaccounts accumulate, the protocol's solvency accounting is permanently corrupted and the liquidation queue is effectively DoS'd for those accounts — a direct analog to the priority-queue DoS described in H-06.

---

### Likelihood Explanation

The trigger requires:
1. A perp market where all positions have been closed (`openInterest == 0`).
2. At least one subaccount in that market with a residual negative `vQuoteBalance` (from accumulated funding payments after closing their position).
3. That subaccount's overall health falling below maintenance margin (e.g., due to losses in other products).

All three conditions are reachable through normal user activity with no privileged access required. A liquidator calling `liquidateSubaccountImpl` with `productId == type(uint32).max` (the finalization path) is the unprivileged entry point.

---

### Recommendation

Mirror the guard already present in `updateStates`. In `socializeSubaccount`, skip the division when `state.openInterest == 0`:

```solidity
if (balance.vQuoteBalance < 0) {
    int128 insuranceCover = MathHelper.min(insurance, -balance.vQuoteBalance);
    insurance -= insuranceCover;
    balance.vQuoteBalance += insuranceCover;
    state.availableSettle += insuranceCover;

    if (balance.vQuoteBalance < 0) {
+       if (state.openInterest == 0) {
+           // No open interest to socialize against; absorb loss into availableSettle
+           state.availableSettle += balance.vQuoteBalance; // vQuoteBalance is negative
+           balance.vQuoteBalance = 0;
+       } else {
            int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
            state.cumulativeFundingLongX18 += fundingPerShare;
            state.cumulativeFundingShortX18 -= fundingPerShare;
            balance.vQuoteBalance = 0;
+       }
    }
```

The exact handling of the zero-`openInterest` branch (absorb into insurance, write off, etc.) should be decided by the protocol team, but the division must not be reached when `openInterest == 0`.

---

### Proof of Concept

1. Deploy the protocol with one perp market (productId = 2).
2. Subaccount A opens a long position; subaccount B opens a short position of equal size. `openInterest = 2 * size`.
3. Funding payments accumulate, leaving subaccount A with a negative `vQuoteBalance`.
4. Both A and B close their positions. `openInterest` drops to 0. Subaccount A still has `vQuoteBalance < 0`.
5. Subaccount A's quote balance goes negative (e.g., from a separate spot liability), pushing it below maintenance health.
6. Liquidator calls `Endpoint.submitTransactionsChecked` with a `LiquidateSubaccount` transaction where `productId == type(uint32).max` (finalization).
7. Execution reaches `_finalizeSubaccount` → `perpEngine.socializeSubaccount(A, insurance)`.
8. Inside the loop, `balance.vQuoteBalance < 0` is true; insurance is insufficient; execution reaches `MathSD21x18.div(state.openInterest)` where `openInterest == 0`.
9. Transaction reverts with `"DBZ"`. Subaccount A can never be finalized. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/PerpEngine.sol (L141-178)
```text
    function socializeSubaccount(bytes32 subaccount, int128 insurance)
        external
        returns (int128)
    {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];
            (State memory state, Balance memory balance) = getStateAndBalance(
                productId,
                subaccount
            );
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

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-627)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```
