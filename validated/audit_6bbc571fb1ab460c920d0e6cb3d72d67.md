All four cited code locations are confirmed in the repository. Let me verify the complete call chain:

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument — the immediate caller of `pool.swap()`. [1](#0-0) 

2. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which is the router address when called via `MetricOmmSimpleRouter`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no per-user allowlist check, making `msg.sender` in the pool equal to the router address. [4](#0-3) 

The vulnerability is real and the entire call chain is confirmed.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Disallowed Swappers to Bypass Curated-Pool Access Control - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address rather than the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every user — including explicitly disallowed ones — can bypass the curated-pool allowlist by routing through the public router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap` (L230–240). `ExtensionCalling._beforeSwap` forwards this value unchanged via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` (L160–176). `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` (L37), where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly (L72–80) with no per-user allowlist enforcement. This makes the pool's `msg.sender` the router contract, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

**Attack path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only specific user addresses.
2. To support router-mediated swaps for those users, admin calls `setAllowedToSwap(pool, router, true)` — the only available mechanism.
3. Any disallowed user calls `router.exactInputSingle({pool: curatedPool, ...})`.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. `beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The disallowed user successfully trades on the curated pool.

The inverse also holds: allowlisted users who call through the router are rejected because `allowedSwapper[pool][router]` is `false`, forcing direct pool calls and defeating the purpose of the periphery.

## Impact Explanation
The curated pool's swap allowlist is rendered entirely ineffective for any user routing through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to support router-mediated swaps), the allowlist becomes a no-op for all router callers. Disallowed users can execute live swaps against pool liquidity, directly violating the pool's access-control invariant. This constitutes broken core pool functionality causing potential loss of funds — disallowed users can arbitrage or front-run against LP positions that the allowlist was designed to protect.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. Pool admins who want to support both direct and router-mediated swaps for allowlisted users will naturally allowlist the router, unknowingly opening the gate to all users. The exploit requires no special privileges, no unusual token behavior, and no multi-transaction setup — a single `exactInputSingle` call suffices. The condition (router allowlisted) is a natural and expected admin action.

## Recommendation
The extension must recover the original end-user identity rather than the immediate caller of `pool.swap()`. The simplest safe fix: the router always appends `abi.encode(msg.sender)` to `extensionData` for allowlisted pools, and the extension decodes and checks that address when `sender` is a known router. Alternatively, the router itself enforces a per-pool allowlist before calling `pool.swap()`, so the pool-level extension sees the router only when the router has already verified the user.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin: setAllowedToSwap(pool, alice, true)   // alice is the intended allowlisted user
3. Admin: setAllowedToSwap(pool, router, true)   // needed so alice can use the router
4. Bob (disallowed) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...) with msg.sender = router
   → _beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true  → passes
   → Bob's swap executes on the curated pool despite being disallowed.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
