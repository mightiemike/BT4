Audit Report

## Title
Router-Mediated Swap Passes Router Address as `sender` to `SwapAllowlistExtension`, Allowing Any User to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension::beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap(...)`. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router, not the originating user. If the router is allowlisted (required for any router-mediated swap to succeed), the per-user allowlist is structurally bypassed for all router-mediated swaps, allowing any unprivileged user to swap on a pool that should have blocked them.

## Finding Description
In `MetricOmmPool::swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards this `sender` value as the first argument to the extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original user's identity — the pool sees `msg.sender = router`: [4](#0-3) 

The same identity confusion applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. There is no existing guard that recovers the original user's address before the allowlist check executes. The `extensionData` field is passed through but the extension does not decode it for identity purposes.

## Impact Explanation
This is a broken core pool functionality / admin-boundary break. A pool admin deploys `SwapAllowlistExtension` specifically to restrict which addresses may swap. The structural identity confusion means there is no configuration that simultaneously (a) allows specific users to use the router and (b) blocks others. If the router is allowlisted, any address can call the router and bypass the per-user restriction entirely. The allowlist invariant — that only explicitly permitted addresses may swap — is completely defeated for all router-mediated paths. This constitutes a direct bypass of an admin-enforced access control boundary by an unprivileged caller.

## Likelihood Explanation
The router (`MetricOmmSimpleRouter`) is the standard public entrypoint for swaps in the periphery and is permissionless — any address can call it. Any pool that deploys `SwapAllowlistExtension` with `allowAllSwappers = false` and expects users to interact via the router is affected. The pool admin has no on-chain remedy without replacing the extension, since the identity confusion is structural and not configurable. The attack requires a single transaction with no special privileges.

## Recommendation
The extension must receive the original user's identity, not the intermediary's. The preferred fix is for the pool to pass both the direct caller (`msg.sender`) and an `originSender` (e.g., from a signed context or a trusted-forwarder pattern analogous to EIP-2771) so the extension can check the true initiator. Alternatively, the router should be redesigned to pass the original `msg.sender` as verified `extensionData` in a way the extension can authenticate (e.g., the pool trusts a specific router and decodes the payer from a signed payload). The simplest interim fix is for `SwapAllowlistExtension` to decode an address from `extensionData` when `sender` is a known router, but this is forgeable without authentication. The cleanest architectural fix adds an `originSender` parameter to `beforeSwap` at the pool level.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for any router use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack (single transaction):
  - attacker calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...)   // pool sees msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true  ✅ (passes)
  - attacker's swap executes on an allowlisted pool they were never authorized to use

Same path applies to exactOutputSingle, exactInput, and exactOutput.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
