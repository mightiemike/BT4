### Title
Division-by-Zero in `socializeSubaccount` Permanently Blocks Liquidation Finalization When `openInterest == 0` — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` performs an unguarded division by `state.openInterest` when attempting to spread a negative `vQuoteBalance` across remaining participants. When `state.openInterest == 0` (the liquidatee was the sole participant in a product), `MathSD21x18.div` explicitly reverts with `"DBZ"`. This causes `ClearinghouseLiq._finalizeSubaccount` to revert atomically, permanently blocking liquidation finalization for the insolvent subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after exhausting insurance coverage, the code attempts to socialize residual negative `vQuoteBalance` across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← no zero-guard
    );
``` [1](#0-0) 

`MathSD21x18.div` unconditionally requires the denominator to be non-zero:

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
``` [2](#0-1) 

`state.openInterest` reaches zero when the liquidatee is the sole remaining participant in a product and their position (`balance.amount`) has already been closed to zero by prior liquidation steps. `_finalizeSubaccount` enforces `balance.amount == 0` for every perp product before calling `socializeSubaccount`:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [3](#0-2) 

A closed position (`amount == 0`) can still carry a negative `vQuoteBalance` (realized loss). If the liquidatee was the only participant, `state.openInterest` is 0 at this point, and the division reverts every time `_finalizeSubaccount` is attempted.

Notably, the protocol already handles `openInterest == 0` correctly in `updateStates` with an explicit `continue` guard:

```solidity
if (state.openInterest == 0) {
    continue;
}
``` [4](#0-3) 

This guard is absent in `socializeSubaccount`.

**Correction to the question's partial-write claim:** Solidity reverts are atomic — no partial state writes persist. The actual impact is a permanent revert of the entire `liquidateSubaccountImpl` call, not inconsistent storage.

---

### Impact Explanation

`_finalizeSubaccount` is called from `liquidateSubaccountImpl` with `txn.productId == type(uint32).max`:

```solidity
if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
    ...
    return;
}
``` [5](#0-4) 

Because `socializeSubaccount` always reverts for this subaccount, `_finalizeSubaccount` always reverts, and the liquidation finalization path is permanently blocked. The insolvent subaccount can never be resolved, leaving unrecoverable bad debt in the protocol with no on-chain path to clear it.

---

### Likelihood Explanation

The precondition — a subaccount being the sole participant in a perp product with a negative `vQuoteBalance` after position closure — is reachable in production:

- Low-liquidity or newly listed products where a single user opens a position
- All other participants exit before the liquidatee is finalized
- The liquidatee's position is closed at a loss (negative `vQuoteBalance`) via prior liquidation steps

No privileged access, governance capture, or external dependency failure is required. The path is reachable through the normal `liquidateSubaccountImpl` endpoint.

---

### Recommendation

Add a zero-check for `state.openInterest` before the division in `socializeSubaccount`. When `openInterest == 0`, there are no participants to socialize to; the residual loss should be absorbed by the insurance fund or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No participants to socialize to; absorb as unrecoverable bad debt
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
``` [6](#0-5) 

---

### Proof of Concept

1. Deploy the protocol with a single perp product.
2. Have a single subaccount (`liquidatee`) open a long position (they are the only participant; `state.openInterest > 0`).
3. All other participants close their positions (or there are none), leaving `state.openInterest == liquidatee.amount`.
4. Price drops; liquidatee becomes under-maintenance health.
5. Liquidator calls `liquidateSubaccountImpl` with the perp `productId` to close the position → `balance.amount` → 0, `balance.vQuoteBalance < 0`, `state.openInterest` → 0.
6. Liquidator calls `liquidateSubaccountImpl` with `productId == type(uint32).max` to finalize.
7. `_finalizeSubaccount` → `perpEngine.socializeSubaccount` → loop hits the product → `balance.vQuoteBalance < 0`, insurance insufficient → `(-balance.vQuoteBalance).div(0)` → revert `"DBZ"`.
8. Assert: the call always reverts; the subaccount can never be finalized. [7](#0-6)

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

**File:** core/contracts/ClearinghouseLiq.sol (L620-627)
```text
        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```
