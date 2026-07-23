Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper Identity, Allowing Any User to Bypass Swap Allowlist on Curated Pools — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the `msg.sender` of `MetricOmmPool.swap` — against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the originating user. Because the router must be allowlisted for any legitimate user to use it on a curated pool, any unprivileged user can route through the public router and have their swap pass the allowlist check, completely bypassing the per-user restriction.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all `exact*` variants) calls `pool.swap(...)` directly with no mechanism to forward the originating user's address: [4](#0-3) 

When `bob` calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router becomes `msg.sender` inside `MetricOmmPool.swap`, so `sender = router` is delivered to the extension. The extension checks `allowedSwapper[pool][router]` — if the router is allowlisted (a necessary precondition for any legitimate user to use the router on the pool), the check passes regardless of who the actual originating user is. The wrong value is the identity checked: `router address` instead of `bob's address`.

## Impact Explanation
A curated pool with `SwapAllowlistExtension` configured (e.g., KYC-gated, institutional, or market-maker-restricted) can be accessed by any unprivileged user by routing through `MetricOmmSimpleRouter`. The allowlist guard is completely bypassed. Unauthorized users can execute swaps against the pool's liquidity, exposing LPs to unauthorized counterparties and circumventing compliance or access controls the pool admin intended to enforce. This constitutes a direct loss of policy enforcement with fund-impacting consequences for LPs on curated pools.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract with no access controls on its `exact*` entry points. The bypass requires only that the router be in the allowlist — which is a necessary condition for any legitimate user to use the router on a curated pool. Pool admins who configure a swap allowlist and also want to support router-mediated swaps will inevitably create this bypass condition. The attack requires no special privileges, no malicious setup, and no non-standard tokens. [5](#0-4) 

## Recommendation
The `SwapAllowlistExtension` must not treat `sender` (the immediate caller of `pool.swap`) as the authoritative swapper identity when the pool supports router-mediated swaps. Options:

1. **Forward original caller in `extensionData`**: Have the router encode the originating `msg.sender` into `extensionData` and have the extension decode and verify it (e.g., via a router-signed attestation). The extension then checks the decoded user address, not `sender`.
2. **Check `recipient` instead of `sender`**: If the pool's design guarantees `recipient` is always the actual beneficiary, check `recipient`. This is weaker but avoids the router indirection problem.
3. **Disallow router on allowlisted pools**: Add a runtime check in the router or document and enforce that `MetricOmmSimpleRouter` cannot be used with pools that have `SwapAllowlistExtension` configured in restricted mode.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` in restricted mode. Admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` (router must be added for `alice` to use the router).
2. `bob` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the curated pool.
3. The router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
4. `MetricOmmPool.swap` passes `sender = router` to `_beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` — returns `true` — check passes.
6. `bob`'s swap executes successfully despite `bob` not being in the allowlist.
7. `bob` receives output tokens; the curated pool's LP set is exposed to an unauthorized counterparty. [6](#0-5)

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
