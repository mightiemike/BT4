### Title
Stale `cumulativeBorrowsMultiplierX18` in `assertUtilization` Allows Over-Withdrawal When Protocol Is Near-Full Utilization - (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.assertUtilization` reads `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` directly from storage without first accruing interest via `_updateState`. Because `cumulativeBorrowsMultiplierX18` grows faster than `cumulativeDepositsMultiplierX18`, stale values understate actual borrow amounts relative to deposits. This allows `withdrawCollateral` to pass the utilization guard when the true post-accrual state would fail it, potentially enabling a withdrawal that leaves the protocol insolvent.

---

### Finding Description

`SpotEngineState.updateStates` is the only function that advances `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` to reflect elapsed interest: [1](#0-0) 

Between sequencer-submitted `updateStates` calls, both multipliers remain frozen at their last-written values. `SpotEngine.assertUtilization` is an `external view` function that computes utilization directly from those frozen storage values: [2](#0-1) 

`getStateAndBalance` (called inside `assertUtilization`) simply reads `states[productId]` from storage and applies `balanceNormalizedToBalance`; it never calls `_updateState`: [3](#0-2) 

`Clearinghouse.withdrawCollateral` calls `assertUtilization` after debiting the user's balance: [4](#0-3) 

Because `cumulativeBorrowsMultiplierX18` compounds at the borrow rate (which exceeds the deposit rate), the longer the gap since the last `updateStates`, the more the stale check understates actual borrows. If the protocol is near 100% utilization, the stale check can show `totalDeposits >= totalBorrows` while the true accrued state has `totalBorrows > totalDeposits`.

---

### Impact Explanation

A user withdrawal that is processed before the next `updateStates` call can pass `assertUtilization` even when the true post-accrual utilization exceeds 100%. The withdrawal reduces `totalDepositsNormalized` while `totalBorrowsNormalized` is unchanged, leaving the protocol in a state where outstanding borrows exceed available deposits — i.e., protocol insolvency. Depositors who attempt to withdraw after this point will find insufficient collateral backing their positions.

The corrupted invariant is: `totalDepositsNormalized * cumulativeDepositsMultiplierX18 >= totalBorrowsNormalized * cumulativeBorrowsMultiplierX18`.

---

### Likelihood Explanation

The sequencer calls `updateStates` periodically but not atomically before every withdrawal. Any withdrawal processed in a batch where `updateStates` has not yet been called for that epoch uses stale multipliers. At high utilization (≥95%) with a non-trivial borrow rate (e.g., 10–100% APY), even a gap of minutes to hours is sufficient for the true borrow multiplier to exceed the deposit multiplier enough to flip the utilization check. Users can submit `WithdrawCollateral` transactions through the slow-mode queue, which the sequencer must process within a deadline, providing a concrete unprivileged entry path.

---

### Recommendation

`assertUtilization` should compute the up-to-date multipliers by applying the accrued interest for the elapsed time before comparing totals, analogous to how `_updateState` computes them. Alternatively, `withdrawCollateral` in `Clearinghouse.sol` should call `updateStates` (or an equivalent internal accrual step) before invoking `assertUtilization`, ensuring the check always operates on current values.

---

### Proof of Concept

1. Protocol state: `totalDepositsNormalized = 1000`, `totalBorrowsNormalized = 990`, `cumulativeDepositsMultiplierX18 = 1.00`, `cumulativeBorrowsMultiplierX18 = 1.00` (utilization = 99%).
2. `updateStates` is not called for 1 day. Borrow rate at 99% utilization is ~105% APY (per `_updateState` formula). After 1 day, the true `cumulativeBorrowsMultiplierX18 ≈ 1.00288`, making true `totalBorrows ≈ 1012.8 > totalDeposits = 1000`.
3. User submits `WithdrawCollateral` for 5 units. Sequencer processes it without calling `updateStates` first.
4. `Clearinghouse.withdrawCollateral` debits 5 units, then calls `spotEngine.assertUtilization`.
5. `assertUtilization` reads stale multipliers: `totalDeposits = 1000 * 1.00 = 1000`, `totalBorrows = 990 * 1.00 = 990`. Check passes.
6. True state after withdrawal: `totalDeposits ≈ 995`, `totalBorrows ≈ 1012.8`. Protocol is insolvent. [2](#0-1) [5](#0-4) [1](#0-0)

### Citations

**File:** core/contracts/SpotEngineState.sol (L236-244)
```text
    function getStateAndBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (State memory, Balance memory)
    {
        State memory state = states[productId];
        BalanceNormalized memory balance = balances[productId][subaccount];
        return (state, balanceNormalizedToBalance(state, balance));
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

**File:** core/contracts/SpotEngine.sol (L232-241)
```text
    function assertUtilization(uint32 productId) external view {
        (State memory _state, ) = getStateAndBalance(productId, X_ACCOUNT);
        int128 totalDeposits = _state.totalDepositsNormalized.mul(
            _state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = _state.totalBorrowsNormalized.mul(
            _state.cumulativeBorrowsMultiplierX18
        );
        require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
    }
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
