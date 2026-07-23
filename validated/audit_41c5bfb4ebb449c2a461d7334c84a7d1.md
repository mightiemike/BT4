All four code references check out exactly as cited. The full call chain is confirmed in production code:

- `MetricOmmPool.sol` L230-231: `_beforeSwap(msg.sender, ...)` passes the caller (router) as `sender`
- `ExtensionCalling.sol` L149-176: `_beforeSwap` forwards `sender` unchanged to extensions
- `SwapAllowlistExtension.sol` L37: checks `allowedSwapper[msg.sender][sender]` — pool as key, router as swapper
- `MetricOmmSimpleRouter.sol` L72-80: calls `pool.swap(...)` directly, making the router `msg.sender` in the pool

The invariant break is real and unmitigated: the extension has no mechanism to recover the original end-user address, and allowlisting the router is the only way to permit router-mediated swaps for any user.

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` rather than the end-user's address. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently opens the allowlist to every unprivileged address that calls through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router when the call arrives via the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

There is no mechanism in the extension, the pool, or the router to recover the original end-user address. The router stores the original `msg.sender` in transient storage for callback payment purposes only — it is never surfaced to the pool or extensions. [5](#0-4) 

## Impact Explanation
A pool admin deploying a curated pool (KYC-gated, institutional-only, or regulatory-restricted) with `SwapAllowlistExtension` who also allowlists the router loses all per-user access control. Any address — including those explicitly not allowlisted — can execute swaps against the pool's LP reserves by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. LP funds are exposed to unauthorized counterparties, breaking the pool's curation guarantee and constituting direct loss of LP principal if the pool carries favorable pricing for its intended participants.

## Likelihood Explanation
The bypass requires the pool admin to add the router to the allowlist. This is a natural and expected operational step: allowlisted users need the router for slippage protection, multi-hop routing, and WETH wrapping. The design gives the admin no alternative — there is no way to permit router-mediated swaps for specific users without also opening the gate to all users. Any production deployment of a curated pool that supports the router is therefore vulnerable. The attacker requires no special privileges, only the ability to call the public router.

## Recommendation
1. **Router-side:** `MetricOmmSimpleRouter` should encode the original `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` should decode and check that address when present, falling back to `sender` otherwise.
2. **Extension-side (short-term):** Document that `SwapAllowlistExtension` is incompatible with router-mediated flows and must only be used on pools where all swappers call the pool directly.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // KYC'd user
  allowedSwapper[pool][router] = true   // admin adds router to support alice's UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  pool.swap(bob, ...) is called with msg.sender = router
  _beforeSwap(router, bob, ...)
  SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → PASSES

  bob's swap executes against LP reserves despite not being allowlisted.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
