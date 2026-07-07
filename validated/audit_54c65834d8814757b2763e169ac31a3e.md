### Title
Stale Interest Multiplier in `withdrawCollateral` Health Check Allows Undercollateralized Withdrawal — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`withdrawCollateral` checks subaccount health using a stale `cumulativeBorrowsMultiplierX18` because `spotEngine.updateStates()` is never called before the health check. A borrower whose position has become unhealthy due to accrued interest (since the last `SpotTick`) can still pass the health check and withdraw collateral, leaving the protocol with an undercollateralized position.

---

### Finding Description

Spot interest in Nado is tracked via a normalized balance model. Each borrower's actual debt is:

```
actualDebt = amountNormalized * cumulativeBorrowsMultiplierX18
```

The multiplier `cumulativeBorrowsMultiplierX18` is stored in `states[productId]` and is only advanced when `updateStates(dt)` is called. That call is triggered exclusively by `SpotTick` transactions submitted by the sequencer: [1](#0-0) 

Between `SpotTick` submissions, the stored multiplier is stale. Interest has accrued in real time, but the on-chain state does not reflect it.

When a trader calls `withdrawCollateral`, the function deducts the withdrawal amount and then checks health: [2](#0-1) 

`getHealth()` calls `spotEngine.getHealthContribution()` → `_calculateProductHealth()` → `_getBalance()` → `getBalance()` → `balanceNormalizedToBalance()`: [3](#0-2) 

This function multiplies `amountNormalized` by the **stored** (stale) `cumulativeBorrowsMultiplierX18`. Since the multiplier has not been advanced to the current time, the borrower's debt is understated, and their health appears better than it actually is.

`withdrawCollateral` never calls `spotEngine.updateStates()` before the health check, so the stale multiplier is always used. [4](#0-3) 

---

### Impact Explanation

A borrower whose actual health (with current accrued interest) is negative can pass the `require(getHealth(...) >= 0)` check and successfully withdraw collateral. This leaves the protocol holding an undercollateralized position. The withdrawn collateral is a real asset transfer out of the protocol. Lenders and the insurance fund absorb the resulting shortfall.

The corrupted state delta: `spotEngine.balances[productId][sender].amountNormalized` is reduced (collateral removed) while the true debt obligation — `amountNormalized * currentMultiplier` — exceeds what the stale health check measured.

---

### Likelihood Explanation

The sequencer submits `SpotTick` transactions on a fixed schedule (not per-block). Any gap between ticks is a window where interest has accrued on-chain but the multiplier is stale. A borrower near the health boundary can observe the last tick timestamp, compute their true health, and submit a `WithdrawCollateral` or `WithdrawCollateralV2` transaction through the sequencer before the next tick advances the multiplier. The entry path is fully unprivileged — any trader with a borrow position can trigger this.

---

### Recommendation

Call `spotEngine.updateStates(dt)` at the beginning of `withdrawCollateral`, using the current block timestamp minus the last recorded `spotTime`, before performing the health check. This mirrors the fix recommended for the Unstoppable analog: always advance the debt accumulator before any action that depends on a health or liquidability check.

---

### Proof of Concept

1. Trader opens a position with a negative spot balance (borrow) and initial health just above zero.
2. Time passes; interest accrues, making true health negative. No `SpotTick` has been submitted yet.
3. Trader submits a `WithdrawCollateral` transaction.
4. `withdrawCollateral` deducts the amount, then calls `getHealth()`.
5. `getHealth()` → `getBalance()` → `balanceNormalizedToBalance()` uses the stale `cumulativeBorrowsMultiplierX18` from `states[productId]`.
6. The stale multiplier understates the debt; health appears ≥ 0.
7. `require(getHealth(sender, healthType) >= 0)` passes.
8. Collateral is transferred out. The position is now undercollateralized relative to true accrued debt.
9. When the next `SpotTick` finally advances the multiplier, the position is revealed as unhealthy and subject to liquidation — but the collateral is already gone. [5](#0-4) [6](#0-5) [1](#0-0)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/SpotEngineState.sol (L180-192)
```text
    function balanceNormalizedToBalance(
        State memory state,
        BalanceNormalized memory balance
    ) internal pure returns (Balance memory) {
        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
    }
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
