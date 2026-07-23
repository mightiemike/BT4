Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is the production guard for curated pools restricting which addresses may swap. Its `beforeSwap` hook receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call — the router contract when swaps are routed through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (the only way to enable router-mediated swaps), every user on-chain can bypass the allowlist by routing through it, completely defeating the curation policy.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

Inside the pool, `msg.sender` is the **router**, so `sender` forwarded to the extension is the **router address**, not the end user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma:
- **Do not allowlist the router**: Individually allowlisted users cannot swap through the standard periphery at all.
- **Allowlist the router**: Every user on-chain can bypass the allowlist by routing through the router.

By contrast, `DepositAllowlistExtension` correctly checks `owner` (the position holder), which is explicitly passed as a separate parameter and is not conflated with the caller: [5](#0-4) 

The swap path has no equivalent correct binding — `sender` is structurally the immediate caller, not the originating user.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a whitelist of addresses is rendered fully open to any user who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's LP reserves without being on the allowlist. In pools where the allowlist enforces KYC, regulatory constraints, or adverse-selection protection, this constitutes a high-severity policy bypass with direct fund-impacting consequences for LPs. This matches the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point in the periphery. Any user who reads the docs or inspects the periphery contracts will discover the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only calling the public router instead of the pool directly. The condition (pool admin allowlisting the router to enable normal router usage) is the expected operational state. Likelihood is high.

## Recommendation
The pool should convey the end user's identity to the extension, not the immediate `msg.sender`. The cleanest fix consistent with the existing interface is to establish a convention where `MetricOmmSimpleRouter` ABI-encodes the real user as the first word of `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes it when present, falling back to `sender` for direct pool calls. Alternatively, the pool could accept an explicit `originator` parameter in `swap()` and forward it to extensions, letting the router supply the real user while the pool enforces that `msg.sender` implements the callback.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1`, configured with `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient=bob, ...)`. Inside the pool, `msg.sender = router`.
6. `_beforeSwap(router, bob, ...)` is called. The extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

Alternatively, if the admin does **not** allowlist the router:
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(...)`. Extension checks `allowedSwapper[pool][router]` → `false`.
6. Reverts `NotAllowedToSwap` — Alice cannot use the standard periphery even though she is individually allowlisted.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
