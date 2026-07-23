Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router so that permitted users can trade through it, every unprivileged user gains identical access by calling the same public router, completely defeating the allowlist's access-control invariant.

## Finding Description
`MetricOmmPool.swap` unconditionally forwards `msg.sender` as the `sender` argument to the extension hook:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // always the immediate caller of pool.swap()
  recipient, ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with the router as `msg.sender` and passes `params.extensionData` directly (user-controlled bytes, not the originating user's address):

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← not the user's address
  );
``` [3](#0-2) 

The full call chain resolves to `allowedSwapper[pool][router]`. Two broken states arise:

1. **Bypass (primary):** Admin allowlists the router so permitted users can trade through it → every address on the network passes the gate by calling the public router.
2. **Broken functionality (secondary):** Admin does not allowlist the router → allowlisted users cannot use the router at all; their swaps revert with `NotAllowedToSwap` even though they are individually permitted.

No existing guard in the extension, pool, or router corrects the `sender` to the originating EOA.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set (KYC-verified counterparties, whitelisted market makers, compliance-gated participants) provides no real restriction once the router is allowlisted. Any address can call the public router and execute swaps against the restricted pool. LP funds are exposed to the full universe of traders rather than the intended restricted set, directly contradicting the pool's access-control invariant and violating LP risk assumptions about counterparty identity. This constitutes broken core pool functionality causing potential loss of funds and an unusable/bypassable access-control flow — a High severity impact under the contest's allowed impact gate.

## Likelihood Explanation
The router is the standard user-facing entry point. A pool admin who deploys a swap-allowlisted pool and wants allowlisted users to use the router must allowlist the router address — this is the natural, expected operational step. The bypass requires no special privilege, no flash loan, and no oracle manipulation. A single call to `MetricOmmSimpleRouter.exactInputSingle` suffices. The condition is reachable in any realistic deployment of `SwapAllowlistExtension` that also supports router-mediated trading.

## Recommendation
The extension must gate the end user, not the immediate caller of `pool.swap()`. The simplest correct fix: the router appends `abi.encode(msg.sender)` to `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `extensionData` is non-empty, falling back to `sender` for direct pool calls. Alternatively, enforce the allowlist at the router level before calling the pool, so the pool-level extension only needs to gate direct pool callers.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  allowedSwapper[pool][alice]  = true   // intended gated user
  allowedSwapper[pool][router] = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][bob]    = false  // explicitly excluded

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool, tokenIn: token0, zeroForOne: true, amountIn: X, ...
  })
  Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
  Pool calls _beforeSwap(sender=router, ...).
  Extension checks allowedSwapper[pool][router] → true.
  Swap executes. Bob bypasses the allowlist.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension.
  2. setAllowedToSwap(pool, alice, true); setAllowedToSwap(pool, router, true).
  3. vm.prank(bob); router.exactInputSingle(...) → assert swap succeeds.
  4. vm.prank(bob); pool.swap(...) directly → assert revert NotAllowedToSwap.
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
