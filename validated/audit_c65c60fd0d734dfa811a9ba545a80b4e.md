Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Trader, Allowing Any EOA to Bypass Per-Trader Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of the pool — the router — not the originating EOA trader. When a pool admin allowlists the router address (the only way to enable router-based swaps on an allowlist-gated pool), every EOA that calls through `MetricOmmSimpleRouter` bypasses the per-trader allowlist entirely, silently nullifying the extension's core access-control guarantee.

## Finding Description

The full call chain is confirmed in production code:

**Step 1 — Router calls pool with itself as `msg.sender`:**
`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The pool sees `msg.sender` as the router contract address. [1](#0-0) 

**Step 2 — Pool passes `msg.sender` (router) as `sender` to `_beforeSwap`:**
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. At this point `msg.sender` is the router. [2](#0-1) 

**Step 3 — `_beforeSwap` forwards `sender` (router) to the extension:**
`ExtensionCalling._beforeSwap` encodes `sender` as the first argument to `IMetricOmmExtensions.beforeSwap`. [3](#0-2) 

**Step 4 — Extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`:**
`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool, since the pool calls the extension) as the pool key and `sender` (the router, forwarded from the pool) as the swapper key. The actual EOA trader is never inspected. [4](#0-3) 

The extension's NatSpec states it "Gates `swap` by swapper address, per pool," but the swapper address it actually gates on is the router, not the human trader. [5](#0-4) 

No existing guard compensates for this: the `allowedSwapper` mapping is keyed only on `(pool, sender)` and there is no mechanism to propagate the originating EOA through the call chain. [6](#0-5) 

## Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` and allowlists the router address inadvertently opens the pool to every EOA on the network. Any unauthorized trader calls `exactInputSingle` through the router; the extension sees the allowlisted router address and passes the check; the swap executes and transfers pool token balance to the trader's `recipient`. The allowlist provides zero per-trader protection in the router path. This is broken core pool functionality: the extension's entire purpose — restricting swaps to a curated set of addresses — is silently nullified for all router-mediated swaps, constituting a direct loss of pool token principal to unauthorized parties.

## Likelihood Explanation

Allowlisting the router is the only way to enable router-based swaps on an allowlist-gated pool. Any operator who deploys this extension and wants users to use the standard router will make exactly this configuration. The bypass requires no special privileges, no flash loans, and no unusual token behavior — just calling the public `exactInputSingle` function. The condition is met by every standard deployment of this extension with router support enabled.

## Recommendation

`SwapAllowlistExtension.beforeSwap` must check the originating trader, not the immediate caller. Two options:

1. **Require the router to forward the real trader address in `extensionData`**, and have the extension decode and verify it — with the extension verifying that `msg.sender` (the pool's caller, i.e., the router) is a trusted router before accepting the forwarded identity. This is the cleaner long-term fix: the router encodes `msg.sender` (the EOA) into `extensionData`, and the extension reads it after confirming the pool's caller is a trusted router.
2. **Pass `tx.origin` as the checked address** — simple but incompatible with smart-contract traders.

## Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // trader EOA is NOT in allowedSwapper

    address unauthorizedTrader = makeAddr("badActor");
    token0.mint(unauthorizedTrader, 1_000_000e18);
    vm.prank(unauthorizedTrader);
    token0.approve(address(router), type(uint256).max);

    uint256 poolToken1Before = token1.balanceOf(address(pool));

    // Unauthorized trader swaps through router — extension sees router, passes check
    vm.prank(unauthorizedTrader);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: unauthorizedTrader,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Pool token1 balance decreased despite trader not being allowlisted
    assertLt(token1.balanceOf(address(pool)), poolToken1Before);
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
