Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating user. A pool admin who allowlists the router (the necessary step to let allowlisted users trade via the router) simultaneously grants every non-allowlisted user the ability to bypass the allowlist entirely by routing through the router.

## Finding Description
**Call chain:**

1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly — `msg.sender` to the pool is the router address. (`metric-periphery/contracts/MetricOmmSimpleRouter.sol` L72–80)

2. `MetricOmmPool.swap` invokes `_beforeSwap(msg.sender, ...)`, passing the router as `sender`. (`metric-core/contracts/MetricOmmPool.sol` L230–231)

3. `ExtensionCalling._beforeSwap` encodes `sender` (= router) into the extension call payload. (`metric-core/contracts/ExtensionCalling.sol` L160–176)

4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37)

**The dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router — broken UX |
| **Allowlist the router** | Any non-allowlisted user bypasses the allowlist via the router — broken security |

There is no safe configuration. The extension ignores `extensionData` entirely and has no mechanism to recover the actual originating user's identity.

**Existing guards are insufficient:** A direct `pool.swap()` call by a non-allowlisted user correctly reverts with `NotAllowedToSwap` because `allowedSwapper[pool][bob]` is `false`. The router path silently bypasses this check because `allowedSwapper[pool][router]` is `true`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to trusted counterparties (KYC'd institutions, whitelisted market makers, or partners) loses that protection entirely for router-mediated swaps. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the pool and the guard passes because the router is allowlisted. Unauthorized traders with adverse information or MEV intent can drain LP value from a pool explicitly designed to exclude them. This is a broken core pool functionality and admin-boundary break: the pool admin's own reasonable configuration step (allowlisting the router) is the trigger that opens the bypass. The invariant stated in the contest materials is violated: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."*

Severity: **High** — direct loss of LP principal through unauthorized trading on curated pools, no extensive external conditions required beyond the pool admin's natural configuration.

## Likelihood Explanation
The router (`MetricOmmSimpleRouter`) is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the expected, natural configuration step. Once done, the bypass is immediately available to all users with no further privileged action required. The attacker needs only to call the public router with the target pool address.

## Recommendation
The extension must check the **actual user's identity**, not the intermediary router's address. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it when `sender` is a recognized router. This requires a trusted encoding convention between the router and the extension.
2. **Router registry in the extension:** The extension maintains a registry of trusted routers and, when `sender` is a known router, falls back to checking a user identity embedded in `extensionData`.

The simplest safe fix is to have the router always encode the originating user in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a recognized router address.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed trader.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
8. Bob trades on a pool he was explicitly excluded from.

A direct call `pool.swap(...)` by Bob correctly reverts with `NotAllowedToSwap` because `allowedSwapper[pool][bob]` is `false`. The router path silently bypasses this check.

**Foundry test plan:**
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. setAllowedToSwap(pool, alice, true)
// 3. setAllowedToSwap(pool, address(router), true)
// 4. vm.prank(bob); router.exactInputSingle(...)
// 5. Assert swap succeeds (bob bypassed allowlist)
// 6. vm.prank(bob); pool.swap(...) directly
// 7. vm.expectRevert(NotAllowedToSwap) — direct call correctly blocked
```