Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension receives `sender = address(router)`. If the pool admin allowlists the router to enable allowlisted users to access the pool via the standard periphery, every non-allowlisted user gains identical access by calling the same public router, fully neutralising the allowlist.

## Finding Description
**Step 1:** `MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`:
```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(msg.sender, recipient, zeroForOne, amountSpecified, ...);
```

**Step 2:** `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder` (lines 149–177 of `ExtensionCalling.sol`).

**Step 3:** `SwapAllowlistExtension.beforeSwap` evaluates:
```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address when called via the router.

**Step 4:** `MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap` directly:
```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ...);
```
This makes the router the `msg.sender` the pool observes, so `sender` forwarded to the extension is `address(router)`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — intended allowlisted trader.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary for `userA` to use the router.
4. Non-allowlisted `userB` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)`; pool's `msg.sender = router`.
6. `_beforeSwap(sender=router, ...)` dispatched to `SwapAllowlistExtension`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → no revert.
8. `userB`'s swap executes against the pool's LP capital; allowlist is fully bypassed.

No existing guard prevents this: the extension has no mechanism to distinguish the router from the real economic actor, and the router provides no attestation of the originating caller.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties). Once the router is allowlisted — a routine operational step required to let allowlisted users access the pool through the standard periphery — any unprivileged address can execute swaps against the pool's liquidity at oracle-derived prices. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's allowlist policy is rendered entirely ineffective by an unprivileged path through the public router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the documented standard periphery for end-user swaps. A pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to use the router will naturally add the router to the allowlist — the extension provides no warning that doing so opens the pool to all callers. The precondition is a routine operational step, not an exotic configuration, making exploitation straightforward and repeatable by any address.

## Recommendation
The extension must gate the economic actor (the wallet that initiated the transaction), not the intermediate contract that called `pool.swap`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks that value. Requires coordinated changes to the router and extension.
2. **Use `tx.origin` as a fallback check**: Only acceptable if the extension explicitly documents that it is designed for EOA-only contexts and the pool admin accepts that limitation.
3. **Separate trusted-forwarder tier**: Allowlist the router as a trusted forwarder and require the router to attest the real caller inside `extensionData`, which the extension then verifies.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension as a configured hook.
2. Pool admin calls setAllowedToSwap(pool, userA, true).
3. Pool admin calls setAllowedToSwap(pool, router, true) — required for userA to use the router.
4. Non-allowlisted userB calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(...); pool's msg.sender = router.
6. _beforeSwap(sender=router, ...) is dispatched to SwapAllowlistExtension.
7. Extension evaluates: allowedSwapper[pool][router] == true → no revert.
8. userB's swap executes at oracle price against the pool's LP capital.
   userA's allowlist protection is completely bypassed.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `userA` and `router`, call `exactInputSingle` from `userB`, assert no revert and swap succeeds.