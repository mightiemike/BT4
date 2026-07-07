### Title
Incorrect Rounding Direction in Normalized Borrow Balance Calculation Allows Borrowers to Systematically Underpay Debt - (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

In `SpotEngineState._updateBalanceNormalized`, the conversion of a borrow amount into its normalized form (the protocol's analog to "debt shares") uses `MathSD21x18.div`, which truncates toward zero in Solidity integer arithmetic. For negative values (borrows), truncation toward zero means the result is *less negative* than the true quotient — i.e., the user is assigned fewer debt shares than they owe. Over time, as `cumulativeBorrowsMultiplierX18` grows, borrowers repay less than they actually borrowed, extracting value from depositors.

---

### Finding Description

The Nado SpotEngine tracks user balances in a normalized form. The actual balance is recovered by multiplying `amountNormalized` by the appropriate cumulative multiplier:

```
actual_balance = amountNormalized × cumulativeMultiplierX18
```

When a user borrows (negative `balanceDelta`), `_updateBalanceNormalized` computes the new normalized balance at line 43:

```solidity
balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
``` [1](#0-0) 

The `div` function in `MathSD21x18` is:

```solidity
int256 result = (int256(x) * ONE_X18) / y;
``` [2](#0-1) 

Solidity integer division truncates toward zero. For a negative numerator and positive denominator:

```
(-100e18 * 1e18) / 1.001e18  =  -99900099900099900099.9...  →  truncates to  -99900099900099900099
```

The mathematically correct result for a borrow (to protect the protocol) should round *away from zero* (more negative): `-99900099900099900100`. Instead, the user receives a `amountNormalized` that is one unit less negative than it should be.

When the user later repays, their debt is computed as `amountNormalized × cumulativeBorrowsMultiplierX18`. Because `amountNormalized` was rounded toward zero, the repayment amount is slightly less than what was borrowed. The rounding error is 1 unit of the X18 fixed-point representation per borrow operation, but it is systematic and accumulates across every borrow.

The same truncation-toward-zero behavior also affects `totalBorrowsNormalized` at line 48:

```solidity
state.totalBorrowsNormalized -= balance.amountNormalized;
``` [3](#0-2) 

Since `amountNormalized` is less negative than it should be, `totalBorrowsNormalized` is understated, which suppresses the utilization ratio and therefore the borrow interest rate, compounding the loss for depositors.

---

### Impact Explanation

- **Direct loss per borrow**: Each borrow operation rounds the debt shares toward zero by up to 1 unit of the X18 fixed-point representation. When the user repays, they repay slightly less than they borrowed. The shortfall is absorbed by the pool's depositors.
- **Understated utilization**: `totalBorrowsNormalized` is systematically understated, causing `_updateState` to compute a lower utilization ratio and therefore a lower borrow rate, reducing depositor yield.
- **Cumulative effect**: With high borrow volume (many users, many operations), the aggregate loss to depositors is non-trivial. The effect is amplified as `cumulativeBorrowsMultiplierX18` grows over time.

---

### Likelihood Explanation

This is triggered by every borrow operation through the normal user flow. No special privileges are required. Any user who borrows spot assets via the `Endpoint` → `Clearinghouse` → `SpotEngine.updateBalance` path triggers the rounding error on every call. [4](#0-3) 

---

### Recommendation

When `newAmount` is negative (borrow), the division should round away from zero (i.e., toward more negative values) to ensure the protocol is never shortchanged. Introduce a rounding-aware division helper:

```solidity
// For borrows (newAmount < 0), round away from zero:
if (newAmount < 0) {
    // round down (more negative) by subtracting 1 if there is a remainder
    int256 raw = int256(newAmount) * ONE_X18;
    int256 q = raw / int256(cumulativeMultiplierX18);
    if (raw % int256(cumulativeMultiplierX18) != 0) {
        q -= 1; // round away from zero for negative values
    }
    balance.amountNormalized = int128(q);
} else {
    balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
}
```

This mirrors the fix described in the referenced patches (`030d9cb`, `01beccb`) for the Move-based lending pool, adapted to Solidity signed fixed-point arithmetic.

---

### Proof of Concept

**Setup**: `cumulativeBorrowsMultiplierX18 = 1.001e18` (pool has been active for some time).

**Step 1 — User borrows 100 tokens** (`balanceDelta = -100e18`):
- `newAmount = 0 + (-100e18) = -100e18`
- `amountNormalized = (-100e18 * 1e18) / 1.001e18`
  - Exact: `-99900099900099900099.9...`
  - Stored (truncated toward zero): `-99900099900099900099`
  - Correct (rounded away from zero): `-99900099900099900100`

**Step 2 — User repays full debt** (protocol computes repayment as `amountNormalized × multiplier`):
- Repayment = `(-99900099900099900099 * 1.001e18) / 1e18`
  - ≈ `-99999999999999999999` (1 wei short of `-100e18`)

**Result**: The user borrowed `100e18` but repays `99999999999999999999` — 1 wei is extracted from depositors per borrow. With thousands of borrow operations per day across all users, the aggregate loss is material. [5](#0-4) [2](#0-1)

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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-68)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
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
