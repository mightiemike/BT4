### Title
Negative `canSettle` in `PerpEngine.settlePnl` Inflates `availableSettle` and Drains Spot Collateral — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.getSettlementState` returns `MathHelper.min(positionPnl, state.availableSettle)` with no floor at zero. When `positionPnl < 0` and `state.availableSettle > 0`, the returned `canSettle` is negative. `settlePnl` then subtracts this negative value from both `state.availableSettle` and `balance.vQuoteBalance`, increasing both. The negative `totalSettled` is forwarded to `Clearinghouse._settlePnl`, which calls `spotEngine.updateBalance(QUOTE_PRODUCT_ID, subaccount, negative)`, draining the subaccount's spot quote balance. The net result is an inflated `availableSettle` pool with no real backing and an artificially improved perp `vQuoteBalance` at the cost of spot collateral.

---

### Finding Description

**`getSettlementState`** computes `canSettle` as:

```solidity
availableSettle = MathHelper.min(
    calculatePositionPnl(balance, productId),  // may be negative
    state.availableSettle                       // positive
);
``` [1](#0-0) 

`MathHelper.min` is a plain signed comparison with no zero-floor:

```solidity
function min(int128 a, int128 b) internal pure returns (int128) {
    return a < b ? a : b;
}
``` [2](#0-1) 

When `positionPnl < 0 < state.availableSettle`, `min` returns the negative `positionPnl`. Back in `settlePnl`:

```solidity
state.availableSettle -= canSettle;   // -= negative → increases
balance.vQuoteBalance -= canSettle;   // -= negative → increases
totalSettled += canSettle;            // negative
``` [3](#0-2) 

`Clearinghouse._settlePnl` then forwards the negative `amountSettled` to the spot engine:

```solidity
int128 amountSettled = perpEngine.settlePnl(subaccount, productIds);
_spotEngine().updateBalance(QUOTE_PRODUCT_ID, subaccount, amountSettled);
``` [4](#0-3) 

This decreases the subaccount's spot quote balance by `|canSettle|`.

---

### Impact Explanation

For a single settlement call with `positionPnl = -X` (X > 0) and `availableSettle > 0`:

| Variable | Before | After | Delta |
|---|---|---|---|
| `state.availableSettle` | `A` | `A + X` | `+X` (inflated, unbacked) |
| `balance.vQuoteBalance` | `V` | `V + X` | `+X` (artificial improvement) |
| Spot quote balance | `S` | `S - X` | `-X` (real collateral drained) |

The `availableSettle` pool grows without any real value entering it. Subsequent legitimate settlements draw from this inflated pool, creating protocol bad debt. The subaccount's perp health improves while its spot collateral is destroyed.

---

### Likelihood Explanation

The entry point is `Clearinghouse.settlePnl` gated by `onlyEndpoint`. [5](#0-4) 

The sequencer routinely submits `SettlePnl` transactions for subaccounts. There is no off-chain or on-chain filter that prevents a subaccount with negative unrealized PnL from being included in a `SettlePnl` batch. The contract itself imposes no guard. A sequencer bug, a misconfigured settlement bot, or a deliberate inclusion of a negative-PnL subaccount in the batch is sufficient to trigger this path — no key compromise is required.

---

### Recommendation

Clamp `canSettle` to zero from below in `getSettlementState`:

```solidity
availableSettle = MathHelper.max(
    0,
    MathHelper.min(
        calculatePositionPnl(balance, productId),
        state.availableSettle
    )
);
```

This ensures that a subaccount with negative unrealized PnL produces `canSettle = 0`, leaving all state variables unchanged, which is the correct no-op behavior.

---

### Proof of Concept

State setup:
- `state.availableSettle = 100e18`
- `balance.amount = 1e18` (long 1 unit)
- `balance.vQuoteBalance = -200e18`
- `priceX18 = 50e18` → `positionPnl = 50e18 - 200e18 = -150e18`

Call: `Endpoint → Clearinghouse.settlePnl → PerpEngine.settlePnl`

1. `getSettlementState`: `canSettle = min(-150e18, 100e18) = -150e18`
2. `state.availableSettle -= -150e18` → `state.availableSettle = 250e18` (+150e18, unbacked)
3. `balance.vQuoteBalance -= -150e18` → `balance.vQuoteBalance = -50e18` (improved by 150e18)
4. `totalSettled = -150e18`
5. `spotEngine.updateBalance(QUOTE_PRODUCT_ID, subaccount, -150e18)` → spot quote balance decreases by 150e18

Assert: `availableSettle_after (250e18) > availableSettle_before (100e18)` — invariant broken. [6](#0-5)

### Citations

**File:** core/contracts/PerpEngine.sol (L77-105)
```text
    function settlePnl(bytes32 subaccount, uint256 productIds)
        external
        returns (int128)
    {
        _assertInternal();
        int128 totalSettled = 0;

        while (productIds != 0) {
            uint32 productId = uint32(productIds & ((1 << 32) - 1));
            // otherwise it means the product is a spot.
            if (productId % 2 == 0) {
                (
                    int128 canSettle,
                    State memory state,
                    Balance memory balance
                ) = getSettlementState(productId, subaccount);

                state.availableSettle -= canSettle;
                balance.vQuoteBalance -= canSettle;

                totalSettled += canSettle;

                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
            productIds >>= 32;
        }
        return totalSettled;
    }
```

**File:** core/contracts/PerpEngine.sol (L135-138)
```text
        availableSettle = MathHelper.min(
            calculatePositionPnl(balance, productId),
            state.availableSettle
        );
```

**File:** core/contracts/libraries/MathHelper.sol (L15-17)
```text
    function min(int128 a, int128 b) internal pure returns (int128) {
        return a < b ? a : b;
    }
```

**File:** core/contracts/Clearinghouse.sol (L620-626)
```text
        int128 amountSettled = perpEngine.settlePnl(subaccount, productIds);

        _spotEngine().updateBalance(
            QUOTE_PRODUCT_ID,
            subaccount,
            amountSettled
        );
```

**File:** core/contracts/Clearinghouse.sol (L629-637)
```text
    function settlePnl(bytes calldata transaction) external onlyEndpoint {
        IEndpoint.SettlePnl memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.SettlePnl)
        );
        for (uint128 i = 0; i < txn.subaccounts.length; ++i) {
            _settlePnl(txn.subaccounts[i], txn.productIds[i]);
        }
    }
```
