### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` Permanently Blocks Finalization When `openInterest == 0` — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when residual negative `vQuoteBalance` remains after insurance coverage. There is no guard for the case where `openInterest == 0`. `MathSD21x18.div` explicitly reverts with `"DBZ"` on a zero divisor. Because `_finalizeSubaccount` calls `socializeSubaccount` with no try/catch, the entire finalization transaction reverts, permanently blocking bad-debt resolution and insurance-fund recovery for the affected subaccount.

---

### Finding Description

**Root cause — `PerpEngine.socializeSubaccount`, lines 164–168:**

```solidity
if (balance.vQuoteBalance < 0) {
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← no zero-check; reverts when == 0
    );
``` [1](#0-0) 

`MathSD21x18.div` enforces `require(y != 0, ERR_DIV_BY_ZERO)`: [2](#0-1) 

**How `openInterest` reaches zero:**

`_updateBalance` in `PerpEngineState` tracks `openInterest` as the running sum of `|balance.amount|` across all participants. When every participant's `amount` is 0, `openInterest` is exactly 0: [3](#0-2) 

**Why a subaccount can have `amount == 0` but `vQuoteBalance < 0`:**

`_finalizeSubaccount` requires `balance.amount == 0` for every perp product before proceeding. A position closed at a loss leaves `amount = 0` with a residual negative `vQuoteBalance`. These two fields are independent: [4](#0-3) 

**The call that propagates the revert — no try/catch:**

```solidity
v.insurance = perpEngine.socializeSubaccount(
    txn.liquidatee,
    v.insurance
);
``` [5](#0-4) 

A revert inside `socializeSubaccount` unwinds the entire `liquidateSubaccountImpl` call.

---

### Impact Explanation

When `openInterest == 0` for a perp product and the liquidatee still holds a negative `vQuoteBalance` in that product that insurance cannot fully cover:

1. Every call to `liquidateSubaccountImpl` with `txn.productId == type(uint32).max` (the finalization path) reverts.
2. The underwater subaccount's bad debt cannot be socialized.
3. The insurance fund cannot be applied or recovered for this subaccount.
4. The subaccount is permanently stuck in an unfinalizable state unless an organic participant independently opens a new position in the same market (raising `openInterest > 0`), which the protocol cannot guarantee and an attacker can prevent by immediately closing any such position.

This matches the Critical scope: **permanent lock of insurance funds and prevention of bad-debt recovery**.

---

### Likelihood Explanation

The precondition is reachable without any privileged access:

- A perp market with a single long and a single short is sufficient.
- The short is liquidated down to `amount = 0` with residual negative `vQuoteBalance` (standard liquidation outcome).
- The long holder (attacker or any participant) closes their position, driving `openInterest` to 0.
- The attacker submits a finalization transaction (`productId = type(uint32).max`).

This is achievable in any thin or newly-created perp market and requires only normal user-level transactions.

---

### Recommendation

Add an explicit guard before the division in `PerpEngine.socializeSubaccount`:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No participants to socialize against; absorb loss into protocol
        // or revert with a meaningful error rather than DBZ.
        // One option: leave vQuoteBalance as-is and let the caller handle it,
        // or zero it out and track the unrecovered loss separately.
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(
            state.openInterest
        );
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

The exact accounting treatment when `openInterest == 0` (absorb into protocol reserve, carry forward, etc.) is a design decision, but the division must never execute with a zero divisor.

---

### Proof of Concept

```
1. Deploy protocol with one perp market (productId = 1).
2. Alice deposits USDC and opens a short: amount = -100, vQuoteBalance = +100 * P0.
3. Bob deposits USDC and opens a long:  amount = +100, vQuoteBalance = -100 * P0.
   → openInterest = 200.
4. Price rises to P1 >> P0. Alice is underwater (maintenance health < 0).
5. Liquidator closes Alice's short via normal liquidation steps:
   Alice: amount = 0, vQuoteBalance = -X  (X > 0, residual loss).
   → openInterest = 100 (only Bob's long remains).
6. Bob closes his long voluntarily:
   Bob: amount = 0.
   → openInterest = 0.
7. Attacker submits liquidateSubaccount(liquidatee=Alice, productId=type(uint32).max).
8. _finalizeSubaccount passes the amount==0 check (Alice.amount == 0).
9. Insurance is insufficient to cover -X.
10. socializeSubaccount reaches:
      fundingPerShare = -(-X).div(0)  → MathSD21x18.div reverts "DBZ".
11. Entire transaction reverts. Alice's bad debt is permanently locked.
```

### Citations

**File:** core/contracts/PerpEngine.sol (L164-168)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
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

**File:** core/contracts/ClearinghouseLiq.sol (L313-320)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```
