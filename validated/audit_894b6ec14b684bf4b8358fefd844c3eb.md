### Title
Division by Zero on Zero `openInterest` Permanently Blocks Subaccount Finalization and Loss Socialization — (`File: core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` without a zero-guard. When a perp product has no remaining open positions (`openInterest == 0`) but the liquidatee still carries a negative `vQuoteBalance`, the division reverts with `"DBZ"`, permanently blocking the finalization path for that insolvent subaccount and preventing protocol-wide loss socialization.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after confirming `balance.vQuoteBalance < 0` and exhausting insurance coverage, the code computes a per-share funding adjustment to distribute the residual loss across all open-interest holders:

```solidity
// core/contracts/PerpEngine.sol  lines 164–171
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest          // <-- denominator, no zero-check
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
```

`MathSD21x18.div` enforces `require(y != 0, "DBZ")`:

```solidity
// core/contracts/libraries/MathSD21x18.sol  lines 62–68
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
        ...
    }
}
```

`state.openInterest` can legitimately be zero when the liquidatee was the sole (or last remaining) trader in a perp product and their position was already closed (`balance.amount == 0`) before socialization runs. This is not a hypothetical edge case: `_finalizeSubaccount` in `ClearinghouseLiq.sol` **requires** all perp `balance.amount == 0` before calling `socializeSubaccount`:

```solidity
// core/contracts/ClearinghouseLiq.sol  lines 313–320
for (uint32 i = 0; i < v.perpIds.length; ++i) {
    uint32 perpId = v.perpIds[i];
    IPerpEngine.Balance memory balance = perpEngine.getBalance(perpId, txn.liquidatee);
    require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
}
```

A subaccount with `balance.amount == 0` contributes zero to `openInterest`. If no other trader holds an open position in that product, `state.openInterest == 0` at the moment `socializeSubaccount` is invoked, and the division reverts.

---

### Impact Explanation

The revert propagates through the full call stack:

```
Endpoint.submitTransactions
  → Clearinghouse.liquidateSubaccount  (delegatecall)
    → ClearinghouseLiq.liquidateSubaccountImpl
      → _finalizeSubaccount
        → perpEngine.socializeSubaccount  ← REVERT "DBZ"
```

The entire `liquidateSubaccountImpl` transaction reverts. Because `_finalizeSubaccount` is the only code path that clears an insolvent subaccount's residual negative `vQuoteBalance`, the subaccount is permanently stuck: it cannot be finalized, its losses cannot be socialized, and the protocol's solvency accounting is corrupted. Any subsequent attempt to finalize the same subaccount will hit the same revert as long as `openInterest` remains zero for that product.

---

### Likelihood Explanation

The condition is reachable without any privileged access. A single trader who opens and then closes a position in a perp product (leaving a negative `vQuoteBalance` from funding or mark-to-market losses) while being the only participant in that product satisfies all preconditions. This is realistic for newly listed or low-liquidity perp products. The liquidator is an unprivileged caller who triggers the path via the standard `LiquidateSubaccount` transaction type.

---

### Recommendation

Add a zero-guard before the division in `socializeSubaccount`. If `openInterest` is zero there are no other participants to socialize the loss against; the residual loss should either be absorbed by the insurance fund or written off, consistent with the protocol's existing socialization intent:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest != 0) {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
    }
    balance.vQuoteBalance = 0;
}
```

---

### Proof of Concept

1. Product `perpId = 2` is listed; only Alice opens a long position of size `S` at price `P`, so `state.openInterest = S`.
2. Alice closes her position entirely (`balance.amount = 0`). `openInterest` returns to `0`. Alice's `vQuoteBalance` is negative (e.g., she closed at a loss: `vQuoteBalance = -L`).
3. Alice's maintenance health drops below zero (e.g., her quote balance is insufficient).
4. A liquidator submits `LiquidateSubaccount` with `productId = type(uint32).max` (finalize path).
5. `_finalizeSubaccount` passes the `balance.amount == 0` check for `perpId = 2`.
6. `perpEngine.socializeSubaccount` is called; it finds `balance.vQuoteBalance = -L < 0` and `state.openInterest = 0`.
7. `(-(-L)).div(0)` → `MathSD21x18.div` reverts with `"DBZ"`.
8. The entire liquidation transaction reverts. Alice's subaccount can never be finalized. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/PerpEngine.sol (L164-172)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
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

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```
