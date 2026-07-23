Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual EOA Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument forwarded by the pool, which is `msg.sender` at the pool level — the router contract address, not the originating EOA. When a pool admin allowlists the router to enable legitimate router-mediated swaps, every non-allowlisted user can bypass the guard by routing through the same public `MetricOmmSimpleRouter`. The allowlist invariant is completely broken for any pool that permits router usage.

## Finding Description
In `MetricOmmPool.swap()`, `msg.sender` (the router) is passed as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]` where `sender` is the router address: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, passing `params.extensionData` as-is with no EOA encoding: [4](#0-3) 

The pool admin faces an impossible choice: if the router is not allowlisted, no user (including legitimate allowlisted ones) can use the router. If the router is allowlisted, every user on the network can bypass the allowlist by routing through it. The extension has no mechanism to recover the originating EOA from the call chain.

For multi-hop `exactInput`, the router is `msg.sender` to every pool in the path, so the bypass applies to all hops simultaneously: [5](#0-4) 

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps against the pool's liquidity at oracle-anchored prices, bypassing the access-control invariant the extension was designed to enforce. This constitutes a complete failure of a core pool access-control mechanism and a direct loss of LP principal through unauthorized swap execution.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user who observes that a pool has a swap allowlist can trivially route through the router in a single transaction. No privileged access, special tokens, or multi-step setup is required. The bypass is unconditional once the router is allowlisted by the pool admin, which is a necessary operational step for any pool that intends to support router-mediated swaps for legitimate users.

## Recommendation
The extension must gate the original EOA, not the intermediary router. The cleanest fix is to have `MetricOmmSimpleRouter` encode `msg.sender` (the EOA) into `extensionData` before calling `pool.swap()`, and update `SwapAllowlistExtension.beforeSwap()` to decode and check that address when `sender` is a recognized router. Alternatively, the extension can fall back to `tx.origin` when `sender` is a known contract, though this approach does not support smart-contract wallets.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; alice is allowlisted, charlie is not.
// Admin allowlists alice AND the router (required for alice to use the router).
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Charlie (not allowlisted) calls the pool directly → reverts NotAllowedToSwap ✓
vm.prank(charlie);
pool.swap(charlie, false, 1000, type(uint128).max, "", "");
// → revert NotAllowedToSwap

// Charlie routes through the router → SUCCEEDS (bypass)
vm.prank(charlie);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: charlie,
    tokenIn: token0,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// → succeeds; SwapAllowlistExtension saw sender=router (allowlisted), not charlie
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
