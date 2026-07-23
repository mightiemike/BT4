Audit Report

## Title
`SwapAllowlistExtension` checks the router's address instead of the originating EOA, allowing any router user to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` to the pool — the router contract address when a user routes through `MetricOmmSimpleRouter`. Once a pool admin allowlists the router (the only way to enable router-mediated swaps for any allowlisted user), every user of the router inherits that allowance, completely defeating the per-user gate. Any unprivileged EOA can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle`.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards `sender` unchanged into the extension call:

```solidity
// ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that forwarded `sender`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called the pool. When a user routes through `MetricOmmSimpleRouter`, the router calls `pool.swap(params.recipient, ...)` directly with no mechanism to inject the originating EOA:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

This creates an inescapable catch-22: if the pool admin wants any allowlisted user to swap via the router, they must call `setAllowedToSwap(pool, router, true)`. But doing so sets `allowedSwapper[pool][router] = true`, which passes the check for every caller of the router — not just the intended allowlisted users. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user_eoa]`. No existing guard in the router, pool, or extension corrects for this identity mismatch.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists alice: `setAllowedToSwap(pool, alice, true)`.
2. Admin allowlists the router so alice can use normal UX: `setAllowedToSwap(pool, router, true)`.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Bob's swap executes on the restricted pool.

## Impact Explanation
`SwapAllowlistExtension` is the protocol's primary mechanism for restricting swap access on a per-pool basis. Pools may be restricted for regulatory compliance, LP protection, or guarded launches. Once the router is allowlisted — a necessary admin action to enable any router-mediated swap for allowlisted users — the gate is fully open to every user of the router. Non-allowlisted actors can execute arbitrary swaps against the pool, exposing LPs to trades the pool admin explicitly intended to block. If the restriction was meant to exclude value-extracting actors (e.g., arbitrageurs), LP principal is directly at risk. This constitutes broken core pool functionality causing potential loss of LP assets.

## Likelihood Explanation
The router is a standard, publicly deployed periphery contract. Any pool admin who wants allowlisted users to have a normal swap UX must allowlist the router, triggering the bypass. No special privilege, flash loan, or contract deployment is required — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`. The bypass is silent: the extension emits no event and the swap completes normally, giving the pool admin no on-chain signal that the gate was circumvented. The condition is expected to arise in normal protocol operation.

## Recommendation
The extension must check the identity of the economic actor, not the intermediary. Two options:

1. **Document incompatibility and prohibit router allowlisting** — `SwapAllowlistExtension` should document that it gates `msg.sender` to the pool, and pool admins must only allowlist individual EOAs that call the pool directly. The router must never be allowlisted on pools using this extension.
2. **Pass the originating user through `extensionData`** — the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it against the allowlist. This requires the extension to trust the router's encoding and introduces a trust assumption on the router contract.

The cleanest fix is option 1, paired with a `require` or NatSpec warning in `setAllowedToSwap` that rejects known router addresses, or a separate `beforeSwap` implementation that explicitly rejects non-EOA senders.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the only intended swapper
  allowedSwapper[pool][router] = true   // admin allowlists router for alice's UX

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool:      pool,
        recipient: bob,
        zeroForOne: true,
        amountIn:  X,
        ...
    })

  pool.swap(bob, true, X, ...) is called with msg.sender = router
  _beforeSwap(router, bob, ...) is dispatched
  SwapAllowlistExtension.beforeSwap(router, bob, ...) checks:
    allowedSwapper[pool][router] == true  → passes
  bob's swap executes on the restricted pool
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension`.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. `vm.prank(bob)` → `router.exactInputSingle(...)` → assert swap succeeds (no `NotAllowedToSwap` revert).
4. `vm.prank(bob)` → `pool.swap(...)` directly → assert reverts with `NotAllowedToSwap`.