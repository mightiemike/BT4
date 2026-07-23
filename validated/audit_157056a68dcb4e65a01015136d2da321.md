Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of `sender`, Fully Bypassing the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual transaction initiator) and gates on `owner` instead — a free argument supplied by the caller to `addLiquidity`. Because `owner` is attacker-controlled, any unprivileged address can bypass the allowlist by passing any already-allowlisted address as `owner`, breaking the pool admin's access-control invariant and allowing unauthorized bin-state modification and LP dilution.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as a separate argument to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both distinct addresses to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then leaves the first parameter (`sender`) unnamed and checks `owner` instead:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The inconsistency is confirmed by `SwapAllowlistExtension.beforeSwap`, which correctly names and checks `sender`:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }
}
``` [4](#0-3) 

**Exploit path:**
1. Pool is configured with `DepositAllowlistExtension`; `allowedDepositor[pool][alice] = true`; `allowAllDepositors[pool] = false`.
2. Bob (not allowlisted) calls `pool.addLiquidity(owner=alice, salt=0, deltas=..., ...)`.
3. The pool calls `_beforeAddLiquidity(msg.sender=bob, owner=alice, ...)`.
4. The extension receives `(sender=bob, owner=alice)`, ignores `bob`, and checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes with `owner=alice`, minting shares into alice's position key, with bob paying tokens via the liquidity callback.
6. Bob has successfully added liquidity to a restricted pool without being allowlisted. [5](#0-4) 

## Impact Explanation

The pool admin's configured access control — that only approved depositors may provide liquidity — is completely broken. Any unprivileged address can add liquidity to a restricted pool. Concrete consequences include:

- **Unauthorized bin-state modification**: `curPosInBin`, `binTotals`, and per-bin balances are altered by an unapproved party, shifting effective execution prices for subsequent swaps.
- **Dilution of existing LP positions**: New shares are minted in targeted bins; existing LPs' proportional claim on bin fees and residual value decreases measurably.
- **Position-key griefing**: The attacker can occupy `(alice, salt)` position keys, preventing the legitimate allowlisted owner from using those keys or forcing unexpected position merges.
- **Admin-boundary break**: The pool admin's allowlist configuration is rendered entirely ineffective by an unprivileged caller — a direct bypass of the admin-configured access control gate.

## Likelihood Explanation

- Requires only a single call to `pool.addLiquidity` with `owner` set to any address in the allowlist.
- No flash loan, oracle manipulation, or privileged access is needed.
- `allowedDepositor` is a public mapping, so a valid `owner` value is trivially discoverable on-chain.
- Every pool deploying `DepositAllowlistExtension` with `allowAllDepositors = false` is affected.
- The attack is repeatable at will with no cooldown or state dependency.

## Recommendation

Name and check `sender` (the actual caller) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowAllDepositors[pool] = false
  bob is NOT allowlisted

Attack (single transaction, no special privileges):
  bob calls pool.addLiquidity(
      owner        = alice,        // allowlisted — passes the guard
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [largeAmount] },
      callbackData = "",
      extensionData = ""
  )

Trace:
  MetricOmmPool.addLiquidity:
    _beforeAddLiquidity(msg.sender=bob, owner=alice, ...)
      → DepositAllowlistExtension.beforeAddLiquidity(bob_unnamed, alice, ...)
        checks allowedDepositor[pool][alice] → true → no revert
    LiquidityLib.addLiquidity(..., owner=alice, ...)
      → position (alice, 0) created; bob pays tokens via callback
      → binTotals, _binStates, _positionBinShares updated

Result:
  - Extension check passed for non-allowlisted bob
  - Unauthorized liquidity added to restricted pool
  - Bin 0 state modified by unapproved party
  - Existing LPs in bin 0 diluted
  - alice's position key occupied without her consent
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
