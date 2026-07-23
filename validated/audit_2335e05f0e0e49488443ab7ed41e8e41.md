Audit Report

## Title
SwapAllowlistExtension Checks Immediate Pool Caller (Router) Instead of End User, Enabling Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender` — the immediate caller of the pool. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the pool to every user of the router, defeating the allowlist entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct mapping key) and `sender` is the first argument forwarded by the pool from its own `msg.sender`. `MetricOmmPool.swap` passes `msg.sender` directly as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes and forwards this `sender` value unchanged to the extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls the pool, the router is `msg.sender` to the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

So the extension receives `sender = address(router)`, not the end user's address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

The `FullMetricExtensionTest` confirms this binding: the test allowlists `callers[0]` (the `TestCaller` wrapper contract — the immediate pool caller), not `users[0]` (the human address):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [5](#0-4) 

No existing guard in `SwapAllowlistExtension` or `MetricOmmPool` recovers the originating user's address; the pool has no mechanism to pass the true end-user identity through the router to the extension.

## Impact Explanation
A pool admin who deploys a curated pool (KYC-only, institution-only, or counterparty-restricted) with `SwapAllowlistExtension` and allowlists the official router so that their curated users can use standard periphery simultaneously opens the pool to every caller of the router's public entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). The extension's `allowedSwapper[pool][router] == true` check passes for all of them. This constitutes an admin-boundary break: an unprivileged, non-allowlisted user bypasses the pool admin's intended access control via a public periphery path, enabling unauthorized trading on a pool designed to restrict counterparties. If the pool's curation purpose was to limit adverse selection or enforce compliance, LP principal is exposed to counterparties the admin explicitly excluded.

## Likelihood Explanation
The scenario requires no privileged access, no malicious token, and no non-standard ERC20 behavior. The only precondition is that the pool admin allowlists the router — a natural, expected action for any admin who wants their allowlisted users to use the standard periphery. The router is a public, permissionless contract. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the swap will succeed because the router is allowlisted. The attack is repeatable and requires no setup beyond the admin's own configuration.

## Recommendation
The router must forward the originating caller's identity to the pool so the extension can gate the correct actor. One approach: use the existing transient-storage pattern (analogous to `_setNextCallbackContext` which already stores the payer in transient storage) to communicate the originating user to the extension — the extension would read the true end-user address from a router-set transient slot rather than relying on the `sender` argument. Alternatively, add a `sender` override parameter to the router's swap functions and pass the originating `msg.sender` as the first argument to `pool.swap`. A third option is to document explicitly that `SwapAllowlistExtension` gates only the immediate pool caller and that router-mediated swaps require a separate per-user allowlist mechanism at the router layer, preventing admins from allowlisting the router under the mistaken belief that per-user curation is preserved.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, router, true)   // to support router-mediated swaps for curated users
3. Admin does NOT call setAllowedToSwap(pool, attacker, true)
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)           // router is msg.sender to pool
   → pool calls _beforeSwap(router, ...)              // sender = router address
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
   → swap succeeds
5. Attacker has traded on a pool they were never individually allowlisted for.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L70-73)
```text
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
```
