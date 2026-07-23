Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` evaluates the router's address instead of the end-user's address, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the router contract — not the originating user. When a pool admin allowlists the router to permit router-mediated swaps, every unprivileged address on-chain can bypass the per-user curation by calling `MetricOmmSimpleRouter.exactInputSingle`. Conversely, allowlisted EOAs cannot swap through the router at all because the router's address is not in the allowlist.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router, not the end user
```

`SwapAllowlistExtension.beforeSwap` then evaluates that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (used as the mapping key) and `sender` is the router address when the call originates from `MetricOmmSimpleRouter.exactInputSingle`:

```solidity
// MetricOmmSimpleRouter.sol:71-80
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

The router stores the real user in transient storage via `_setNextCallbackContext` (for payment purposes) but never surfaces it to the pool or extension. The pool therefore passes the router's address as `sender`, and the extension evaluates `allowedSwapper[pool][router]` — a slot that is either unset (blocking all router users, including legitimately allowlisted ones) or set to `true` by the admin (allowing every address on-chain to bypass the restriction). No existing guard corrects this mismatch.

## Impact Explanation

**Allowlist bypass (high):** A pool admin who calls `setAllowedToSwap(pool, routerAddress, true)` to enable router-mediated swaps inadvertently opens the pool to every address on-chain. Because `MetricOmmSimpleRouter` is a public, permissionless contract, any non-allowlisted EOA can call `exactInputSingle` and pass the extension check. The per-user curation is completely nullified; LP principal is exposed to unauthorized swaps.

**Broken core functionality (medium):** A pool admin who allowlists specific EOAs finds those users cannot swap through the router (the router is not in the allowlist). Allowlisted users must implement `IMetricOmmSwapCallback` themselves and call `pool.swap()` directly, which is not the intended UX and renders the pool effectively unusable for normal participants.

Both outcomes represent a direct break of the pool's intended access-control invariant and, in the bypass case, direct loss of LP principal through unauthorized swaps — matching the contest's "broken core pool functionality" and "admin-boundary break" impact categories.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter this mismatch on the very first router-mediated swap. The bypass path requires no special timing, no privileged access, and no exotic token behavior — any address with a standard ERC-20 approval can exploit it the moment the router is allowlisted. The broken-functionality path manifests immediately for any allowlisted EOA who attempts to use the router.

## Recommendation

The extension must resolve the actual end-user identity rather than the immediate pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router via `extensionData`.** The router already stores the payer in transient storage (`_getPayer()`). The router could encode the real user address in a standardized `extensionData` envelope that the extension decodes and verifies (checking that `msg.sender` in the extension is a known, trusted router before trusting the embedded address).

2. **Expose a `swapInitiator()` view on the pool during the swap action.** The pool could store the original initiator in transient storage at the start of `swap()` and expose it to extensions, allowing the extension to read the true originator regardless of intermediary contracts.

Option 1 is the more practical near-term fix given the existing transient-storage infrastructure in the router.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, routerAddress, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attackerEOA, true).

Attack:
  1. Attacker (non-allowlisted EOA) calls
     MetricOmmSimpleRouter.exactInputSingle({pool, ...}).
  2. Router calls pool.swap(); pool's msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes; attacker receives output tokens.

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool.
  - LP funds are exposed to any on-chain address via the public router.

Foundry test outline:
  1. Deploy pool + SwapAllowlistExtension; configure extension on beforeSwap.
  2. vm.prank(poolAdmin); extension.setAllowedToSwap(pool, router, true).
  3. vm.prank(attacker); router.exactInputSingle({pool, ...}).
  4. Assert swap succeeds (no NotAllowedToSwap revert) and attacker receives tokens.
  5. Assert allowedSwapper[pool][attacker] == false (confirming bypass).
```