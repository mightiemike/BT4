Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing non-allowlisted callers to bypass deposit gating — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositor, `msg.sender` at the pool level) and gates on `owner` instead. Because `owner` is a free caller-supplied parameter in `pool.addLiquidity` with no constraint linking it to `msg.sender`, any non-allowlisted address can pass the check by nominating any allowlisted address as `owner`.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-chosen `owner` parameter and passes `msg.sender` (actual depositor) and `owner` (position recipient) to the extension hook: [1](#0-0) 

The extension hook receives `sender` as its first argument but the function signature discards it (unnamed `address`), then checks only `owner`: [2](#0-1) 

When the extension is called, `msg.sender` is the pool, so the check resolves to `allowedDepositor[pool][owner]`. Because `owner` is freely chosen by the caller, a non-allowlisted attacker sets `owner = allowlistedAddr` and the check passes. The pool imposes no constraint linking `msg.sender` to `owner` in `addLiquidity` — contrast with `removeLiquidity`, which enforces `msg.sender == owner`: [3](#0-2) 

The periphery router `MetricOmmPoolLiquidityAdder` may call `_validateOwner`, but the pool itself is a public entrypoint — the attacker bypasses the router entirely and calls the pool directly.

## Impact Explanation

The deposit allowlist is the primary curation mechanism for restricting which parties may provide liquidity to a pool. With this bug, any address can add liquidity to a curated pool by specifying any allowlisted address as `owner`. The attacker pays the tokens via callback; the position is credited to the allowlisted address. This unconditionally breaks the pool's curation invariant, allows unauthorized parties to alter pool liquidity composition (affecting LP share dilution and pool depth), and the allowlisted address receives an unsolicited position they can immediately withdraw. This constitutes broken core pool functionality with direct fund-flow impact into a restricted pool.

## Likelihood Explanation

Exploitability is trivial. The attacker needs only to read one `AllowedToDepositSet` event to obtain a valid `owner` address, then call `pool.addLiquidity` directly. No privileged access, no special setup, and no non-standard token behavior is required. The attack is unconditionally repeatable.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor) rather than `owner` (the position recipient):

```solidity
// fixed
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

## Proof of Concept

```solidity
function test_allowlistBypass() public {
    address allowlisted = makeAddr("allowlisted");
    address attacker    = makeAddr("attacker");

    // Pool admin allowlists only `allowlisted`
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

    // Fund attacker and approve pool callback
    token0.mint(attacker, 1_000 ether);
    vm.prank(attacker);
    token0.approve(address(attackerCallback), type(uint256).max);

    // Attacker calls pool directly with owner = allowlisted
    vm.prank(attacker);
    // Should revert (attacker not allowlisted) but SUCCEEDS due to the bug
    pool.addLiquidity(
        allowlisted,          // owner = allowlisted → passes the check
        uint80(1),
        deltas,
        abi.encode(attacker), // callback pulls tokens from attacker
        ""
    );

    // Position exists under allowlisted; attacker paid tokens
    uint256 shares = stateView.positionBinShares(address(pool), allowlisted, 1, int8(4));
    assertGt(shares, 0, "Bypass succeeded");
}
```

The call succeeds because `beforeAddLiquidity` checks `allowedDepositor[pool][allowlisted]` (true) while the actual depositor `attacker` is never checked.

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
