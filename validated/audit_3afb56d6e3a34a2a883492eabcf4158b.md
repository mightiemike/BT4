Audit Report

## Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Replaces User Identity in `beforeSwap` Sender Check - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to support legitimate users simultaneously grants swap access to every caller of the router, completely defeating the per-user allowlist.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // router address when called via MetricOmmSimpleRouter
  recipient,
  ...
  extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates that value as the swapper identity:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:71-80
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

The pool sees `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same pattern applies to `exactInput` (line 103-112) and `exactOutput`/`exactOutputSingle` (lines 136-137, 165-181), where the router is always the direct caller of `pool.swap()`. The `_setNextCallbackContext` call stores `msg.sender` only for payment routing in the callback — it is never forwarded to the pool's extension hook.

Existing guards are insufficient: the extension has no mechanism to distinguish a router-mediated call from a direct call, and the pool's hook interface only exposes the immediate caller as `sender`.

## Impact Explanation

Any user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter`. Unauthorized users gain the ability to swap against LP funds at oracle-derived prices on pools configured to be curated (e.g., KYC-gated, counterparty-restricted, or protocol-only). The allowlist is the sole protection boundary for such pools; once bypassed, LP principal is directly at risk from volume from unauthorized counterparties. This constitutes a direct loss of user/LP principal and broken core pool functionality, meeting Critical/High severity thresholds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint. No privileged access, flash loan, or multi-step setup is required. The only precondition is that the pool admin has allowlisted the router — which is the expected operational choice to support legitimate users. Any user who discovers a pool uses `SwapAllowlistExtension` can immediately attempt the bypass by calling the router. The attack is repeatable and requires no special on-chain state beyond the router being allowlisted.

## Recommendation

The extension must check the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Viable fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks that address. Requires a coordinated convention between router and extension.
2. **Gate at the router level**: The router enforces its own allowlist check before calling the pool, and the extension trusts only direct pool callers (non-router addresses).
3. **Most robust**: Change `SwapAllowlistExtension` to require routers to attest the real user identity via a signed or encoded payload in `extensionData`, and verify that attestation before accepting the router's address as a pass-through.

## Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin allowlists alice: allowedSwapper[pool][alice] = true
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
    (required so alice can use the router)

Attack:
  1. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap() — pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives output tokens, LP funds exposed

Result: bob, never individually allowlisted, successfully swaps on a
        pool restricted to alice only.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
