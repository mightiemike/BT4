Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Enabling Full Deposit Allowlist Bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and checks `owner` instead. Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no `msg.sender == owner` constraint, any address can bypass the deposit allowlist by naming any allowlisted address as `owner`. The allowlist — the sole access-control mechanism for deposits — is rendered completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-controlled `owner` parameter as `owner` into the extension hook: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, …)` but names the first argument `_` (unnamed, discarded) and checks only `owner`: [2](#0-1) 

The allowlist mapping is keyed on `allowedDepositor[pool][owner]`, not on the actual depositor (`sender`). Because `addLiquidity` imposes **no** `msg.sender == owner` constraint: [3](#0-2) 

This is directly inconsistent with `removeLiquidity`, which enforces `msg.sender == owner`: [4](#0-3) 

And inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly names and checks `sender`: [5](#0-4) 

The extension's own NatSpec states its purpose as **"Gates `addLiquidity` by depositor address"** and the mapping is named `allowedDepositor`, both indicating the intent is to gate on the token-providing caller (`sender`), not the LP-position recipient (`owner`): [6](#0-5) 

## Impact Explanation
Any address not on the allowlist can deposit to a restricted pool by calling `pool.addLiquidity(owner = <any allowlisted address>, ...)`. The hook checks `allowedDepositor[pool][allowlistedAddress]` → `true`, so the guard passes. The unlisted caller provides tokens via the `IMetricOmmAddLiquidityCallback` callback and the LP position is credited to the named `owner`. This is a broken admin-boundary: the pool admin's configured deposit guard is bypassed by an unprivileged path with no special role or privilege required. Pools configured for KYC/compliance gating, institutional-only liquidity, or any other depositor restriction are fully open to any address.

## Likelihood Explanation
Exploitation requires only a standard `addLiquidity` call with a known allowlisted address as `owner`. No flash loan, oracle manipulation, or privileged role is needed. Any observer of on-chain allowlist state (via the public `allowedDepositor` mapping) can identify a valid `owner` and execute the bypass immediately. The attack is repeatable with zero friction.

## Recommendation
Name and check `sender` (the actual depositor) instead of `owner`, mirroring `SwapAllowlistExtension`:

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
**Setup:**
- Pool `P` has `DepositAllowlistExtension` configured with `BEFORE_ADD_LIQUIDITY_ORDER` pointing to it.
- Pool admin calls `setAllowedToDeposit(P, Alice, true)`. Only Alice is allowlisted.
- Bob is **not** allowlisted.

**Attack:**
```solidity
contract BobAttacker is IMetricOmmAddLiquidityCallback {
    function attack(address pool, address alice) external {
        LiquidityDelta memory deltas = /* ... */;
        pool.addLiquidity(
            alice,  // owner: allowlisted → guard passes
            0,
            deltas,
            "",
            ""
        );
    }

    function metricOmmAddLiquidityCallback(uint256 amount0, uint256 amount1, bytes calldata) external {
        // Bob provides his own tokens
        IERC20(token0).transfer(msg.sender, amount0);
        IERC20(token1).transfer(msg.sender, amount1);
    }
}
```

**Result:**
1. `_beforeAddLiquidity(sender=Bob, owner=Alice, …)` fires.
2. `DepositAllowlistExtension` checks `allowedDepositor[pool][Alice]` → `true`. Guard passes.
3. Bob's callback provides tokens; Alice receives the LP position.
4. Bob has deposited to a pool he is explicitly barred from — the allowlist is bypassed entirely.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

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
