Audit Report

## Title
SwapAllowlistExtension gates on the immediate pool caller (router address) instead of the actual end-user, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is set to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router's address. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, any unprivileged user can bypass the individual allowlist by routing through the same router.

## Finding Description
In `MetricOmmPool.sol`, `swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks: [3](#0-2) 

Where `msg.sender` = pool and `sender` = the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly: [4](#0-3) 

So `sender` = router address. If the pool admin has allowlisted the router (`allowedSwapper[pool][router] = true`) to let allowlisted users route through it, the check passes for **any** caller of the router, regardless of individual allowlist status. The extension has no mechanism to distinguish the actual end-user from the router.

## Impact Explanation
Any user not on the allowlist can bypass the swap restriction by routing through `MetricOmmSimpleRouter`. This breaks the pool admin's access control invariant. In pools designed to restrict swaps to specific institutional or KYC-verified participants, unauthorized users can interact with the pool and drain LP liquidity. The allowlist is the sole mechanism preventing unauthorized swaps; once bypassed, there is no secondary guard.

## Likelihood Explanation
Medium. The pool admin must have allowlisted the router for this bypass to work. However, allowlisting the router is a natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users — the admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same. The attacker requires no special privileges, only access to the public router.

## Recommendation
The extension must check the actual end-user's address, not the immediate pool caller. Two options:

1. **Trusted-forwarder pattern:** The router appends the original `msg.sender` to `extensionData`; the extension reads and verifies it against the allowlist (requires router to be a trusted forwarder recognized by the extension).
2. **Pool-level sender tracking:** The pool exposes the original initiator via a transient storage slot that the extension reads directly, bypassing the `sender` parameter entirely.

## Proof of Concept
1. Pool admin deploys pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle(pool=pool, ...)`.
5. Router calls `pool.swap()` → pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes against the restricted pool, bypassing the allowlist entirely.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
