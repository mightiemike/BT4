Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` argument it receives, which the pool always sets to `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the router is added to the allowlist — the natural operational step for supporting router-mediated swaps — every address, including explicitly non-allowlisted ones, can bypass the allowlist by routing through the router. The individual user identity is never checked.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender == msg.sender of pool.swap()
    )
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router `msg.sender` inside the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Bypass path:** A pool admin who wants to support router-mediated swaps calls `setAllowedToSwap(pool, router, true)`. From that moment, the extension evaluates `allowedSwapper[pool][router]` — which is `true` — for every swap routed through the router, regardless of who the actual end user is. The attacker's address is never checked.

**Broken-functionality path (secondary):** A pool admin who allowlists specific user EOAs but does not allowlist the router will find that those users cannot swap through the router at all, even though they are individually permitted, because the extension sees the router address and rejects it.

No existing guard compensates for this: the `extensionData` field passed by the router is an empty string `""` for single-hop swaps, so the extension has no out-of-band channel to recover the real user identity. [6](#0-5) 

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict swaps to trusted counterparties (e.g., whitelisted market makers, KYC'd addresses, or protocol-controlled bots) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The swap executes at oracle-derived bid/ask prices; the pool's LP positions absorb the trade as if it were from an authorized party. Because the pool is oracle-anchored, the LP bears the full adverse-selection risk that the allowlist was designed to prevent. This constitutes a direct loss of LP principal or owed fees above Sherlock thresholds whenever the pool's curation policy has economic value. This maps to the allowed impact: broken core pool functionality causing loss of funds, and admin-boundary break where an unprivileged path bypasses a required access control check.

## Likelihood Explanation

The router is a standard, publicly deployed periphery contract. Any pool that wants to support normal user-facing swap UX must either allowlist the router or accept that allowlisted users cannot use the router. Allowlisting the router is the natural operational choice and is the step that opens the bypass. The attacker needs no special privilege: they call a public router function with standard parameters. No admin action beyond the natural allowlist setup, no malicious initial pool setup, and no non-standard token behavior is required. The condition is reachable by any unprivileged trader.

## Recommendation

The `SwapAllowlistExtension` must gate on the economic actor (the end user), not the immediate `msg.sender` of `pool.swap`. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should forward the original `msg.sender` (the end user) inside `extensionData` so extensions can decode and check it. The extension would then verify the decoded user address against the allowlist when `sender` is a known router.

2. **Extension-side:** `SwapAllowlistExtension.beforeSwap` should decode the real user from `extensionData` when `sender` is a known router, or the pool/router architecture should be redesigned so the extension always receives the originating user address as a first-class argument.

Until fixed, pool admins using `SwapAllowlistExtension` must not allowlist the router, which means allowlisted users cannot use the router — breaking the intended UX.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (to allow router-mediated swaps for legitimate users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
  - Attacker address is therefore not in the allowlist.

Attack:
  1. Attacker calls router.exactInputSingle({
         pool: pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(attacker, true, X, ...).
     msg.sender inside pool.swap() == router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  5. Swap executes at oracle price; attacker receives output tokens.

Result:
  - Attacker (not in allowlist) successfully swaps on a curated pool.
  - The allowlist guard was bypassed because the extension checked the router,
    not the attacker.

Foundry test outline:
  - Deploy pool + SwapAllowlistExtension.
  - setAllowedToSwap(pool, address(router), true).
  - vm.prank(attacker); router.exactInputSingle(...).
  - Assert swap succeeds (no NotAllowedToSwap revert).
  - Assert attacker received output tokens.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
