Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of `msg.sender`-Derived `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual transaction initiator forwarded as `msg.sender` from `MetricOmmPool.addLiquidity`) and instead gates on `owner`, which is a free argument supplied by the caller. Because any caller can pass an already-allowlisted address as `owner`, the deposit allowlist is completely bypassed by any unprivileged address in a single call.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as two distinct arguments to `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both to the extension hook:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then discards the first argument (the real sender) and checks `owner`:

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
```

Since `owner` is attacker-controlled, any address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`. The extension sees `owner = allowlistedAddress`, finds it in `allowedDepositor`, and permits the deposit. The actual caller is never checked. By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The inconsistency is structural: the deposit extension checks the wrong positional argument.

## Impact Explanation
Any pool deploying `DepositAllowlistExtension` with `allowAllDepositors = false` has its access control completely broken. An unprivileged address can add liquidity to a restricted pool, modifying `binTotals`, `curPosInBin`, and per-bin balances without authorization. This constitutes broken core pool functionality (the allowlist extension's sole purpose is to restrict who may provide liquidity) and causes measurable harm to existing LPs through dilution of bin shares and bin-state manipulation affecting subsequent swap execution prices.

## Likelihood Explanation
The bypass requires only a single call to `pool.addLiquidity` with `owner` set to any allowlisted address. No flash loan, oracle manipulation, or privileged access is needed. The `allowedDepositor` mapping is public, so a valid `owner` value is trivially discoverable on-chain. Every pool using this extension with a non-open allowlist is affected.

## Recommendation
Name and check `sender` (the actual caller) instead of `owner`, mirroring `SwapAllowlistExtension`:

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
  bob is NOT allowlisted

Attack (single transaction, no special privileges):
  bob calls pool.addLiquidity(
      owner         = alice,        // allowlisted — passes the guard
      salt          = 0,
      deltas        = { binIdxs: [0], shares: [largeAmount] },
      callbackData  = "",
      extensionData = ""
  )

Trace:
  MetricOmmPool.addLiquidity:
    _beforeAddLiquidity(msg.sender=bob, owner=alice, ...)
  ExtensionCalling._beforeAddLiquidity:
    encodes (sender=bob, owner=alice, ...)
  DepositAllowlistExtension.beforeAddLiquidity:
    checks allowedDepositor[pool][alice] → true → no revert
  LiquidityLib.addLiquidity executes with owner=alice, bob pays via callback
  Position (alice, 0) created; bin 0 state modified by unauthorized party
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
