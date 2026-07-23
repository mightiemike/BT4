Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and gates access on `owner` (the caller-supplied position owner). Because `MetricOmmPool.addLiquidity` permits any caller to specify an arbitrary `owner`, any address not on the allowlist can bypass the restriction by naming any allowlisted address as the position owner.

## Finding Description
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any caller and forwards `msg.sender` as `sender` to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` passes both values verbatim to every registered extension:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` drops the first argument (`sender`) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The admin-facing setter names its parameter `depositor`, confirming the intent is to restrict callers, not position owners:

```solidity
// DepositAllowlistExtension.sol L18-20
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

`SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), making the discrepancy clearly unintentional:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The existing unit tests in `DepositAllowlistSubExtension.t.sol` pass `address(0)` as the `sender` argument and `depositor` as `owner`, confirming the tests themselves validate the wrong argument and do not catch this bug.

## Impact Explanation
The deposit allowlist is completely ineffective. Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, "")`, pass the `owner` check, and add liquidity to the pool. The unauthorized caller pays the tokens via the modify-liquidity callback; the allowlisted address receives the LP shares. Any pool admin access control enforced via this extension — KYC restrictions, regulatory constraints, manipulation prevention — is silently bypassed by any unprivileged actor who knows any allowlisted address (all discoverable on-chain from `AllowedToDepositSet` events). This constitutes broken core pool functionality causing loss of intended access control over LP deposits.

## Likelihood Explanation
Exploitation requires no special privilege. Any external account can call `addLiquidity` with an arbitrary `owner`. Allowlisted addresses are discoverable from emitted `AllowedToDepositSet` events. The bypass is unconditional whenever the pool has a non-zero `BEFORE_ADD_LIQUIDITY_ORDER` pointing to this extension. The `MetricOmmPoolLiquidityAdder` periphery contract also exposes `addLiquidityExactShares(pool, owner, ...)` which allows specifying an arbitrary `owner`, making the bypass trivially reachable through the standard router.

## Recommendation
Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

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
1. Deploy pool with `DepositAllowlistExtension` registered in `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is permitted.
3. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → hook returns success.
6. Bob's tokens are transferred into the pool via callback; Alice's position receives the LP shares.
7. Bob has successfully deposited despite being absent from the allowlist.

Foundry test skeleton:
```solidity
function test_bypassAllowlist() public {
    // alice is allowlisted, bob is not
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), alice, true);

    // bob calls beforeAddLiquidity with alice as owner — should revert but does not
    vm.prank(address(pool));
    LiquidityDelta memory delta = LiquidityDelta({...});
    // passes: checks allowedDepositor[pool][alice] == true, ignores bob
    extension.beforeAddLiquidity(bob, alice, 0, delta, "");
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L31-31)
```text
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
```
