### Title
Pending Spot Interest Not Settled Before Config Update in `addOrUpdateProduct` — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct()` overwrites `configs[productId]` — which contains all interest rate parameters — for existing products without first calling `updateStates()` to checkpoint accrued interest at the old rates. The next `updateStates()` invocation will retroactively apply the new rates to the entire elapsed period since the last update, causing depositors or borrowers to receive incorrect interest.

---

### Finding Description

In `SpotEngineState._updateState()`, the borrow rate and deposit rate for a product are computed directly from `configs[productId]`: [1](#0-0) 

The five rate-determining fields — `interestFloorX18`, `interestInflectionUtilX18`, `interestSmallCapX18`, `interestLargeCapX18`, and `minDepositRateX18` — are read at the moment `updateStates(dt)` is called and applied to the full elapsed interval `dt`.

`SpotEngine.addOrUpdateProduct()` is callable by the owner for both new and existing products. For an existing product it directly overwrites the config: [2](#0-1) 

There is no call to `updateStates()` before `configs[productId] = config` executes. `updateStates` is gated `onlyEndpoint` and is driven by the sequencer on a periodic cadence: [3](#0-2) 

Because `updateStates` is the only function that advances `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18`, any elapsed time since the last `updateStates` call is an unresolved interest window. When the owner updates the config at time T1 (between two `updateStates` calls at T0 and T2), the next `updateStates(T2 − T0)` call computes interest for the full period `(T2 − T0)` using the **new** config, instead of using the old config for `(T1 − T0)` and the new config for `(T2 − T1)`.

---

### Impact Explanation

**Impact: Medium.** If interest rates are raised (e.g., `interestFloorX18` or `interestLargeCapX18` increased), borrowers are overcharged for the pre-update window `(T1 − T0)`, and depositors receive a windfall at borrowers' expense. If rates are lowered, depositors are underpaid for that same window. The magnitude scales with the size of the rate change and the length of the unresolved window (the sequencer's `updateStates` interval). The corrupted state is `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` for the affected product, which permanently misprices all normalized balances until the next socialization or manual correction. [4](#0-3) 

---

### Likelihood Explanation

**Likelihood: Medium.** The bug fires on every legitimate owner call to update an existing product's interest config — a routine governance action. The impact window is bounded by the sequencer's `updateStates` cadence (typically minutes to hours), but the accounting error is permanent once `updateStates` runs with the new config.

---

### Recommendation

Before overwriting `configs[productId]`, force-settle pending interest for the affected product at the current rates. Since `updateStates` is `onlyEndpoint`, the fix should either:

1. Extract the per-product interest settlement logic from `_updateState` into an internal helper callable from `addOrUpdateProduct`, and invoke it before `configs[productId] = config`; or
2. Require that the sequencer submit an `updateStates` transaction atomically before any config-update transaction, enforced at the endpoint level.

---

### Proof of Concept

1. At **T0**: sequencer calls `updateStates(dt)` — interest settled at old rates `R_old`. `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` are checkpointed.
2. At **T1** (T1 > T0): owner calls `addOrUpdateProduct(productId, ..., newConfig, ...)` where `newConfig.interestLargeCapX18` is significantly higher than before. `configs[productId]` is immediately overwritten. No interest is settled for the window `(T1 − T0)`.
3. At **T2** (T2 > T1): sequencer calls `updateStates(T2 − T0)`. `_updateState` reads `configs[productId]` (now the new config) and computes `borrowRateMultiplierX18` for the full interval `(T2 − T0)` using the elevated `interestLargeCapX18`. The window `(T1 − T0)` is charged at the new higher rate instead of the old lower rate. Borrowers' `cumulativeBorrowsMultiplierX18` is inflated beyond what they accrued, permanently overstating their debt. [5](#0-4)

### Citations

**File:** core/contracts/SpotEngineState.sol (L56-100)
```text
    ) internal {
        int128 borrowRateMultiplierX18;
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
        int128 minDepositRateX18;
        {
            Config memory config = configs[productId];

            // annualized borrower rate
            int128 borrowerRateX18 = config.interestFloorX18;
            if (utilizationRatioX18 == 0) {
                // setting borrowerRateX18 to 0 here has the property that
                // adding a product at the beginning of time and not using it until time T
                // results in the same state as adding the product at time T
                borrowerRateX18 = 0;
            } else if (utilizationRatioX18 < config.interestInflectionUtilX18) {
                borrowerRateX18 += config
                    .interestSmallCapX18
                    .mul(utilizationRatioX18)
                    .div(config.interestInflectionUtilX18);
            } else {
                borrowerRateX18 +=
                    config.interestSmallCapX18 +
                    config.interestLargeCapX18.mul(
                        (
                            (utilizationRatioX18 -
                                config.interestInflectionUtilX18).div(
                                    ONE - config.interestInflectionUtilX18
                                )
                        )
                    );
            }

            // convert to per second
            borrowerRateX18 = borrowerRateX18.div(
                MathSD21x18.fromInt(31536000)
            );
            borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
            minDepositRateX18 = config.minDepositRateX18;
        }
```

**File:** core/contracts/SpotEngineState.sol (L129-137)
```text
        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(borrowRateMultiplierX18);

        int128 depositRateMultiplierX18 = ONE + realizedDepositRateX18;

        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(depositRateMultiplierX18);
```

**File:** core/contracts/SpotEngineState.sol (L265-283)
```text
    function updateStates(uint128 dt) external onlyEndpoint {
        State memory quoteState;
        require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            if (productId == NLP_PRODUCT_ID) {
                continue;
            }
            State memory state = states[productId];
            if (productId == QUOTE_PRODUCT_ID) {
                quoteState = state;
            }
            if (state.totalDepositsNormalized == 0) {
                continue;
            }
            _updateState(productId, state, dt);
            _setState(productId, state);
        }
    }
```

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```
