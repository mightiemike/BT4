Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the immediate caller (router) instead of the end user, enabling full per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates pool swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with `msg.sender` — the immediate caller of the pool. When any swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the router is allowlisted rather than whether the end user is allowlisted. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user of that router unrestricted swap access, completely defeating the per-user allowlist.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender` to the extension:**

```solidity
// MetricOmmPool.sol line 230–240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Extension checks the pool-level `sender` (the router), not the end user:**

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

**Router calls `pool.swap` directly, making itself `msg.sender` from the pool's perspective:**

```solidity
// MetricOmmSimpleRouter.sol line 72–80
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

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured, intending to restrict swaps to a set of KYC'd addresses.
2. Admin calls `setAllowedToSwap(pool, routerAddress, true)` to allow router-mediated swaps for their approved users.
3. Any unprivileged user — not in the allowlist — calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The router calls `pool.swap(...)`, so `msg.sender` inside the pool is the router.
5. `_beforeSwap` passes the router address as `sender` to the extension.
6. The extension evaluates `allowedSwapper[pool][router] == true` and permits the swap.
7. The end user's address is never checked; the per-user allowlist is fully bypassed.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` directly. [4](#0-3) 

No existing guard prevents this: the extension has no mechanism to recover the originating EOA from `extensionData`, and the router passes no such data by default. [2](#0-1) 

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to approved addresses per pool. The bypass allows any unprivileged user to execute swaps in a restricted pool simply by routing through `MetricOmmSimpleRouter`, rendering the allowlist entirely ineffective for router-mediated swaps. This breaks a core pool access-control invariant: the admin-set allowlist boundary is bypassed by an unprivileged path (any router caller), matching the "admin-boundary break" allowed impact category.

## Likelihood Explanation
The condition requires only that the pool admin has allowlisted the router address — a natural and expected action for any pool that intends to support router-mediated trading. Once that single admin action is taken, the bypass is unconditional, requires no special permissions, and is repeatable by any address. The attacker needs only to call a public router function.

## Recommendation
The extension must verify the true end user, not the immediate pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` before calling `pool.swap`, and have the extension decode and verify that address. This requires a trusted encoding convention.
2. **Check `tx.origin` as a fallback** (only if the threat model accepts EOA-only callers): replace `sender` with `tx.origin` inside `beforeSwap`. This is fragile and generally discouraged.
3. **Preferred — router-level allowlist**: Deploy a separate allowlist check inside the router itself, so the router rejects non-allowlisted callers before forwarding to the pool. The pool-level extension then only needs to allowlist the router.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin calls setAllowedToSwap(pool, address(router), true).
//    (alice and bob are NOT individually allowlisted)
// 3. Alice (not allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool:            address(pool),
    recipient:       alice,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// Expected: revert NotAllowedToSwap (alice not in allowlist)
// Actual:   swap succeeds — extension sees router address, which IS allowlisted
```

A Foundry integration test can confirm this by asserting the swap succeeds for an address not present in `allowedSwapper[pool]`, provided the router address is allowlisted.

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
