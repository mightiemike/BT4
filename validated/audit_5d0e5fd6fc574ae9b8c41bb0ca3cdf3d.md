Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller) and instead checks whether the caller-supplied `owner` argument (the position recipient) is allowlisted. Because `addLiquidity` accepts an arbitrary `owner` with no restriction on who may supply it, any non-allowlisted address can bypass the gate by passing an allowlisted address as `owner`.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly encodes both values into the hook call: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but names only the second parameter, silently discarding `sender`: [3](#0-2) 

The guard at line 38 is `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool (correct key) and `owner` is the position recipient — not the actual depositing address. An attacker calls `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)`, the extension checks `allowedDepositor[pool][allowlisted_address]` which is `true`, and the deposit proceeds. The attacker supplies the tokens via the liquidity callback; the position accrues to the nominated `owner`, but the pool's access restriction is fully bypassed.

## Impact Explanation
The deposit allowlist is completely defeated. Any address — including one that was explicitly denied — can deposit into a pool protected by this extension by nominating any allowlisted address as the position `owner`. Pools relying on this extension for KYC/compliance or whitelist-only liquidity provision have no effective gate on who can deposit. This constitutes broken core pool functionality (the allowlist hook) causing the pool's intended access control to be entirely non-functional, meeting the "Broken core pool functionality" impact criterion.

## Likelihood Explanation
The attack requires no privileged access, no special token behavior, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity` with an allowlisted `owner`. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping or from emitted `AllowedToDepositSet` events. The attack is trivially repeatable.

## Recommendation
Replace the `owner` check with the `sender` argument:

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

Also update `isAllowedToDeposit`, `setAllowedToDeposit`, and the storage mapping name to reflect that the gated entity is the depositing caller, not the position owner.

## Proof of Concept
```solidity
// Foundry test sketch
function test_nonAllowlistedOperatorBypassesGate() public {
    address allowlistedOwner = makeAddr("allowlistedOwner");
    address attacker         = makeAddr("attacker");

    // Only allowlistedOwner is permitted
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), allowlistedOwner, true);

    // Attacker is NOT allowlisted
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Attacker calls addLiquidity with owner = allowlistedOwner
    // Extension checks allowedDepositor[pool][allowlistedOwner] → true → no revert
    vm.prank(attacker);
    pool.addLiquidity(allowlistedOwner, salt, deltas, callbackData, extensionData);
    // Deposit succeeds; attacker bypassed the allowlist
}
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
