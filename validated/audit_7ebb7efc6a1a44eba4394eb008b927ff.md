Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (the actual caller, `msg.sender` of `addLiquidity`) and validates only the `owner` parameter against the allowlist. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unpermissioned address can bypass the deposit guard by calling `addLiquidity` with `owner` set to any allowlisted address, providing tokens via the callback, and having the position credited to that allowlisted address.

## Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook at line 191:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is the real external caller (`msg.sender`); the second is the `owner` of the position being created. `addLiquidity` has no `msg.sender == owner` guard — only `removeLiquidity` enforces that constraint: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives the two addresses in the same order but discards the first entirely (it is unnamed), then checks `owner` against the allowlist:

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

The NatSpec and admin setter name the gated entity "depositor", and the mapping key is `depositor`: [4](#0-3) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly names and checks the first parameter as `sender`: [5](#0-4) 

The asymmetry confirms the deposit extension checks the wrong parameter. An attacker who knows any allowlisted address can call `addLiquidity(owner = allowlisted_address, ...)`, supply tokens via the callback, and the guard passes because `allowedDepositor[pool][allowlisted_address] == true`.

## Impact Explanation

**High.** The allowlist guard is completely ineffective against any external caller. Concrete impacts:

1. **Allowlist bypass / unauthorized pool participation.** A pool configured for KYC-only LPs can be deposited into by any address. The attacker provides tokens via the `addLiquidity` callback; the position is credited to the allowlisted `owner`. The attacker has injected liquidity into a private pool without authorization.

2. **Bin-balance manipulation.** Unauthorized deposits shift `token0BalanceScaled`/`token1BalanceScaled` and `binTotals`, altering the effective price curve and LP share values for all existing LPs.

3. **Griefing allowlisted LPs.** An attacker can force unwanted liquidity positions onto an allowlisted address (locking tokens in bins the victim did not choose). The victim holds the shares but cannot remove liquidity they did not initiate (they would need to call `removeLiquidity` themselves, but the position was created without their consent).

## Likelihood Explanation

**High.** Preconditions are minimal: the pool must use `DepositAllowlistExtension`, and at least one allowlisted address must exist (required for the pool to be usable). The attacker needs only to know any allowlisted address — no privileged access, flash loan, or oracle manipulation required. The call is a standard `addLiquidity` with a chosen `owner` argument.

## Recommendation

Rename the first parameter and check `sender` instead of `owner`, matching the intent stated in the NatSpec and the pattern used by `SwapAllowlistExtension`:

```diff
-function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
     external view override returns (bytes4)
 {
-    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
         revert IMetricOmmPoolActions.NotAllowedToDeposit();
     }
     return IMetricOmmExtensions.beforeAddLiquidity.selector;
 }
```

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Bob] = true
  Alice is NOT allowlisted

Attack:
  Alice calls pool.addLiquidity(
      owner        = Bob,       // allowlisted → check passes
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1e18] },
      callbackData = ...,       // Alice transfers tokens in callback
      extensionData = ""
  )

Execution:
  MetricOmmPool.addLiquidity calls _beforeAddLiquidity(msg.sender=Alice, owner=Bob, ...)
  DepositAllowlistExtension.beforeAddLiquidity receives (Alice, Bob, ...)
  First param (Alice) is discarded; check is allowedDepositor[pool][Bob] == true → no revert
  Alice's callback transfers tokens to the pool
  Bob's position is credited with 1e18 shares in bin 0

Result:
  Alice has deposited into a permissioned pool she is not authorized to access.
  Bob holds an unwanted position he did not initiate.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-21)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
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
