Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of Actual Depositor, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is intended to gate `addLiquidity` by the actual depositor address. However, its `beforeAddLiquidity` hook discards the first parameter (the real `msg.sender` of `addLiquidity`) and checks only the caller-supplied `owner` argument against the allowlist. Because `owner` is freely chosen by the caller, any unprivileged address can bypass the allowlist by supplying any already-allowed address as `owner`, rendering the access control completely inoperative.

## Finding Description
`MetricOmmPool.addLiquidity` passes the actual caller as the first argument and the caller-supplied LP-position recipient as the second: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully to each configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently discards the first parameter (the real depositor) and checks only `owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks the first parameter (`sender`) and ignores the second (`recipient`): [4](#0-3) 

The asymmetry is unambiguous: the deposit guard is bound to the wrong identity. An attacker calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` where `alice` is any known-allowed address. The hook evaluates `allowedDepositor[pool][alice] == true` and does not revert, even though the actual depositor (`attacker`) is not on the allowlist.

## Impact Explanation
The allowlist is completely inoperative. Any unprivileged address can add liquidity to a pool the admin intended to restrict. This constitutes an admin-boundary break: the pool admin's invariant ("only approved depositors may add liquidity") is silently violated on every call from an unauthorized depositor. Additionally, LP shares are minted to `owner` (an allowed party who provided no tokens) while the actual token provider holds no position and cannot recover funds — a structural locked-fund outcome for the attacker's own capital.

## Likelihood Explanation
Exploitation requires only a single `addLiquidity` call with `owner` set to any known-allowed address (the pool admin is always a candidate, as they are typically allowlisted). No special permissions, flash loans, or oracle manipulation are needed. The bypass is deterministic and repeatable by any address.

## Recommendation
Check the actual depositor (first parameter `sender`) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

## Proof of Concept
1. Deploy pool with `DepositAllowlistExtension`; set `allowedDepositor[pool][alice] = true`, `allowedDepositor[pool][attacker] = false`.
2. `attacker` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool fires `_beforeAddLiquidity(attacker /*sender*/, alice /*owner*/, ...)`.
4. Extension evaluates `allowedDepositor[pool][alice] == true` → no revert.
5. `LiquidityLib.addLiquidity` executes the callback against `attacker` (the actual `msg.sender`) to pull tokens; `attacker` pays.
6. LP shares are minted to `alice`; `attacker` has deposited into a restricted pool without being on the allowlist, and `attacker`'s tokens are locked under `alice`'s position with no recovery path.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
