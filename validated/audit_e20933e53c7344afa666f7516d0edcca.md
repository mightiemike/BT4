Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Economic Actor, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates, it calls `pool.swap()` directly, making the router the `msg.sender` of that call and therefore the `sender` checked by the extension. Any pool admin who allowlists the router to enable approved users to trade via the standard periphery simultaneously opens the pool to every unprivileged user who routes through the same router contract.

## Finding Description

**Root cause — wrong actor binding in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (correct key) and `sender` is the first argument forwarded by the pool. The extension assumes `sender` is the economic actor initiating the swap, but this assumption breaks when a router intermediates.

**Call chain:**

1. `MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:
   ```solidity
   _beforeSwap(msg.sender, recipient, zeroForOne, ...);
   ```
   (`metric-core/contracts/MetricOmmPool.sol`, line 231)

2. `ExtensionCalling._beforeSwap` encodes this value verbatim and forwards it to every configured extension via `_callExtensionsInOrder`.

3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — making the router the `msg.sender` of that call:
   ```solidity
   _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
   (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool).swap(...);
   ```
   (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, lines 71–80)

4. The same pattern applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165).

**Result:** The extension evaluates `allowedSwapper[pool][router]`. If the router is allowlisted — the only way to let any approved user trade via the router — every unprivileged user who calls the router bypasses the per-user restriction.

**Existing guards are insufficient:** There is no mechanism in the extension to distinguish between a direct swap by an allowlisted address and a router-mediated swap on behalf of an arbitrary user. The `extensionData` field is passed through but the extension does not read it.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The unauthorized user receives the pool's output tokens and pays the input tokens exactly as an approved user would, at the live oracle price. This is a direct violation of the pool's curation policy and, depending on the pool's purpose, a direct loss of LP value if the pool was designed to trade only with specific counterparties. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation

The bypass requires the router to be allowlisted on the pool. A pool admin who wants their approved users to be able to use the standard periphery router must allowlist the router — there is no other supported path. The moment the router is allowlisted, the allowlist is effectively open to all users of the router. This is not a hypothetical misconfiguration; it is the only configuration that makes the extension compatible with the router. Any production pool that uses `SwapAllowlistExtension` and also supports router-based swaps is vulnerable. The attack requires no special privileges, no capital beyond the swap input, and is repeatable indefinitely.

## Recommendation

The extension must check the economic actor, not the direct caller of `pool.swap()`. Two concrete approaches:

1. **Router-forwarded user identity via `extensionData`:** The router encodes the original `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes this field and, when `sender` is a known trusted router, checks `allowedSwapper[pool][decodedUser]` instead. This requires a trust registry of approved routers in the extension.

2. **Transient storage proof:** The router writes the real user into a transient storage slot before calling `pool.swap()`. The extension reads that slot when `sender` is a known router. This avoids ABI-encoding overhead and is tamper-resistant within the same transaction.

3. **Documentation + separate extension:** Document that `SwapAllowlistExtension` is incompatible with the router for per-user restrictions, and provide a dedicated extension that reads the real user from a router-specific transient storage slot set before the pool call.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  allowedUser  = address allowlisted: allowedSwapper[pool][allowedUser] = true
  attackerUser = address NOT allowlisted

Step 1 (pool admin, legitimate):
  swapExtension.setAllowedToSwap(pool, router, true)
  // Admin allowlists the router so allowedUser can trade via the router.

Step 2 (attacker):
  router.exactInputSingle({
      pool:       pool,
      recipient:  attackerUser,
      zeroForOne: true,
      amountIn:   X,
      ...
  })
  // Router calls pool.swap() with msg.sender = router.
  // Pool calls _beforeSwap(sender=router, ...).
  // Extension checks allowedSwapper[pool][router] → true → passes.
  // Swap executes. Attacker receives output tokens.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; attacker bypasses the curated allowlist.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` as `beforeSwap` hook.
2. Call `setAllowedToSwap(pool, router, true)` from pool admin.
3. Call `router.exactInputSingle` from an address not in `allowedSwapper`.
4. Assert the call succeeds and the attacker receives output tokens.
5. Assert a direct `pool.swap()` call from the same attacker address reverts with `NotAllowedToSwap`.