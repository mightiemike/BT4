Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing any unprivileged caller to bypass per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps using `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to support standard periphery swaps, every user on the network can bypass the curated allowlist by routing through the router — the extension provides zero per-user access control for router-mediated swaps.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — The extension checks `allowedSwapper[pool][sender]`.**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the first argument, i.e., the pool's `msg.sender`) as the identity to gate: [2](#0-1) 

**Step 3 — The router calls `pool.swap()` directly, so `msg.sender` in the pool is the router.**

`exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` from within the router: [3](#0-2) 

For multi-hop `exactInput`, every hop is called from the router: [4](#0-3) 

**Result:** The extension always sees `sender = router_address` for every router-mediated swap. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The wrong value gated is the `sender` identity passed to `beforeSwap` — it is the router contract address rather than the originating EOA or contract.

## Impact Explanation

The pool admin faces an impossible configuration choice. If the router is NOT allowlisted, allowlisted users cannot use the standard periphery path. If the router IS allowlisted (the natural configuration to support router-based swaps), every user on the network can bypass the allowlist by calling any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router. This is a direct admin-boundary break: an unprivileged trader bypasses the pool admin's explicit access-control policy, enabling unauthorized swaps in curated pools (e.g., KYC-gated or institutionally restricted pools). Unauthorized users can execute swaps, interact with pools they were explicitly excluded from, and drain liquidity at oracle prices — constituting a fund-impacting policy bypass.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin deploying `SwapAllowlistExtension` to restrict access will naturally also want allowlisted users to use the standard router, making allowlisting the router the obvious and expected configuration step. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can exploit it by calling `exactInputSingle` on the router with a pool address that has the extension configured.

## Recommendation

The extension must gate the **originating user**, not the immediate pool caller. Options:

1. **Pass the original user through the router via `extensionData`.** The router forwards the original `msg.sender` as an authenticated field inside `extensionData`; the extension decodes and verifies it, requiring a trust relationship between the extension and a known router address.
2. **Restrict `SwapAllowlistExtension` to direct pool calls only** and document that it is incompatible with `MetricOmmSimpleRouter`. Require allowlisted users to call `pool.swap()` directly. This is the safest short-term fix.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // enable router support

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls IMetricOmmPoolActions(pool).swap(recipient, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes successfully despite not being allowlisted

Wrong value: allowedSwapper[pool][router] is checked instead of allowedSwapper[pool][bob]
Result: Bob trades in a curated pool he was explicitly excluded from;
        SwapAllowlistExtension provides zero per-user protection for router-mediated swaps.
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
