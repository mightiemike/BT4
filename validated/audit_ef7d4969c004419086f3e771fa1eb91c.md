Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass Per-Pool Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, where `sender` is whoever called `pool.swap()` directly. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating EOA. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently grants swap access to every user who calls the router, completely nullifying the allowlist restriction.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmPool.swap` passes its own `msg.sender` (the direct caller of `pool.swap`) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

From the pool's perspective, `msg.sender` is the **router**, not the originating EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:
- If the router is **not** allowlisted: allowlisted users cannot use the router at all.
- If the router **is** allowlisted: every user can bypass the allowlist via the router.

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`, all of which call `pool.swap()` directly with the router as `msg.sender`.

## Impact Explanation
Any unprivileged user can bypass a `SwapAllowlistExtension`-gated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router swap function). The allowlist is intended to restrict who may trade — for compliance, risk management, or exclusive-access pools. With the router allowlisted, the restriction is completely nullified. Users who should be blocked can execute swaps, draining pool liquidity at oracle-anchored prices that were only intended for approved counterparties. This constitutes a direct loss of user principal and broken core pool functionality (allowlist-gated swap access).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract callable by any EOA. A pool admin who wants router-mediated swaps for their allowlisted users **must** allowlist the router — there is no other mechanism in the current codebase. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. Any EOA can trigger it by calling the router with a valid swap path.

## Recommendation
The extension must check the **originating user**, not the direct pool caller. The cleanest production fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that field when `sender` is a known router address. Alternatively, the pool could accept a dedicated `originator` field in the swap call that the router populates with `msg.sender`, and the extension checks `originator` instead of `sender`. Using `tx.origin` is acceptable only for EOA-only pools but is generally discouraged.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be approved for alice to use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(...) → msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true
  - bob's swap executes successfully despite not being on the allowlist

Result:
  - bob trades against a pool restricted to approved counterparties
  - The allowlist invariant is broken; any user with router access can trade
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
