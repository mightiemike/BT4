Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper on Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the direct caller of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any non-allowlisted user can bypass a curated pool's swap restriction by routing through the public router, completely defeating the access control for the primary public entry point.

## Finding Description

The full call chain is confirmed by the production code:

**Step 1 — Router calls pool with itself as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly with no `sender` override: [1](#0-0) 

**Step 2 — Pool passes its own `msg.sender` (= router) as `sender` to the hook:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [2](#0-1) 

**Step 3 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension:** [3](#0-2) 

**Step 4 — Extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`:** [4](#0-3) 

If the pool operator allowlists the router (a natural setup so that allowlisted users can trade via the standard periphery), the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including those explicitly not on the allowlist. The existing guard is structurally insufficient: it correctly identifies the pool as `msg.sender` but uses the wrong actor (`router`) as the swapper identity.

The same structural flaw exists in `DepositAllowlistExtension.beforeAddLiquidity`, which checks `owner` passed by the pool — the `MetricOmmPoolLiquidityAdder` can separate payer from owner, creating an analogous bypass path: [5](#0-4) 

## Impact Explanation

A pool operator deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers). The operator allowlists the router so that allowlisted users can trade through the standard periphery. Because the extension checks `sender = router` for all router-mediated swaps, any user — including those explicitly excluded — can bypass the restriction by calling `MetricOmmSimpleRouter` instead of the pool directly. The curated pool's access control is entirely defeated for the router path. Disallowed counterparties can trade against LP positions in pools designed to be restricted, exposing LPs to adverse selection or regulatory risk they explicitly opted out of. This is a direct policy bypass with fund-impacting consequences meeting the "Admin-boundary break" and "Broken core pool functionality" impact criteria.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special privilege, flash loan, or multi-step setup is required — any EOA or contract can call it.
- The bypass is deterministic and repeatable on every swap.
- Pool operators have no on-chain mechanism to prevent router-mediated swaps without removing the router from the allowlist entirely, which would also block legitimate allowlisted users who prefer the router path.
- The precondition (router allowlisted) is the natural and expected operator configuration.

## Recommendation

The pool must forward the **original initiating user** as `sender` to the extension, not its own `msg.sender`. Two standard approaches:

1. **Caller-forwarding in the router**: `MetricOmmSimpleRouter` passes `msg.sender` (the actual user) explicitly as a dedicated `sender` argument when calling `pool.swap`, and the pool propagates this value to the extension hook unchanged. This requires a pool interface change.
2. **Extension-side transient context**: Use EIP-1153 transient storage (already used elsewhere in the protocol via `MetricReentrancyGuardTransient`) to record the original caller at the router entry point and read it inside the extension hook, bypassing the `sender` parameter entirely.

Either approach must be applied consistently to `DepositAllowlistExtension.beforeAddLiquidity` for the analogous `MetricOmmPoolLiquidityAdder` path.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  allowedSwapper[pool][router] = true   // operator allowlists router for convenience
  allowedSwapper[pool][alice]  = true   // alice is a legitimate allowlisted user
  allowedSwapper[pool][bob]    = false  // bob is explicitly NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...) — router is msg.sender to the pool
  3. Pool calls _beforeSwap(msg.sender=router, ...) → ExtensionCalling forwards sender=router
  4. SwapAllowlistExtension.beforeSwap checks: allowedSwapper[pool][router] == true → passes
  5. Swap executes; bob receives output tokens

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — bob bypasses the allowlist entirely

Foundry test outline:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, router, true)
  - setAllowedToSwap(pool, bob, false)
  - vm.prank(bob); router.exactInputSingle(...)
  - Assert: swap succeeds (no revert) — confirms bypass
```

### Citations

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
