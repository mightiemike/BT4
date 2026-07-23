Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first parameter (`sender`, the actual token-providing caller) and enforces the allowlist only against `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no restriction that `msg.sender == owner`, any unprivileged address can bypass the allowlist by passing an already-allowlisted address as `owner`, depositing tokens into a restricted pool without authorization.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument and the caller-supplied `owner` as the second argument to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards the first parameter entirely (unnamed `address`) and evaluates the guard only against `owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`: [4](#0-3) 

There is no guard in `addLiquidity` requiring `msg.sender == owner`: [5](#0-4) 

(Compare `removeLiquidity` at L206, which does enforce `msg.sender == owner`.)

The exploit path is: attacker calls `pool.addLiquidity(owner=allowlistedAddress, ...)` → extension sees `owner=allowlistedAddress` → `allowedDepositor[pool][allowlistedAddress] == true` → check passes → attacker pays tokens in the swap callback → position is minted to the allowlisted address. The allowlisted address can then call `removeLiquidity` to withdraw the attacker's tokens.

## Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting liquidity provision to permissioned pools (KYC pools, private LP pools, etc.). The allowlist guard is completely ineffective: any address can deposit into a restricted pool, violating regulatory compliance invariants, LP identity requirements, and fee-tier eligibility. The pool admin cannot fix this via `setAllowedToDeposit` because the root cause is which parameter the extension reads, not the allowlist data. This constitutes broken core pool functionality (access-control extension does not function as specified) and an admin-boundary break (unprivileged path bypasses the factory/pool-admin-configured allowlist).

## Likelihood Explanation

The attack requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can exploit it in a single transaction by calling `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`. The allowlisted addresses are publicly readable on-chain via `allowedDepositor`. The attack is repeatable indefinitely.

## Recommendation

Change `beforeAddLiquidity` to check the first parameter (`sender`) — the actual caller who provides tokens — instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`.

## Proof of Concept

```
Setup:
  - Pool P is deployed with DepositAllowlistExtension E.
  - Pool admin calls E.setAllowedToDeposit(P, alice, true).
  - bob is NOT on the allowlist.

Attack:
  1. bob calls P.addLiquidity(
         owner         = alice,   // allowlisted address
         salt          = 0,
         deltas        = <desired bins/shares>,
         callbackData  = ...,     // bob pays tokens in the metricOmmSwapCallback
         extensionData = ""
     )
  2. Pool calls E.beforeAddLiquidity(bob /*discarded*/, alice, ...).
     Extension checks allowedDepositor[P][alice] == true → passes.
  3. Pool calls bob's metricOmmSwapCallback; bob transfers tokens to the pool.
  4. Position is minted for alice.
  5. alice calls P.removeLiquidity(alice, 0, deltas, "") and withdraws bob's tokens.

Result: bob deposited into a restricted pool; the allowlist guard was never triggered.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
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
