Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper on every `MetricOmmSimpleRouter`-mediated swap, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`. When any swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the end user. `SwapAllowlistExtension.beforeSwap` therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`, making the curation gate either fully bypassable (if the router is allowlisted) or broken for all router users (if it is not).

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and passes it directly as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // router address when called via router
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` encodes that value unchanged and dispatches it to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(BEFORE_SWAP_ORDER, abi.encodeCall(
    IMetricOmmExtensions.beforeSwap, (sender, ...)
));
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), but `sender` is the router (wrong). The actual end-user address is never visible to the extension. This affects all four router entry points:

- `exactInputSingle` (L71-80): calls `pool.swap` directly; pool sees `msg.sender = router`.
- `exactInput` (L103-112): same for every hop.
- `exactOutputSingle` (L136-137): same.
- `exactOutput` (L165-181) and its recursive `_exactOutputIterateCallback` (L220-228): intermediate hops also call `pool.swap` from within the callback, again with `msg.sender = router`.

No existing guard in the pool, router, or extension recovers the real originator. The pool's `swap` interface provides no `originator` parameter, and the router does not encode the real user in `extensionData`.

## Impact Explanation
Two concrete fund-impacting failure modes:

**Bypass (high impact):** Admin allowlists the router as a trusted periphery contract (`setAllowedToSwap(pool, router, true)`). Any non-allowlisted user calls `router.exactInputSingle(...)`. The extension sees `sender = router`, finds it allowlisted, and permits the swap. The curation gate is fully defeated; the pool accepts trades from arbitrary counterparties the admin intended to exclude.

**Lockout (medium impact):** Admin does not allowlist the router (only individual users). Allowlisted users who call the router are blocked because the extension sees `sender = router` and finds it not allowlisted. The router — the protocol's primary swap interface — is unusable for any allowlisted pool, breaking core swap functionality.

Both modes satisfy the allowed impact gate: broken core pool swap functionality causing loss of funds or unusable swap flows.

## Likelihood Explanation
Allowlisting the router is a natural and expected admin action for a trusted periphery contract. The bypass is reachable through a normal, non-malicious admin configuration. The lockout requires no admin mistake at all — it triggers automatically the moment any allowlisted user attempts to use the router. Both paths are reachable by an unprivileged trader with no special preconditions beyond a pool configured with `SwapAllowlistExtension`.

## Recommendation
The pool's `swap` function should accept an explicit `originator` parameter that the router sets to `msg.sender` (the actual user) before calling the pool. The extension would then gate on `originator` rather than the pool's `msg.sender`. Alternatively, the router can encode the real user in `extensionData` and the extension can decode it, though this requires coordinated changes to both contracts. At minimum, `SwapAllowlistExtension` documentation must state it is incompatible with router-mediated swaps and that allowlisting the router opens the pool to all users.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls extension.setAllowedToSwap(pool, alice, true)   // alice is the intended grantee
3. Admin calls extension.setAllowedToSwap(pool, router, true)  // router added as trusted periphery
4. Attacker (bob, not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool, tokenIn: ..., tokenOut: ..., recipient: bob, ...
       })
5. pool.swap is called with msg.sender = router.
6. SwapAllowlistExtension.beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true  →  check passes.
8. Bob's swap executes; allowlist is bypassed.
```

Root cause: `MetricOmmPool.sol` L231 passes `msg.sender` as `sender`; [1](#0-0)  `SwapAllowlistExtension.sol` L37 gates on that `sender` value; [2](#0-1)  `MetricOmmSimpleRouter.sol` L72-80 and L220-228 are the unprivileged trigger paths. [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
