### Title
Retroactive Interest Rate Application on Spot Product Config Update - (`core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct()` updates `configs[productId]` for an existing product without first settling accrued interest via `_updateState()`. The next `SpotTick` transaction then applies the new interest rate config retroactively to the entire elapsed period `dt` since the last tick, corrupting `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` for all depositors and borrowers of that product.

---

### Finding Description

`SpotEngine.addOrUpdateProduct()` is callable by the owner to update the interest rate configuration (`interestFloorX18`, `interestSmallCapX18`, `interestLargeCapX18`, `minDepositRateX18`) of an existing spot product. The function directly overwrites `configs[productId]` at line 83 without first calling `_updateState()` to settle the interest that has accrued since the last `SpotTick`. [1](#0-0) 

Interest accrual in `SpotEngineState._updateState()` reads `configs[productId]` at line 67 and applies the resulting borrow/deposit rate multipliers over the full `dt` window passed in from the sequencer's `SpotTick` transaction. [2](#0-1) 

The `SpotTick` handler in `EndpointTx.processTransactionImpl()` computes `dt = txn.time - t.spotTime` and calls `spotEngine.updateStates(dt)`. This `dt` spans the entire interval since the previous tick — which includes the time before the config was changed. [3](#0-2) 

`updateStates()` iterates all products and calls `_updateState(productId, state, dt)` using the **current** (already-updated) config for the **entire** `dt` period. [4](#0-3) 

Because `addOrUpdateProduct()` and `SpotTick` are independent transactions with no atomicity guarantee, any config update that occurs between two ticks causes the new rates to be applied retroactively to the full inter-tick interval.

---

### Impact Explanation

`cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` are the sole accounting primitives used to convert every user's `amountNormalized` to a real balance. [5](#0-4) 

A retroactive rate increase overcharges all borrowers and over-rewards all depositors for the period before the change. A retroactive rate decrease does the opposite. The magnitude scales with the size of the inter-tick window (up to 7 days per the `ERR_INVALID_TIME` guard) and the total deposits/borrows of the product. The corrupted multipliers persist permanently and affect every subsequent balance read, withdrawal, health check, and liquidation for the product.

---

### Likelihood Explanation

`addOrUpdateProduct()` is a routine operational call — interest rate parameters are expected to be tuned over the protocol's lifetime. There is no mechanism that forces the owner to atomically pair a config update with a `SpotTick`. The sequencer submits ticks independently on its own schedule. Therefore, **every** interest rate config update for an existing product will trigger this desynchronization unless the owner and sequencer coordinate to submit both in the exact same transaction batch, which the contract does not enforce or document.

---

### Recommendation

Before overwriting `configs[productId]` for an existing product, `addOrUpdateProduct()` should settle the accrued interest by calling `_updateState(productId, states[productId], dt)` where `dt` is derived from the current oracle time minus the last recorded `spotTime`. This mirrors the pattern used in `updateStates()` and ensures the old config is applied only to the period it was active.

Alternatively, restrict interest-rate config changes to take effect only from the next tick boundary, storing a pending config that `_updateState()` activates at the start of each tick.

---

### Proof of Concept

1. Product `P` has `interestFloorX18 = 1e16` (1% annualized floor). Last `SpotTick` was at `T0`.
2. At time `T1 = T0 + 3600` (1 hour later), owner calls `addOrUpdateProduct(P, ..., newConfig)` where `newConfig.interestFloorX18 = 1e17` (10x higher).
3. At time `T2 = T1 + 3600` (another hour later), sequencer submits `SpotTick` with `txn.time = T2`.
4. `dt = T2 - T0 = 7200` seconds. `_updateState(P, state, 7200)` reads the **new** `interestFloorX18 = 1e17` and applies it to the full 7200-second window.
5. The correct behavior would be: apply 1% rate for the first 3600 seconds, then 10% rate for the next 3600 seconds.
6. The actual behavior: apply 10% rate for the full 7200 seconds — retroactively overcharging all borrowers of product `P` for the first hour.

### Citations

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

**File:** core/contracts/SpotEngineState.sol (L52-100)
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

**File:** core/contracts/EndpointTx.sol (L466-475)
```text
        } else if (txType == IEndpoint.TransactionType.SpotTick) {
            IEndpoint.SpotTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.SpotTick)
            );
            Times memory t = times;
            uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
            spotEngine.updateStates(dt);
            t.spotTime = txn.time;
            times = t;
```
