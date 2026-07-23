Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (the only way to permit router-mediated swaps for legitimate users) simultaneously opens the gate to every address on the network.

## Finding Description

**Call path:**

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks that forwarded `sender` against its per-pool mapping: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At this point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, never touching the actual user's address.

**Why existing guards fail:**

There is no secondary check on the original caller. The extension has no awareness of transient storage, `tx.origin`, or any router-injected identity. The `allowAllSwappers` short-circuit only widens the gate further. The `DepositAllowlistExtension` avoids this flaw because it gates on `owner` — the economic actor explicitly passed as a separate argument by the pool — not on `sender`: [5](#0-4) 

No analogous separation exists for the swap path.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd or institutional participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized swappers can interact with pools that were contractually or regulatorily required to be closed to them, drain LP positions at oracle prices, and extract value from restricted pools. This is a direct broken core pool invariant (access control) with potential for direct loss of LP assets — matching the "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path" allowed impacts.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap entry point. Any pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)` — there is no other mechanism. The moment that entry is added, the bypass is live for every address. No special privilege, flash loan, or unusual token behavior is required; a standard `exactInputSingle` call suffices. The trigger is automatic and repeatable.

## Recommendation

The extension must recover the **original user** from the call context. Two concrete options:

1. **Router encodes initiator in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between the router and the extension, and the extension must verify the call came from a trusted router before trusting the encoded identity.

2. **Separate router allowlist with authenticated user identity**: Introduce a mapping `allowedRouter` and, when `sender` is a known router, require `extensionData` to carry a signed or otherwise authenticated user identity that the extension verifies independently.

The simplest safe default is to treat any call whose `sender` is not in `allowedSwapper` as blocked, and document that router-mediated swaps are unsupported without an authenticated identity forwarding mechanism.

## Proof of Concept

```
Setup:
  - Pool P with SwapAllowlistExtension E
  - Admin: setAllowedToSwap(P, alice, true)      // alice is KYC'd
  - Admin: setAllowedToSwap(P, router, true)     // required for alice to use the router
  - bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, zeroForOne, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[P][router] == true  ✓
    → swap executes; bob receives output tokens

Result:
  bob, an explicitly excluded address, successfully swaps against the
  restricted pool. The allowlist invariant is broken.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist `alice` and the router, then call `exactInputSingle` from `bob` (unlisted) and assert the swap succeeds — confirming the bypass.

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
