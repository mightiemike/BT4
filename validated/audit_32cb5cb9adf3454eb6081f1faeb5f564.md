Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of Actual `sender`, Allowing Complete Deposit Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the real transaction initiator) and enforces the allowlist against the caller-supplied `owner` parameter instead. Because `owner` is a free parameter in `MetricOmmPool.addLiquidity`, any unprivileged address can pass an allowlisted address as `owner` to satisfy the check and inject liquidity into a restricted pool, completely nullifying the deposit allowlist.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument and the caller-supplied `owner` as the second to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives these as `(address sender, address owner, ...)` per the `IMetricOmmExtensions` interface, but the implementation leaves the first parameter unnamed (discarded) and enforces the allowlist only on `owner`:

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

Since `owner` is a free parameter chosen by the caller, any address can pass an allowlisted address as `owner`, satisfy `allowedDepositor[pool][owner] == true`, and have the deposit accepted. The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry between the two sibling extensions confirms the deposit check is checking the wrong field. The existing `nonReentrant` guard and `LiquidityDeltaLengthMismatch` check in `addLiquidity` do not constrain the `owner` parameter in any way.

## Impact Explanation
The deposit allowlist is the primary access-control mechanism for restricting who may provide liquidity to a pool. Any unprivileged address can bypass it entirely. The resulting position is credited to the allowlisted `owner` address (the attacker forfeits deposited tokens to that address's position), but the pool's liquidity composition is altered without authorization. This constitutes broken core pool functionality: the allowlist guard does not protect the invariant it is designed to enforce, and pools deployed for permissioned or compliance-gated environments have their core access control silently nullified.

## Likelihood Explanation
Exploitation requires no special privilege, no flash loan, no oracle manipulation, and no admin cooperation. Any EOA or contract can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, "")`. The set of allowlisted addresses is readable from public on-chain state (`allowedDepositor` is a public mapping). The attack is repeatable at any time.

## Recommendation
Replace the unnamed first parameter with `sender` and enforce the allowlist against it, mirroring `SwapAllowlistExtension`:

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

## Proof of Concept
1. Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(P, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) deploys `BobRouter` implementing `IMetricOmmSwapCallback`.
4. `BobRouter` calls `P.addLiquidity(alice, 0, deltas, callbackData, "")`.
5. The extension evaluates `allowedDepositor[P][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` records the position under key `(alice, 0)`.
7. The pool calls `BobRouter.metricOmmSwapCallback(...)`, which transfers the required tokens.
8. Bob has deposited into the restricted pool; the allowlist check was never applied to Bob. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
