Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees the router as `sender`, not the actual user. If the pool admin allowlists the router to enable legitimate users to use the supported periphery path, every unprivileged user can bypass the allowlist by routing through the router, completely defeating the access control invariant.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` gates on that `sender` value:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `sender` the extension sees:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165) — all call `pool.swap()` directly.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the `sender` parameter (first arg, unnamed) and checks `owner` — the economically relevant actor:

```solidity
// DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap extension has no equivalent correction. When the admin allowlists the router so that legitimate users can use the supported periphery path, `allowedSwapper[pool][router] == true` passes for every caller of the router, including non-allowlisted users.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) must also allowlist the router if any legitimate user needs to use the supported periphery path. Once the router is allowlisted, the check passes for every caller of the router, including non-allowlisted users. The allowlist is completely bypassed for all router-mediated swaps. This constitutes an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses the pool's configured access control, matching the allowed impact category "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."

## Likelihood Explanation
The trigger requires no special privileges. The admin faces a forced choice: either allowlist the router (opening the pool to everyone via the router) or don't (breaking the supported periphery path for legitimate users). There is no correct configuration under the current design. Any non-allowlisted user can exploit this by calling any of the four router entry points targeting the affected pool.

## Recommendation
The extension must check the original user, not the intermediary. The cleanest fix mirrors `DepositAllowlistExtension`: the router should pass the real initiator (`msg.sender` at the router level) in `extensionData`, and the extension should decode and verify it (with the pool as the trusted source of `msg.sender`). Alternatively, the pool interface could be extended to carry an explicit `swapper` field analogous to `owner` in `addLiquidity`. A minimal stopgap: document that `SwapAllowlistExtension` is incompatible with any router or intermediary contract, and that pools using it must only be accessed via direct `pool.swap()` calls.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes for bob despite bob not being on the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
```