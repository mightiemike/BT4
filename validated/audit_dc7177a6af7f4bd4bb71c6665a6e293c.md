Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap(...)`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-based swaps on a curated pool inadvertently opens the gate for every user, regardless of their individual allowlist status.

## Finding Description

**Root cause — pool passes its own `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap(), not the end user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as `sender` to every configured extension: [2](#0-1) 

**The extension checks `sender` (the router), not the real user:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct key) and `sender` is the router address when the call originates from `MetricOmmSimpleRouter`.

**The router calls the pool directly without forwarding the real user identity:**

`exactInputSingle` stores `msg.sender` only in transient storage for the payment callback, but passes nothing to the pool that identifies the real user:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The same substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`/`_exactOutputIterateCallback`: [5](#0-4) [6](#0-5) [7](#0-6) 

**Exploit flow:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` attached.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Non-allowlisted `alice` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Alice's swap executes on the curated pool despite not being individually allowlisted.

No existing guard recovers the real user identity. The transient-storage payer context is consumed only inside `metricOmmSwapCallback` for payment, never surfaced to the extension.

## Impact Explanation

The swap allowlist invariant — that only explicitly approved addresses may trade on a curated pool — is silently broken for every non-allowlisted user who routes through `MetricOmmSimpleRouter`. This constitutes a broken core pool functionality (the curation policy is unenforceable on the canonical public swap path) and an admin-boundary break: the pool admin's access control is bypassed by an unprivileged trader without any special setup beyond using the public router. LPs who deposited into a curated pool expecting restricted counterparties are exposed to unrestricted trading against their positions.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, documented public swap entry point.
- Any pool admin who wants router-based swaps to function on a curated pool must allowlist the router — this is the natural, expected configuration.
- No privileged access, no malicious setup, and no non-standard token behavior is required.
- The bypass is unconditional once the router is allowlisted: any address can call the router.
- All four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are affected.

## Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediate router. The cleanest fix is to thread the original initiator through the call stack:

1. Add an explicit `originator` parameter to `IMetricOmmPoolActions.swap` that the router populates from its own `msg.sender`.
2. Have `MetricOmmPool.swap` forward `originator` (not `msg.sender`) as `sender` to `_beforeSwap`.
3. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][originator]`, which is the real user.

Alternatively, require that router-mediated swaps encode the real caller inside `extensionData` and verify it in the extension — but this is fragile and non-standard compared to option 1.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, router, true)
  // alice is NOT allowlisted

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: alice,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → Router calls pool.swap(alice, true, X, ...)   [msg.sender to pool = router]
  → Pool calls _beforeSwap(sender=router, ...)
  → Extension: allowedSwapper[pool][router] == true  → no revert
  → Swap executes

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds for non-allowlisted alice
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. `vm.prank(admin); extension.setAllowedToSwap(pool, address(router), true)`.
3. Confirm `extension.isAllowedToSwap(pool, alice) == false`.
4. `vm.prank(alice); router.exactInputSingle(...)` — assert it does **not** revert.
5. Assert alice's swap settled, demonstrating the bypass.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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
