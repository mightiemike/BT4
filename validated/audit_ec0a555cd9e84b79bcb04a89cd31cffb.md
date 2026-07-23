Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Callers to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and validates only `owner` (the LP position recipient). Any address not on the allowlist can bypass the guard by naming any allowlisted address as `owner`, depositing tokens into a nominally restricted pool without restriction.

## Finding Description
`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding the real caller as the first argument and the position recipient as the second. [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives these as `(address sender, address owner, ...)` per the interface, but names the first parameter `address` (unnamed, discarded) and binds only `owner`: [2](#0-1) 

The check `allowedDepositor[msg.sender][owner]` therefore gates on the position recipient, not the token provider. The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [3](#0-2) 

**Exploit path:**
1. Pool `P` has `DepositAllowlistExtension` configured; `allowedDepositor[P][Alice] = true`, `allowedDepositor[P][Bob] = false`.
2. Bob calls `P.addLiquidity(owner = Alice, salt, deltas, callbackData, "")`.
3. Pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
4. Extension evaluates `allowedDepositor[P][Alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; pool calls Bob's `metricOmmAddLiquidityCallback`; Bob transfers tokens.
6. Alice's position is credited. Bob has deposited into a pool he is explicitly barred from.

No existing guard prevents this: `removeLiquidity` enforces `msg.sender == owner` (line 206), but that only protects withdrawal, not deposit. [4](#0-3) 

## Impact Explanation
The deposit allowlist — an admin-configured security boundary — is fully bypassed by any unprivileged EOA or contract. Concrete consequences: (1) any caller participates in a nominally restricted pool by naming any allowlisted address as `owner`; (2) the named `owner` receives an unsolicited position and must spend gas to remove it; (3) colluding parties gain full LP economics in a pool they are explicitly barred from; (4) bin balance and price/fee distribution can be manipulated by non-allowlisted actors. This satisfies the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gate.

## Likelihood Explanation
No special privilege is required — any EOA or contract can call `addLiquidity` directly. Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events. The attacker bears the token cost of the deposit (which goes to the named owner), making griefing variants low-cost if the owner cooperates. Likelihood: **Medium**.

## Recommendation
Replace the `owner` check with a `sender` check, consistent with `SwapAllowlistExtension`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

If owner-gating is intentional (only allowlisted addresses may hold positions), a separate `sender` check must also be added to prevent unauthorized token injection.

## Proof of Concept

```
Setup:
  Pool P has DepositAllowlistExtension E configured
  E.allowedDepositor[P][Alice] = true
  E.allowedDepositor[P][Bob]   = false  (Bob is blocked)

Attack:
  1. Bob calls P.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData="")
  2. Pool calls E.beforeAddLiquidity(sender=Bob, owner=Alice, ...)
  3. E checks allowedDepositor[P][Alice] → true → no revert
  4. LiquidityLib.addLiquidity executes; pool calls Bob's metricOmmAddLiquidityCallback
  5. Bob transfers tokens to the pool inside the callback
  6. Alice's position is credited with the deposited shares
  7. Bob has successfully deposited into a pool he is explicitly barred from

Foundry test sketch:
  - Deploy pool with DepositAllowlistExtension
  - setAllowedToDeposit(pool, alice, true)
  - vm.prank(bob); pool.addLiquidity(alice, 0, deltas, callbackData, "")
  - Assert: call succeeds (no revert), alice's position shares > 0
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
