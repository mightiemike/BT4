Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their trusted users inadvertently opens the pool to every user who calls through the router, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

Every router entry point calls `pool.swap()` directly, making the router the pool's `msg.sender`. For `exactInputSingle`: [4](#0-3) 

The same pattern applies to `exactInput` (L104–112), `exactOutputSingle` (L136–137), and `exactOutput` (L165–181): [5](#0-4) 

**Bypass path:** A pool admin who wants their allowlisted users to benefit from the router calls `setAllowedToSwap(pool, router, true)`, setting `allowedSwapper[pool][router] = true`. From that point, the check `allowedSwapper[msg.sender][sender]` evaluates to `allowedSwapper[pool][router]` for every user who calls through the router — regardless of whether that user is on the allowlist. The allowlist is fully bypassed for all router-mediated swaps. [6](#0-5) 

**Secondary consequence:** If the pool admin does *not* allowlist the router, allowlisted users who try to swap through the router are blocked (the router is not in the allowlist), even though their own address is. They must call the pool directly, losing slippage protection and multi-hop routing.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted. An unpermissioned user can execute swaps against a pool explicitly designed to exclude them, draining liquidity or extracting value at oracle-anchored prices the LP intended to offer only to trusted counterparties. This constitutes broken core pool functionality (the allowlist guard) and direct loss of LP principal — matching the "Broken core pool functionality causing loss of funds" allowed impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a swap-allowlisted pool and wants their allowlisted users to benefit from the router's slippage protection and multi-hop routing will naturally allowlist the router. The documentation and interface give no indication that doing so opens the pool to all users. The trigger is a single, well-motivated admin call with no malicious intent required. Any unprivileged user can then exploit the open pool by simply calling through the router.

## Recommendation
The extension must check the *originating user*, not the intermediary:

1. **Pass the original user through the router.** Add an `originSender` field to the swap callback context and have the pool forward it as `sender` to extensions when the direct caller is a known periphery contract, mirroring how Uniswap v4 passes `msgSender` through the unlock/callback chain.

2. **Gate on `recipient` as a fallback.** For the swap allowlist specifically, check `recipient` (the address that receives output tokens) in addition to or instead of `sender`. The recipient is always the end user and cannot be spoofed by the router.

3. **Document the invariant.** Until fixed, `SwapAllowlistExtension` NatSpec must state that it gates the *direct pool caller*, not the end user, and that allowlisting any public intermediary (router, multicall) opens the pool to all callers of that intermediary.

## Proof of Concept
```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so their trusted users can use it
ext.setAllowedToSwap(pool, address(router), true);

// Alice (not on the allowlist) calls the router
router.exactInputSingle(ExactInputSingleParams({
    pool:             pool,
    recipient:        alice,
    zeroForOne:       true,
    amountIn:         1e18,
    amountOutMinimum: 0,
    priceLimitX64:    type(uint128).max,
    deadline:         block.timestamp,
    extensionData:    ""
}));
// pool.swap is called with msg.sender = router
// _beforeSwap passes sender = router to SwapAllowlistExtension
// allowedSwapper[pool][router] == true  →  check passes
// Alice's swap executes despite never being on the allowlist
```

The pool admin intended to allowlist specific users; the wrong account (the router) was checked instead, and the allowlist is bypassed for all router-mediated swaps.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
