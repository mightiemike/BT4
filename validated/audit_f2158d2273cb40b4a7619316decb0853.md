Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Enabling Allowlist Bypass and Legitimate Depositor DoS — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token-providing caller) and instead checks `owner` (the LP position recipient). Any unprivileged address can bypass the deposit allowlist by naming an allowlisted address as `owner`, while a legitimately allowlisted depositor is blocked whenever their intended `owner` is not on the allowlist. The parallel `SwapAllowlistExtension.beforeSwap` correctly checks `sender`, confirming the inversion is a defect.

## Finding Description
In `MetricOmmPool.addLiquidity` (L191), the pool calls:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual depositor who will be called back to transfer tokens; `owner` is only the LP position recipient. `ExtensionCalling._beforeAddLiquidity` (L88–99) faithfully forwards both as `(sender, owner, ...)` via `abi.encodeCall`.

`DepositAllowlistExtension.beforeAddLiquidity` (L32–42) receives both but discards `sender` (unnamed `address,`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The admin setter `setAllowedToDeposit(pool_, depositor, allowed)` (L18–20) stores entries under `allowedDepositor[pool_][depositor]`, confirming the intended entity is the depositor/caller, not the owner. `SwapAllowlistExtension.beforeSwap` (L31–41) correctly checks `sender` and discards the second address, proving the intended pattern.

**Bypass path:** `bob` (not allowlisted) calls `pool.addLiquidity(owner=alice, ...)` where `alice` is allowlisted. The hook sees `owner=alice`, the check passes, bob's tokens are pulled via callback, and alice receives LP shares she never requested. The allowlist is fully bypassed with zero privileges.

**DoS path:** `alice` (allowlisted) calls `pool.addLiquidity(owner=vault, ...)` where `vault` is not allowlisted. The hook checks `owner=vault`, reverts `NotAllowedToDeposit`, blocking alice despite her authorization.

## Impact Explanation
For pools configured with this extension as a restricted/private deposit gate (its primary use-case), the bypass allows any arbitrary address to inject liquidity, diluting existing LP fee shares and altering bin distributions without pool admin consent. This constitutes an admin-boundary break: an unprivileged path circumvents the pool admin's access-control boundary. The DoS path additionally breaks core liquidity-management flows for legitimately allowlisted depositors who direct positions to any non-allowlisted recipient (e.g., a vault or multisig).

## Likelihood Explanation
The bypass requires no special privileges, no flash loans, and no oracle manipulation. Any EOA or contract can call `addLiquidity` on a pool with this extension active, pass any allowlisted address as `owner`, and the guard passes unconditionally. The allowlisted address need not cooperate. The DoS path is equally trivial and affects any allowlisted depositor using a non-allowlisted recipient address.

## Recommendation
Replace the unnamed `address,` with `sender` and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

```solidity
// Fixed:
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
  Pool P configured with DepositAllowlistExtension E.
  Pool admin calls E.setAllowedToDeposit(P, alice, true).
  bob is NOT on the allowlist.

Attack (bypass):
  bob calls P.addLiquidity(
      owner        = alice,   // allowlisted — passes the (wrong) check
      salt         = 0,
      deltas       = <valid deltas>,
      callbackData = <bob's callback transferring bob's tokens>,
      extensionData = ""
  );
  Result: E.beforeAddLiquidity(bob /*ignored*/, alice /*checked*/) → passes.
  bob's tokens enter the pool; alice receives LP shares she never requested.

Attack (DoS):
  alice calls P.addLiquidity(owner = vault, ...) where vault ∉ allowlist.
  Result: E.beforeAddLiquidity(alice /*ignored*/, vault /*checked*/) → reverts NotAllowedToDeposit.
  alice cannot deposit to her own vault despite being allowlisted.

Foundry test outline:
  1. Deploy pool with DepositAllowlistExtension.
  2. setAllowedToDeposit(pool, alice, true).
  3. Call addLiquidity from bob with owner=alice; assert it succeeds (demonstrates bypass).
  4. Call addLiquidity from alice with owner=vault (not allowlisted); assert it reverts (demonstrates DoS).
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
