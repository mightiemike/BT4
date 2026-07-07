### Title
Non-Functional Post-Trade Health Guard Due to Stub `isHealthy` Implementation — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange` declares `isHealthy` as a `virtual` function used as a post-trade health guard in `matchOrders`, but the concrete implementation unconditionally returns `true`. This makes the on-chain health enforcement for order matching completely non-functional, mirroring the external report's pattern of a declared guard that is never actually implemented.

---

### Finding Description

In `OffchainExchange.sol`, the function `isHealthy` is declared as an internal virtual function: [1](#0-0) 

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
```

This function is used as the sole post-trade health check inside `matchOrders`, after all balance deltas have been applied to both the taker and maker: [2](#0-1) 

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
```

`OffchainExchange` is a **concrete contract** (not abstract): [3](#0-2) 

It never overrides `isHealthy` with actual health logic. The `virtual` keyword signals intent for subclass override, but no override exists in the deployed contract. The result is that both `require` statements at lines 826–827 are permanently satisfied regardless of the post-trade account state.

This is structurally identical to the external report's bug: a guard modifier (`whenNotPaused`) is applied to critical functions, but the mechanism that would ever make it trigger (`pause()`) is never implemented. Here, the guard (`isHealthy`) is applied to the critical trade-settlement path, but the mechanism that would ever make it reject (`actual health computation`) is never implemented.

---

### Impact Explanation

After `matchOrders` applies balance deltas to both parties via `_updateBalances`, the only on-chain check preventing an unhealthy post-trade state is `isHealthy`. Since it always returns `true`, the protocol's solvency invariant for the trading path is broken:

- A subaccount can execute trades that drive its initial health below zero.
- The protocol's on-chain safety net for order matching is entirely absent.
- All health enforcement in the trading path is delegated exclusively to the off-chain sequencer, with no trustless backstop.

The corrupted state is: `subaccount spot/perp balance` after `_updateBalances` can violate `getHealth(..., INITIAL) >= 0` with no on-chain rejection. [4](#0-3) 

---

### Likelihood Explanation

The `matchOrders` function is `onlyEndpoint`, so it is invoked by the sequencer processing user-submitted signed orders. A user submits a signed order; the sequencer matches it on-chain. The sequencer has off-chain health checks, but the on-chain check is the trustless safety net. If the sequencer has a bug, is under load, or is compromised, no on-chain mechanism rejects trades that leave accounts unhealthy. The entry path is: user submits a signed order → sequencer calls `submitTransactionsChecked` → `processTransaction` → `matchOrders` → `isHealthy` returns `true` unconditionally.

Likelihood is **Medium**: direct exploitation requires sequencer cooperation or failure, but the broken invariant is a concrete protocol-level flaw with no mitigation on-chain.

---

### Recommendation

Override `isHealthy` in `OffchainExchange` with an actual health check delegating to the clearinghouse, for example:

```solidity
function isHealthy(bytes32 subaccount) internal view override returns (bool) {
    return clearinghouse.getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
}
```

This mirrors the fix in the external report: implement the declared but missing control function so the guard actually enforces the intended invariant.

---

### Proof of Concept

1. User A has collateral worth exactly 1 unit of initial health margin.
2. User A submits a signed order to buy a large perp position that would consume 10 units of initial health margin.
3. Sequencer (or a buggy sequencer) calls `submitTransactionsChecked` → `matchOrders`.
4. `_updateBalances` applies the perp balance delta to User A's subaccount.
5. `require(isHealthy(taker.order.sender), ERR_INVALID_TAKER)` is evaluated.
6. `isHealthy` returns `true` unconditionally — the require passes.
7. User A now holds a position with initial health far below zero, with no on-chain rejection.
8. The protocol's solvency invariant is violated; User A is effectively insolvent with an open position. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/OffchainExchange.sol (L20-24)
```text
contract OffchainExchange is
    IOffchainExchange,
    EndpointGated,
    EIP712Upgradeable
{
```

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L811-827)
```text
        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );

        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
```
