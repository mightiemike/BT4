Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, enabling complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so `sender` arriving at the extension is the router address, not the end user. Any pool that allowlists the router to support normal periphery usage inadvertently grants every address — including explicitly blocked ones — the ability to swap freely on the curated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call. The original end-user's address (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback and is never forwarded to the pool or extension: [4](#0-3) 

The result: the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][endUser]`. This creates two mutually exclusive broken states:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user, including explicitly blocked ones, bypasses the allowlist via the router |
| No | Every allowlisted user is blocked from using the router; the canonical swap path is broken |

No existing guard in the extension, pool, or router detects or prevents this mismatch. The extension has no mechanism to distinguish a direct caller from a router-mediated caller, and the router has no mechanism to forward the originating user identity to the extension.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` for access control (KYC-gated, institution-only, or compliance-restricted pools) has its entire curation guarantee voided. Any unpermissioned address can call `router.exactInputSingle()` and execute swaps at oracle-derived prices. LPs in such a pool suffer direct principal loss as unauthorized users drain one side of the pool. This is a broken core pool security invariant with direct fund impact, meeting the Critical/High threshold.

## Likelihood Explanation
The router is the canonical, documented user-facing entrypoint. Any pool that enables `SwapAllowlistExtension` and also wants users to use the router must allowlist the router — this is the only way to support normal periphery usage. No special preconditions, privileged access, non-standard tokens, or unusual configurations are required. The bypass is triggered automatically by the standard usage pattern.

## Recommendation
The extension must verify the economic actor, not the immediate caller. Concrete options:

1. **Pass the originating user through `extensionData`**: Have the router encode `msg.sender` in `extensionData` and have the extension decode and verify it, with a trust check that `msg.sender` (the pool's caller) is a known router.
2. **Expose the payer from transient storage**: The router already stores the payer via `_setNextCallbackContext` in transient storage; expose it or pass it as a verified field through the pool's call path.
3. **Document and enforce incompatibility**: Add a check in `ValidateExtensionsConfig` (or equivalent factory validation) that rejects pool configurations combining `SwapAllowlistExtension` with any router-callable setup, and provide a separate router-aware allowlist extension.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, userA, true)   // userA is KYC'd
3. Pool admin: setAllowedToSwap(pool, router, true)  // required for router support
   // userB is NOT allowlisted
4. userB calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...) — msg.sender of pool.swap() = router
   → pool calls _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for userB
5. userB, who should be blocked, has swapped on the curated pool.
   The allowlist is completely bypassed.
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
