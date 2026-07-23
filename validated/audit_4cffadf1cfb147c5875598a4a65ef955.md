Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Direct Caller (`sender`) Instead of Actual User, Enabling Full Allowlist Bypass Through an Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which is `msg.sender` of the pool's `swap()` call — the direct caller (e.g., `MetricOmmSimpleRouter`). When a pool admin allowlists the router to permit authorized users to trade through it, every user who routes through that contract bypasses the per-user restriction, because the allowlist check resolves to the single router address rather than the individual user. This collapses a per-user guard into a per-router guard, allowing unauthorized users to execute swaps in pools intended to be private.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller, not the original user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` is called by any user, it calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is the router, so `sender = router`. The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. If the router is allowlisted, every caller of the router passes the guard regardless of their individual allowlist status.

**Contrast with `DepositAllowlistExtension`**, which correctly checks the actual beneficiary (`owner`, the second parameter) rather than the direct caller (`sender`, the first parameter):

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The `addLiquidity` path passes the actual LP position owner as a distinct `owner` argument separate from `sender`. The swap path has no equivalent distinct "actual user" field — `recipient` is the output recipient, not necessarily the initiator — so the extension has no way to recover the true user identity from the arguments it receives.

## Impact Explanation
A pool admin who deploys a restricted pool (e.g., a private institutional pool with tight bid/ask spreads) and allowlists `MetricOmmSimpleRouter` to allow authorized users to trade through it inadvertently grants swap access to every address that calls the router. Unauthorized users can:

- Execute swaps in a pool intended to be private, circumventing KYC/compliance intent encoded in the allowlist.
- If the pool offers subsidized or favorable pricing (tight spread), drain LP value at below-market rates.

LP principal is directly exposed to unauthorized counterparties at prices the LPs did not intend to offer to the general public. This constitutes a direct loss of LP assets and broken core pool access-control functionality.

## Likelihood Explanation
The trigger path requires no privileged access:

1. Pool admin deploys a pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — a natural action to allow authorized users to trade via the standard router.
3. Any unauthorized user calls `MetricOmmSimpleRouter.exactInputSingle(...)` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
4. The pool passes `sender = router` to `beforeSwap`; the router is allowlisted; the check passes.
5. Unauthorized swap executes.

This is a realistic operational scenario. The pool admin's intent (allow authorized users via router) and the actual outcome (allow all users via router) diverge silently with no on-chain warning. All four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) trigger this path.

## Recommendation
Mirror the `DepositAllowlistExtension` pattern: check the actual user, not the intermediary. The pool's `swap()` interface should expose a dedicated `swapper` field (separate from `recipient`) representing the true initiator, analogous to `owner` in the deposit path. The pool would set `swapper = msg.sender` and pass it through `_beforeSwap` as a distinct argument. `SwapAllowlistExtension.beforeSwap` would then check `allowedSwapper[pool][swapper]` instead of `allowedSwapper[pool][sender]`. At minimum, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps for per-user access control.

## Proof of Concept
```
Setup:
  - Pool configured with SwapAllowlistExtension in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow authorized users to trade via MetricOmmSimpleRouter).
  - Unauthorized user (not on allowlist) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: unauthorizedUser, ...})
        → pool.swap(recipient=unauthorizedUser, ...)   [router is msg.sender]
          → _beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ (passes)
          → swap executes for unauthorized user

Verification:
  - allowedSwapper[pool][unauthorizedUser] == false (never set)
  - But the guard passed because sender == router, which IS allowlisted.
  - Unauthorized user receives swap output; pool LPs bear the counterparty risk
    they intended to restrict.

Foundry test sketch:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, address(router), true).
  3. vm.prank(unauthorizedUser); router.exactInputSingle(...).
  4. Assert swap succeeds despite allowedSwapper[pool][unauthorizedUser] == false.
```