Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the value forwarded by the pool — which is the pool's own `msg.sender` (the direct caller of `pool.swap`). When `MetricOmmSimpleRouter` is used, that direct caller is the router, not the originating user. A pool admin who allowlists the router to enable router-mediated access for their intended users inadvertently opens the pool to every address that calls through the router, fully bypassing the per-pool swap allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is the direct caller of `pool.swap`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

So the allowlist lookup becomes `allowedSwapper[pool][router]`. A pool admin who wants their allowlisted users to be able to use the router must add the router address to the allowlist. Once `allowedSwapper[pool][router] == true`, the check passes for **every** caller who routes through the router, regardless of whether that caller is on the intended allowlist. The same substitution applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all hops are called by the router, so `sender` is always the router address.

## Impact Explanation
A pool restricted by `SwapAllowlistExtension` is deployed to limit trading to trusted counterparties (e.g., KYC'd market makers, whitelisted integrators) to prevent adverse selection against LPs. Once the router is allowlisted, any unprivileged address can execute swaps on the restricted pool by calling through the router. This exposes LP capital to unrestricted adverse-selection flow, directly eroding LP principal — a medium-to-high direct loss of LP assets, matching the allowed impact gate for direct loss of user principal/owed LP assets.

## Likelihood Explanation
The bypass requires the pool admin to have added the router to the allowlist. This is a natural and expected operational step: a pool admin who deploys a restricted pool and also wants their allowlisted users to access it via the standard router will allowlist the router address. The misconfiguration is not obvious because the admin believes they are enabling router access for their specific users, not for all users. The router is a public, permissionless contract callable by any address. No special privileges are required for the attacker.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should gate on the **original user**, not the intermediate caller. Options:

1. **Pass the originating user through the router via `extensionData`.** The router already stores the real payer in transient storage (`_getPayer()`). The router could encode the originating `msg.sender` into `extensionData`, and the extension could decode and verify it (trusting the pool as the source of `msg.sender`).
2. **Require the pool to pass the original `msg.sender` through a trusted forwarding mechanism.** The cleanest fix is a protocol-level convention where the router passes the originating user as a verified field in `extensionData`, and the extension decodes and checks it.
3. **Document that allowlisting the router is equivalent to `allowAllSwappers`.** If the design intent is that the router is always open, the allowlist should explicitly warn admins that adding the router address opens the pool to all users.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // intends to allow only userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // intends to let userA use the router
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: restrictedPool,
           recipient: userB,
           ...
       })
5. Router calls pool.swap(recipient=userB, ...) — msg.sender to pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] → true
8. Swap executes successfully for userB despite not being on the allowlist.
```

`userB` receives output tokens from a pool intended to be restricted to `userA` only. The allowlist guard is fully bypassed.