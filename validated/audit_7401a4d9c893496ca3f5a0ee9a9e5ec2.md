All the code is confirmed. The vulnerability is real and exactly as described. Let me verify the `addLiquidity` call site quickly.

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any actor to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address (the actual caller). However, its `beforeAddLiquidity` hook silently discards the `sender` argument and checks `owner` (the position owner) instead. Any unprivileged actor can bypass the allowlist by calling `addLiquidity` with `owner` set to any allowlisted address, fully defeating the access control the extension is designed to enforce.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the hook at:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both arguments to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, ...))`: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and discarded; the allowlist lookup is performed against `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The admin-facing setter and view helper both use the word **depositor**, confirming the intended subject is the caller, not the position owner: [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` applies the correct pattern — it names and checks `sender`: [5](#0-4) 

The deposit extension applies the same structural pattern to the wrong field.

## Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools. With this bug, any actor — regardless of allowlist status — can add liquidity to a pool by supplying `owner = <any allowlisted address>`. The extension passes because it checks the allowlisted owner, not the unauthorized caller. The unauthorized caller pays the tokens; the allowlisted address receives the LP position. The pool admin's intended access boundary is fully broken: the guard can be bypassed by any unprivileged actor with zero special privilege. Secondary consequences include unauthorized alteration of bin composition and pool depth, and an allowlisted address being forced into holding an LP position it never requested.

## Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any caller can set `owner` to any address known to be allowlisted (e.g., the pool admin itself, which is publicly readable via `IMetricOmmPoolFactory.poolAdmin`). The attack is trivially executable on every pool that deploys `DepositAllowlistExtension` without `allowAllDepositors` set.

## Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension.beforeSwap`:

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       alice,   // owner = allowlisted address
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `MetricOmmPool` calls `_beforeAddLiquidity(bob /*msg.sender*/, alice /*owner*/, ...)`.
5. `DepositAllowlistExtension.beforeAddLiquidity` receives `sender = bob` (discarded) and `owner = alice`.
6. Check: `allowedDepositor[pool][alice]` → `true` → hook returns success selector.
7. Bob's tokens are pulled via callback; Alice receives the LP position.
8. The deposit allowlist is fully bypassed by an unprivileged actor.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
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
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```
