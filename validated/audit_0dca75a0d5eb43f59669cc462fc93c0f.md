### Title
Missing `_updateState` Call Before Socialization Corrupts Interest Accounting for Depositors — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.socializeSubaccount` directly modifies `state.cumulativeDepositsMultiplierX18` and `state.totalBorrowsNormalized` to absorb bad debt without first calling `_updateState` to accrue pending interest. This is the exact analog of M-04: a function mutates a primary state variable that a derived rate variable depends on, but omits the required rate-update step, permanently corrupting interest accounting for all depositors in the affected product.

---

### Finding Description

In `SpotEngineState._updateState`, interest is accrued by advancing `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` based on the current utilization ratio and elapsed time `dt`. This function is the sole mechanism for applying interest and is only reachable via `updateStates`, which is called by the Endpoint periodically.

`SpotEngine.socializeSubaccount` (lines 243–277) is invoked during liquidation finalization to absorb a negative spot balance as bad debt. It reads `state` directly from storage and then:

1. Computes `totalDeposited` using the stale (not yet interest-accrued) `cumulativeDepositsMultiplierX18`
2. Overwrites `state.cumulativeDepositsMultiplierX18` with a reduced value to absorb the loss
3. Adjusts `state.totalBorrowsNormalized` to cancel the bad debt
4. Writes the modified state back via `_setState`

```solidity
// SpotEngine.sol lines 256–267
int128 totalDeposited = state.totalDepositsNormalized.mul(
    state.cumulativeDepositsMultiplierX18          // ← stale, no _updateState called
);
state.cumulativeDepositsMultiplierX18 = (totalDeposited +
    balance.amount).div(state.totalDepositsNormalized);

state.totalBorrowsNormalized += balance.amount.div(
    state.cumulativeBorrowsMultiplierX18           // ← stale
);
```

Because `_updateState` is never called before these writes, the interest that accrued between the last `updateStates` call and the socialization event is never applied. When `_updateState` is next called (at time T2), it computes interest on the post-socialization state starting from T0, permanently skipping the interest window [T0, T1].

This is structurally identical to M-04: `pauseReward` calls `updatePool` (changes `totalStaked`) but omits `updatePoolRate` (updates `EarnRateSec`), causing the derived rate to be stale. Here, `socializeSubaccount` changes `cumulativeDepositsMultiplierX18` and `totalBorrowsNormalized` but omits `_updateState`, causing the derived multiplier to be stale at the moment of mutation.

---

### Impact Explanation

All depositors in the socialized product permanently lose the interest that should have been accrued between the last `updateStates` call and the socialization event. The loss magnitude is:

```
lost_interest ≈ totalDeposits × utilizationRatio × borrowRate × (T1 − T0)
```

This is a real, non-recoverable asset delta for depositors. The loss is bounded by the `updateStates` call frequency and utilization, making it medium in magnitude but concrete and permanent.

**Impact: Medium** — bounded but permanent interest loss for all depositors in the affected product.

---

### Likelihood Explanation

Socialization is triggered whenever a subaccount is finalized during liquidation with insufficient insurance to cover its negative quote balance. This is a realistic scenario in a leveraged DEX during volatile market conditions. Any unprivileged liquidator can trigger this path.

**Likelihood: Medium** — requires an insolvency event, which is realistic under normal market stress.

---

### Recommendation

Before modifying `state.cumulativeDepositsMultiplierX18` or `state.totalBorrowsNormalized` in `socializeSubaccount`, the contract should ensure all pending interest is accrued. The cleanest fix is to store the last `updateStates` timestamp on-chain in `SpotEngineState` and compute `dt` inside `socializeSubaccount` to call `_updateState` before the socialization writes. Alternatively, the Endpoint sequencer must guarantee that `updateStates` is always submitted immediately before any `liquidateSubaccount` transaction that could trigger finalization.

---

### Proof of Concept

1. **T0**: Endpoint calls `updateStates` → `_updateState` advances `cumulativeDepositsMultiplierX18` to reflect interest up to T0.
2. **T1 > T0**: A subaccount's quote balance goes negative due to market movement. No `updateStates` has been called since T0.
3. **Liquidator** submits `LiquidateSubaccount` via `Endpoint.submitTransactions`.
4. `Clearinghouse.liquidateSubaccount` delegatecalls `ClearinghouseLiq.liquidateSubaccountImpl`.
5. `_finalizeSubaccount` calls `spotEngine.socializeSubaccount(txn.liquidatee)`.
6. `socializeSubaccount` reads `state` with `cumulativeDepositsMultiplierX18` frozen at T0.
7. It computes `totalDeposited` using the T0 multiplier (understating actual deposits by the interest accrued in [T0, T1]).
8. It writes a reduced `cumulativeDepositsMultiplierX18` back to storage, permanently encoding the stale basis.
9. **T2**: `updateStates` is called. `_updateState` applies interest for the full window [T0, T2] on the post-socialization multiplier. The interest for [T0, T1] on the pre-socialization deposit base is permanently lost.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/SpotEngine.sol (L243-277)
```text
    function socializeSubaccount(bytes32 subaccount) external {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];

            State memory state = states[productId];
            Balance memory balance = balanceNormalizedToBalance(
                state,
                balances[productId][subaccount]
            );
            if (balance.amount < 0) {
                int128 totalDeposited = state.totalDepositsNormalized.mul(
                    state.cumulativeDepositsMultiplierX18
                );

                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);

                require(state.cumulativeDepositsMultiplierX18 > 0);

                state.totalBorrowsNormalized += balance.amount.div(
                    state.cumulativeBorrowsMultiplierX18
                );

                _setBalanceAndUpdateBitmap(
                    productId,
                    subaccount,
                    BalanceNormalized({amountNormalized: 0})
                );
                _setState(productId, state);
            }
        }
    }
```

**File:** core/contracts/SpotEngineState.sol (L52-64)
```text
    function _updateState(
        uint32 productId,
        State memory state,
        uint128 dt
    ) internal {
        int128 borrowRateMultiplierX18;
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-412)
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
        return true;
```
