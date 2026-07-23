Audit Report

## Title
DepositAllowlistExtension Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Users to Bypass the Deposit Gate via MetricOmmPoolLiquidityAdder - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and gates only on `owner`. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary caller-supplied `owner` (validated only as non-zero), any unprivileged user can name an allowlisted address as `owner`, route through the public liquidity adder, and satisfy the allowlist check while being the actual payer — fully bypassing the deposit gate.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but leaves `sender` unnamed and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPool.addLiquidity` dispatches the hook with `msg.sender` as `sender` and the caller-supplied `owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

When the call originates from `MetricOmmPoolLiquidityAdder`, `msg.sender` is the liquidity adder contract (not the actual user), and `owner` is whatever the user passed in — validated only as non-zero:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only rejects address(0)
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

**Exploit path:**
1. Pool admin deploys pool with `DepositAllowlistExtension`; allowlists only Alice.
2. Non-allowlisted Bob calls `liquidityAdder.addLiquidityExactShares(pool, Alice, salt, deltas, ...)`.
3. Pool calls `_beforeAddLiquidity(liquidityAdder, Alice, ...)`.
4. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → passes.
5. Callback pulls tokens from Bob (the stored payer); LP shares are minted to Alice.
6. Bob has deposited into a gated pool without being allowlisted.

The existing `_validateOwner` guard only rejects `address(0)` and provides no allowlist protection. The extension never inspects `sender` (the liquidity adder) or the payer (Bob).

## Impact Explanation

The deposit allowlist invariant — "only approved depositors may add liquidity" — is broken for every pool using `DepositAllowlistExtension` alongside the standard public liquidity adder. Any unprivileged user can manipulate pool state (bin cursor, price impact, liquidity composition) that the allowlist was designed to restrict. This constitutes broken core pool functionality causing potential loss of funds and qualifies under the allowed impact gate.

## Likelihood Explanation

`MetricOmmPoolLiquidityAdder` is the standard public periphery entry point. No privileged access is required. The only precondition is knowing one allowlisted address, which is observable on-chain from prior deposits or emitted events. The attack is repeatable by any user at any time a pool uses this extension.

## Recommendation

Gate on `sender` (the actual `addLiquidity` caller) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This ensures the identity initiating the `addLiquidity` call — including through the liquidity adder — is the one the pool admin intended to gate.

## Proof of Concept

```
1. Deploy pool with DepositAllowlistExtension; call setAllowedToDeposit(pool, Alice, true).
2. Bob (not allowlisted) calls:
     liquidityAdder.addLiquidityExactShares(pool, Alice, salt, deltas, max0, max1, extensionData)
3. Pool calls: _beforeAddLiquidity(liquidityAdder, Alice, salt, deltas, extensionData)
4. Extension checks: allowedDepositor[pool][Alice] == true → passes
5. Callback pulls tokens from Bob; LP shares minted to Alice.
6. Assert: Bob's token balances decreased; pool accepted the deposit despite Bob not being allowlisted.
   Foundry test: vm.prank(bob); liquidityAdder.addLiquidityExactShares(..., alice, ...); — should revert but does not.
```