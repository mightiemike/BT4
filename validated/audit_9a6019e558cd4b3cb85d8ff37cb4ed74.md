Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any account to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is that direct caller, so the extension evaluates the router's allowlist status rather than the originating user's. Any pool admin who allowlists the router to enable legitimate router-mediated swaps simultaneously opens the pool to every unpermissioned account on-chain.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` — whoever called `pool.swap()` — as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating `msg.sender`:

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
``` [3](#0-2) 

The same wrong-actor binding applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165) — all router entry points call `pool.swap()` directly, substituting the router address for the originating user. [4](#0-3) 

The `extensionData` field is user-supplied and unverified; there is no mechanism in the router to forward the originating `msg.sender` in a cryptographically verified way. The extension has no fallback check and no awareness of router intermediaries.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` must allowlist the router if they want any approved user to trade through the standard periphery. Once the router is allowlisted, the allowlist is effectively open to every account on-chain: any disallowed user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the pool, the extension sees `sender = router`, finds it allowlisted, and permits the swap. The curated pool's access control is completely defeated. This constitutes broken core pool functionality — the access-control hook produces the wrong extension decision (`allowedSwapper[pool][router] == true` instead of `allowedSwapper[pool][unpermissioned_user] == false`), enabling unauthorized swaps that drain liquidity or extract fees from LP positions that were never meant to be accessible.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. Any pool admin who wants their allowlisted users to use the router must add the router to the allowlist — this is the expected operational pattern, not an edge case. The precondition (router is allowlisted) is the normal deployment state. Any unpermissioned account can exploit this with a single public call to the router, requiring no special privileges, no flash loans, and no complex setup.

## Recommendation
The extension must gate the economically relevant actor — the originating user — not the immediate caller of `pool.swap()`. The cleanest fix is for the router to forward the originating `msg.sender` as a verified parameter via `extensionData` (e.g., ABI-encoded with a router-signed attestation or via a trusted forwarder pattern), and for the extension to read and verify that value instead of `sender`. Alternatively, the pool's `swap()` interface could carry originating user identity in a way the extension can verify. A fragile but simpler mitigation is for the extension to reject calls where `sender` is a known router contract, but this requires maintaining a router registry and is bypassable by deploying a new router.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)   ← required for Alice to use the router
  - Bob is NOT in the allowlist

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  → passes
  6. Swap executes. Bob has traded on a pool he was never permitted to access.

Direct call (correctly blocked):
  1. Bob calls pool.swap(...) directly
  2. Pool calls _beforeSwap(sender=bob, ...)
  3. Extension checks: allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap
```

The wrong value is the extension decision: `allowedSwapper[pool][router] == true` is evaluated instead of `allowedSwapper[pool][bob] == false`, causing `beforeSwap` to return `IMetricOmmExtensions.beforeSwap.selector` (permit) when it should revert with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
