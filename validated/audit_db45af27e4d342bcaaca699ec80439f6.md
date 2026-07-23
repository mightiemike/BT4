Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` and gates only `owner`, allowing any unprivileged caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` (the actual token payer / `msg.sender` of the pool call) as its first argument but declares it as an anonymous `address` placeholder, discarding it entirely. The allowlist check is applied only to `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any address not on the allowlist can deposit into a guarded pool by supplying any allowlisted address as `owner`, fully bypassing the admin-configured access boundary.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position beneficiary to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly encodes both `sender` and `owner` and forwards them to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but names it with an anonymous `address` placeholder, discarding it entirely. The guard is applied only to `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), not the recipient:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The operator pattern is explicitly documented in the interface NatSpec: *"`msg.sender` pays but need not equal `owner` (operator pattern)."* [5](#0-4) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, salt, ...)` makes the exploit trivially accessible from the standard periphery: the caller supplies an arbitrary `owner` while `msg.sender` (the payer) is stored separately in transient context and never reaches the extension check:

```solidity
_validateOwner(owner);
_validateDeltas(deltas);
return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
``` [6](#0-5) 

The exact wrong value is the extension decision: `allowedDepositor[pool][owner]` is evaluated when `allowedDepositor[pool][sender]` is the correct predicate. The `sender` address — the actual token payer — is never checked.

## Impact Explanation

The deposit allowlist is the only on-chain mechanism pool admins have to restrict who may provide liquidity (e.g., for regulatory compliance, KYC gating, or competitive exclusion). Because the guard checks `owner` rather than `sender`, it is completely ineffective against the operator pattern. An address not on the allowlist can call `pool.addLiquidity(owner=allowlistedAddress, ...)` directly or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares`. The extension sees `owner = allowlistedAddress` → check passes. The unauthorized caller pays the tokens; the position is credited to the allowlisted address. With collusion, the allowlisted address calls `removeLiquidity` and returns the proceeds — a complete, costless bypass of the guard. Even without collusion, the unauthorized caller has successfully deposited into a pool that was configured to reject them. This is an admin-boundary break: an unprivileged path bypasses a factory/pool admin-configured guard.

## Likelihood Explanation

Triggering requires no special privilege — any EOA or contract can call `pool.addLiquidity` or `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an arbitrary `owner`. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping or observable from past deposit events. The `MetricOmmPoolLiquidityAdder` periphery contract is the standard user-facing entry point and directly exposes the `owner` parameter, making the bypass path obvious and repeatable.

## Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual depositor / token payer) instead of `owner`, mirroring the already-correct pattern in `SwapAllowlistExtension.beforeSwap`:

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

If the intent is to gate both the payer and the beneficiary, both `sender` and `owner` should be checked.

## Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as beforeAddLiquidity hook.
  - Admin allowlists address A; address B is NOT allowlisted.

Attack:
  1. B calls pool.addLiquidity(
         owner = A,          // allowlisted → check passes
         salt  = 1,
         deltas = <valid bins>,
         callbackData = ...,
         extensionData = ""
     )
  2. Extension receives: sender = B (anonymous, ignored), owner = A (allowlisted) → no revert.
  3. Pool mints shares under key (A, 1, bin).
  4. B's tokens are pulled via callback.
  5. A calls removeLiquidity(owner=A, salt=1, ...) and receives the tokens back.

Result: B deposited into a pool that was configured to reject B.
        The deposit allowlist guard is fully bypassed.

Same path available via:
  MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, A, 1, deltas, ...)
  called by B — the standard periphery entry point.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-40)
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L65-67)
```text
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```
