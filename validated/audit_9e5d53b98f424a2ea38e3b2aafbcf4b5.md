Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Instead of Actual End User, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `msg.sender` inside `pool.swap()` is the router contract, not the end user. Any non-allowlisted user can therefore bypass the curated pool's access control by routing through `MetricOmmSimpleRouter`, provided the router itself is allowlisted (which it must be for any router-based swap to succeed).

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the `msg.sender` inside `pool.swap()`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is that `beforeSwap` evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an inescapable dilemma: allowlisting the router opens the pool to every user regardless of their individual allowlist status; not allowlisting the router makes the router entirely unusable for that pool. No configuration simultaneously enforces per-user access control through the router.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to KYC-verified counterparties or institutional LPs cannot enforce that restriction when `MetricOmmSimpleRouter` is the entry point. Any unpermissioned user can call `exactInputSingle` (or any other router swap function) and trade against the pool's liquidity, bypassing the intended access control entirely. LP funds are exposed to counterparties the pool admin explicitly intended to exclude. This constitutes broken core pool functionality causing potential loss of funds — a direct match to the allowed impact gate for broken access-controlled swap flows.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery entry point for all swaps. Any user who knows the pool address can call it without any special privilege, flash loan, price manipulation, or privileged role. The bypass requires only a standard router call. Likelihood is **High**.

## Recommendation
The extension must gate on the actual end user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Router-side identity forwarding**: Require `MetricOmmSimpleRouter` to ABI-encode `msg.sender` (the actual user) into `extensionData` for each hop, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Recipient-based gating**: Change the check to `allowedSwapper[msg.sender][recipient]` — the recipient is the address that receives output tokens and is typically the actual user. This is simpler but requires pool admins to allowlist recipient addresses rather than initiator addresses.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowlisted for any router swap
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is individually approved
  - bob is NOT in the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, zeroForOne, amount, ...)
     → msg.sender inside pool.swap() = router address
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives output tokens

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.

Foundry test outline:
  - Deploy pool with SwapAllowlistExtension
  - allowlist router, allowlist alice, do NOT allowlist bob
  - vm.prank(bob); router.exactInputSingle(...)
  - Assert swap succeeds (no NotAllowedToSwap revert)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
