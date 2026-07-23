Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of actual caller `sender`, allowing allowlist bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller/payer) and gates only `owner` (the position recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` validated only for non-zero, any unprivileged address can bypass the deposit allowlist by supplying an allowlisted address as `owner` while paying tokens themselves.

## Finding Description
In `DepositAllowlistExtension.beforeAddLiquidity` (L32–42), the first parameter is unnamed and ignored; only `owner` is checked against `allowedDepositor`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPool.addLiquidity` (L191) passes `msg.sender` as `sender` and the caller-supplied `owner` as the position recipient:
```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (L56–68) accepts an arbitrary `owner` and validates only that it is non-zero via `_validateOwner` (L247–249), then stores `msg.sender` (the actual payer, `bob`) in the transient pay context:
```solidity
return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**Exploit path:**
1. Admin calls `setAllowedToDeposit(pool, alice, true)` — `alice` is allowlisted.
2. `bob` (not allowlisted) calls `liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")`.
3. `_validateOwner(alice)` passes (`alice != address(0)`).
4. Adder calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")`.
5. Pool calls `_beforeAddLiquidity(adder, alice, salt, deltas, "")`.
6. Extension checks `allowedDepositor[pool][alice]` → `true` → passes.
7. LP shares minted to `alice`; tokens pulled from `bob` via callback.
8. `bob` has deposited into a restricted pool without being allowlisted.

The same bypass applies to `addLiquidityWeighted` (L106–115), which also passes the caller-supplied `owner` in both the probe and paying calls.

The existing `_validateOwner` guard is insufficient — it only rejects `address(0)`, not unapproved depositors. No other guard in the adder or pool checks whether the actual payer is allowlisted.

## Impact Explanation
The deposit allowlist is bypassed entirely. Any unprivileged address can add liquidity to a pool the admin intended to restrict, by routing through the liquidity adder with an allowlisted `owner`. This is an admin-boundary break: the pool admin's access-control invariant is circumvented by an unprivileged path with no special role required. The pool receives liquidity from an unprivileged source, and LP shares are minted to the allowlisted address while tokens are pulled from the unauthorized depositor.

## Likelihood Explanation
Medium. The attacker only needs to know one allowlisted address (readable on-chain from the public `allowedDepositor` mapping) and be willing to pay tokens (which are minted as LP shares to the allowlisted address). No special privilege is required; any EOA or contract can trigger this. The attack is repeatable and requires no flash loan or complex setup.

## Recommendation
Check `sender` (the actual caller/payer) instead of `owner` in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`sender` is `msg.sender` of the pool's `addLiquidity` call — the entity actually providing tokens — which is the correct identity to gate.

## Proof of Concept

```solidity
// Setup: pool deployed with DepositAllowlistExtension; alice is allowlisted, bob is not
depositAllowlist.setAllowedToDeposit(pool, alice, true);

// bob bypasses the gate via the liquidity adder
vm.prank(bob);
liquidityAdder.addLiquidityExactShares(
    pool,
    alice,   // owner = allowlisted address
    salt,
    deltas,
    max0,
    max1,
    ""
);
// Extension checks allowedDepositor[pool][alice] == true → passes
// LP shares minted to alice, tokens pulled from bob
// bob has deposited into a restricted pool without being allowlisted
// Assert: bob's token balances decreased, alice received LP shares
```