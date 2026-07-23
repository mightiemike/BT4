Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks `owner`, a free caller-supplied argument designating the LP-share recipient. Because any caller can supply an already-authorized address as `owner`, the allowlist check is trivially bypassed by any unprivileged address. The pool admin's primary access-control mechanism for restricting liquidity providers is rendered ineffective.

## Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

- `msg.sender` → forwarded as `sender` (the address paying tokens via the swap callback)
- caller-supplied `owner` → forwarded as `owner` (the LP-share recipient) [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (`sender`) as unnamed, discarding it entirely, and checks only `owner`: [2](#0-1) 

The `allowedDepositor` mapping is keyed by depositor address and is intended to gate who may add liquidity: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller) and discards the recipient: [4](#0-3) 

The inconsistency is the root cause. Since `owner` is a free argument with no on-chain binding to the actual token payer, any address can pass the check by naming an authorized address as `owner`. No existing guard prevents this: `addLiquidity` has no restriction on who can be named as `owner`, and the extension hook is the sole access-control point.

## Impact Explanation

The deposit allowlist is the pool admin's mechanism for restricting who may add liquidity (regulatory compliance, curated LP sets, controlled bootstrapping). With this bug, any unprivileged address can deposit into a restricted pool by naming an authorized address as `owner`. The attacker's tokens enter the pool via the swap callback; LP shares are minted to `owner`. The attacker cannot reclaim shares (`removeLiquidity` enforces `msg.sender == owner`), but unauthorized capital permanently enters the pool, distorting bin balances and violating the admin's access-control boundary. This matches the "Admin-boundary break" impact criterion: an unprivileged path bypasses a pool admin access-control check. [5](#0-4) 

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known authorized address as `owner`. The `allowedDepositor` mapping is public, so any observer can identify authorized addresses. No special privileges, flash loans, or oracle manipulation are needed. The attack is repeatable at will by any address. [3](#0-2) 

## Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring the pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the depositor and the LP recipient, both should be checked independently.

## Proof of Concept

```
Setup:
  Pool P has DepositAllowlistExtension configured
  allowedDepositor[P][AUTHORIZED] = true
  allowedDepositor[P][ATTACKER]   = false (not set)

Attack:
  ATTACKER calls pool.addLiquidity(
      owner        = AUTHORIZED,   // passes the allowlist check
      salt         = 0,
      deltas       = <valid delta>,
      callbackData = ...,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  msg.sender = P (the pool)
  owner      = AUTHORIZED
  allowedDepositor[P][AUTHORIZED] == true  → check passes

Result:
  - ATTACKER's tokens are pulled via metricOmmSwapCallback
  - LP shares are minted to AUTHORIZED
  - ATTACKER has deposited into a pool that explicitly excluded them
  - The allowlist invariant is violated
``` [2](#0-1) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
