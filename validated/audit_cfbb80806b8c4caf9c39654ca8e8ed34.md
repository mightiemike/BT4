Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Real User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which equals the pool's `msg.sender` — the router when a user routes through `MetricOmmSimpleRouter`. The extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the natural operational choice for router-mediated access), every unprivileged user can bypass the per-user allowlist and execute swaps on a curated pool, causing direct loss of LP principal.

## Finding Description

**Call chain:**

`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — never the actual user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the real user's address: [4](#0-3) 

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap(...)` with the router as `msg.sender`.

**Why existing guards fail:** The extension has no way to recover the real initiating user. The `sender` parameter is structurally bound to the immediate pool caller. The `allowedSwapper` mapping is keyed by `(pool, swapper)` and the swapper is always the router when the router is used.

## Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted, any unprivileged user can execute swaps against the pool's liquidity at oracle-anchored prices. This constitutes a direct loss of LP principal on pools where the allowlist is the primary access-control mechanism, as LPs deposited under the assumption that only trusted counterparties could trade. This is a broken core pool functionality / admin-boundary bypass by an unprivileged path causing direct loss of funds.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical user-facing entry point for swaps.
- A pool admin who deploys a curated pool and wants to support router-mediated swaps for their allowlisted users must allowlist the router — this is the expected operational path.
- Once the router is allowlisted, the bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.
- The flaw is invisible in unit tests that call the pool directly, because direct calls correctly bind the user's address as `sender`.

## Recommendation

The extension must recover the real initiating user rather than the immediate pool caller. The cleanest architectural fix is for the router to forward the real user address as an explicit parameter in `extensionData`, and for the extension to decode and verify it. Alternatively, the pool could propagate a separate `origin` field (e.g., `tx.origin` or a router-signed payload) alongside `sender`. Short-term: document that allowlisting the router on a `SwapAllowlistExtension`-protected pool effectively opens the pool to all users, and require pool admins to allowlist individual users only (accepting that those users cannot use the router).

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// `attacker` is NOT allowlisted.
// Router IS allowlisted (pool admin called setAllowedToSwap(pool, router, true)).

// Step 1 – direct swap by attacker reverts correctly:
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Step 2 – router-mediated swap by attacker succeeds (bypass):
token0.approve(address(router), type(uint256).max);
vm.prank(attacker);
// No revert — extension sees sender=router, which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        recipient:        attacker,
        deadline:         block.timestamp + 1,
        amountIn:         1000,
        amountOutMinimum: 0,
        zeroForOne:       true,
        priceLimitX64:    type(uint128).max,
        extensionData:    ""
    })
);
// attacker received token1 from a pool they were never supposed to access.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
