Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Evaluates Router Address as Swapper, Enabling Full Allowlist Bypass for Any User Routing Through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is designed to gate pool swaps by per-user address. However, `MetricOmmPool.swap()` captures `msg.sender` and forwards it as `sender` to `_beforeSwap`, so when `MetricOmmSimpleRouter` intermediates the call, `sender` resolves to the router contract address rather than the originating user. Any pool that allowlists the router (required for router-based swaps to function) inadvertently grants every user routing through it unconditional swap access, fully defeating the per-user allowlist.

## Finding Description
The call chain is confirmed in production code:

1. `MetricOmmPool.swap()` captures `msg.sender` and passes it as `sender` to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap()` forwards that same `sender` value verbatim to every configured extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap()` enforces the guard against `sender` — the direct pool caller — not the originating user: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle()` (and all other swap entry points) calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router — which is structurally required for any router-mediated swap to succeed — the check passes unconditionally for every user who routes through it, regardless of whether that user's own address is on the allowlist. The `recipient` parameter (the actual end user) is present in the `beforeSwap` signature but is ignored by the extension (the second argument is unnamed/discarded): [5](#0-4) 

## Impact Explanation
Any pool that simultaneously deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses and allowlists `MetricOmmSimpleRouter` so that legitimate users can route through it is fully open to any address that calls through the router. The allowlist provides zero per-user protection. This enables unauthorized participants to drain liquidity from a private or permissioned pool, bypass KYC/compliance gates the pool admin believed were enforced, and cause LP principal loss if the pool's pricing or depth was calibrated for a specific trusted counterparty set. This satisfies the **admin-boundary break** and **broken core pool functionality** impact categories.

## Likelihood Explanation
The conflict is structural and unavoidable: any operator who configures `SwapAllowlistExtension` on a pool and also needs the standard user-facing router to work must allowlist the router. There is no mechanism in the current design to simultaneously allow router-based swaps and enforce per-user restrictions. The scenario is not hypothetical — it is the default production configuration for any permissioned pool using the router.

## Recommendation
The extension should check the end user rather than the direct pool caller. Two concrete options:

1. **Check `recipient`** — `recipient` is the address that receives output tokens and is already passed as the second argument to `beforeSwap`. For router-mediated swaps this is the actual user. Change the guard to `allowedSwapper[msg.sender][recipient]` instead of `allowedSwapper[msg.sender][sender]`.

2. **Pass end-user identity via `extensionData`** — The router encodes the originating user address into `extensionData`; the extension decodes and verifies it (with a signature or trusted-forwarder pattern).

Option 1 is the minimal fix consistent with how `recipient` is already threaded through the hook signature.


## Proof of Concept
```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E.
2. Admin calls E.setAllowedToSwap(P, router, true)   // router allowlisted so users can route
3. Admin does NOT call E.setAllowedToSwap(P, alice, true)  // alice is NOT allowlisted

Attack
──────
4. Alice calls MetricOmmSimpleRouter.exactInputSingle(..., pool=P, recipient=alice, ...)
5. Router calls P.swap(recipient=alice, ..., extensionData)
   → msg.sender inside P = router
6. P calls _beforeSwap(sender=router, recipient=alice, ...)
7. E.beforeSwap(sender=router, ...) evaluates:
      allowedSwapper[P][router] == true  ✓  → no revert
8. Swap executes. Alice receives tokens from a pool she was explicitly excluded from.
```

The guard never inspects `alice`; it only sees `router`, which is allowlisted. The allowlist is fully bypassed. [6](#0-5)

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
