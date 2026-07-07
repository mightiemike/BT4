### Title
Borrow Normalization Rounds Toward Zero in `_updateBalanceNormalized`, Understating Borrower Debt on Partial Repayment — (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

When a borrower partially repays their debt, `SpotEngineState._updateBalanceNormalized` computes the new normalized borrow amount using `MathSD21x18.div`, which truncates toward zero. For a negative `newAmount` (remaining borrow) divided by a positive `cumulativeBorrowsMultiplierX18`, the result is less negative than the true mathematical value. The stored `amountNormalized` is therefore smaller in absolute value than it should be, understating the borrower's remaining debt and `totalBorrowsNormalized`. This is the direct analog to the reported `LiquidityPool#repay` rounding-down bug.

---

### Finding Description

In `SpotEngineState._updateBalanceNormalized`, after computing the new real-unit amount for a partial repayment:

```solidity
// core/contracts/SpotEngineState.sol line 33-35
int128 newAmount = balance.amountNormalized.mul(
    cumulativeMultiplierX18
) + balanceDelta;
```

The new normalized amount is stored at line 43:

```solidity
// core/contracts/SpotEngineState.sol line 43
balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
```

`MathSD21x18.div` is implemented as:

```solidity
// core/contracts/libraries/MathSD21x18.sol line 62-69
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
        int256 result = (int256(x) * ONE_X18) / y;
        ...
    }
}
```

Solidity's `/` operator truncates toward zero. When `newAmount` is negative (the position is still a borrow after partial repayment) and `cumulativeMultiplierX18` is positive (always > `ONE_X18` after any interest accrual), the division produces a result that is **less negative** than the true value — i.e., the stored normalized borrow is smaller in absolute value than it should be.

At line 48, `totalBorrowsNormalized` is updated using this understated value:

```solidity
// core/contracts/SpotEngineState.sol line 45-49
if (balance.amountNormalized > 0) {
    state.totalDepositsNormalized += balance.amountNormalized;
} else {
    state.totalBorrowsNormalized -= balance.amountNormalized;
}
```

Because `balance.amountNormalized` is less negative than it should be, `totalBorrowsNormalized` is incremented by a smaller absolute value than the true borrow, understating total protocol borrows.

The entry path is any operation that calls `SpotEngine.updateBalance` with a positive `amountDelta` on a subaccount that currently holds a negative spot balance — concretely, `Clearinghouse.depositCollateral` for a borrower, or any trade settlement that partially reduces a negative balance. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Impact: Low.** Each partial repayment event understates the remaining normalized borrow by at most 1 unit in the fixed-point representation (1 ULP of the X18 format, i.e., `1e-18` in normalized units). Multiplied by `cumulativeBorrowsMultiplierX18`, the real-unit understatement per event is at most `cumulativeBorrowsMultiplierX18 / ONE_X18` — a dust amount. However, this dust accumulates across every partial repayment across all borrowers over the protocol's lifetime. The cumulative effect is that `totalBorrowsNormalized` is understated, the utilization ratio is slightly lower than reality, interest rates are slightly suppressed, and depositors receive slightly less yield than they are owed. The borrower's individual debt is also understated, meaning they can close their position by repaying slightly less than the true accrued amount. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

**Likelihood: High.** The rounding error is triggered by every partial repayment of any borrow position. In Nado, borrows arise implicitly whenever a subaccount's spot balance goes negative (e.g., leveraged spot trading, cross-margin usage). Any subsequent deposit or trade that partially reduces a negative balance calls `_updateBalanceNormalized` with a positive `balanceDelta` on a negative `amountNormalized`, hitting this code path unconditionally. No special conditions or attacker intent are required; normal protocol usage is sufficient. [6](#0-5) [7](#0-6) 

---

### Recommendation

When `newAmount` is negative (the position remains a borrow after the delta), round the division **away from zero** (i.e., toward negative infinity) rather than toward zero. This ensures the stored normalized borrow is never understated. Concretely, after computing `result = (int256(x) * ONE_X18) / y` in `MathSD21x18.div`, if the result is negative and there is a nonzero remainder, subtract 1 from the result before returning. Alternatively, add a dedicated `divRoundDown` (toward negative infinity) variant in `MathSD21x18` and use it specifically in `_updateBalanceNormalized` for the borrow normalization step at line 43. [8](#0-7) [9](#0-8) 

---

### Proof of Concept

1. `cumulativeBorrowsMultiplierX18 = 1.001e18` (0.1% interest accrued).
2. User borrows 100 USDC: `amountNormalized = -100e18 / 1.001e18 = -99900099900099900` (truncated toward zero, already slightly understated, but this is the initial borrow normalization — acceptable at borrow time).
3. User partially repays 50 USDC: `balanceDelta = +50e18`.
4. `newAmount = (-99900099900099900 * 1.001e18 / 1e18) + 50e18`
   `= -99999999999999900 + 50e18 = -49999999999999900` (approximately -50e18, still negative).
5. `balance.amountNormalized = (-49999999999999900 * 1e18) / 1.001e18`
   `= -49950049950049950` (truncated toward zero).
6. True value: `-49999999999999900 / 1.001 = -49950049950049850.149...` → should store `-49950049950049851` (rounding away from zero), but stores `-49950049950049850`.
7. The stored normalized borrow is 1 ULP less negative than it should be. `totalBorrowsNormalized` is understated by 1 ULP.
8. Repeated across thousands of partial repayments, the cumulative understatement of `totalBorrowsNormalized` grows, suppressing the utilization ratio and interest rates, reducing depositor yield. [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/SpotEngineState.sol (L15-50)
```text
    function _updateBalanceNormalized(
        State memory state,
        BalanceNormalized memory balance,
        int128 balanceDelta
    ) internal pure {
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized -= balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized += balance.amountNormalized;
        }

        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
    }
```

**File:** core/contracts/SpotEngineState.sol (L61-64)
```text
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
```

**File:** core/contracts/libraries/MathSD21x18.sol (L54-69)
```text
    function mul(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            int256 result = (int256(x) * y) / ONE_X18;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
    }

    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
    }
```

**File:** core/contracts/SpotEngine.sol (L176-205)
```text
    function updateBalance(
        uint32 productId,
        bytes32 subaccount,
        int128 amountDelta,
        int128 quoteDelta
    ) external {
        require(productId != QUOTE_PRODUCT_ID, ERR_INVALID_PRODUCT);
        _assertInternal();
        State memory state = states[productId];
        State memory quoteState = states[QUOTE_PRODUCT_ID];

        BalanceNormalized memory balance = balances[productId][subaccount];

        BalanceNormalized memory quoteBalance = balances[QUOTE_PRODUCT_ID][
            subaccount
        ];

        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }

        _updateBalanceNormalized(state, balance, amountDelta);
        _updateBalanceNormalized(quoteState, quoteBalance, quoteDelta);

        _setBalanceAndUpdateBitmap(productId, subaccount, balance);
        _setBalanceAndUpdateBitmap(QUOTE_PRODUCT_ID, subaccount, quoteBalance);

        _setState(productId, state);
        _setState(QUOTE_PRODUCT_ID, quoteState);
    }
```
