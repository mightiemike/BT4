Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is always `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension evaluates the router's allowlist status rather than the actual user's. Any unprivileged address can bypass a per-user allowlist by calling the router, provided the router itself is allowlisted — which is a prerequisite for the pool to be usable via the router at all.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check against `sender`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`sender` is the first argument passed by `MetricOmmPool.swap` to `_beforeSwap`, and it is always `msg.sender` of the pool call:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

`msg.sender` to the pool is therefore the router contract address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates an inescapable dilemma: if the router is not allowlisted, allowlisted users cannot swap through the router; if the router is allowlisted, every address on-chain can bypass the per-user allowlist by routing through it.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the second parameter — the economically relevant LP actor), not `sender`:

```solidity
// DepositAllowlistExtension.sol L32,38
function beforeAddLiquidity(address, address owner, ...)
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

`SwapAllowlistExtension` does not apply the same principle, making the swap allowlist structurally bypassable via the router.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) achieves no protection once the router is allowlisted. Any unprivileged address can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` on the router and trade freely in the supposedly gated pool. This is a direct admin-boundary break: the pool admin's access control configuration is rendered ineffective by an unprivileged path reachable by any on-chain address.

## Likelihood Explanation
The router is the standard, documented user-facing entry point for swaps. Pool admins who deploy `SwapAllowlistExtension` will inevitably need to allowlist the router to make the pool usable for normal users, at which point the bypass is universally available. The trigger requires only a standard router call — no flash loans, no callbacks, no special timing, no admin cooperation.

## Recommendation
The extension must check the actual user identity, not the intermediary. Two sound approaches:

1. **Decode user from `extensionData`**: Require callers (router or direct) to encode the actual user address in `extensionData`; the extension decodes and verifies it. The router would need to forward the real `msg.sender` in the extension payload.
2. **Mirror the deposit pattern**: Pass the real payer/user through a dedicated field in the hook arguments, analogous to how `owner` is passed separately from `sender` in `beforeAddLiquidity`, and gate on that field instead of `sender`.

## Proof of Concept
```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists only `trustedUser` and the router (to enable router swaps).
// allowedSwapper[pool][trustedUser] = true
// allowedSwapper[pool][router]      = true   ← required for router to work

// Attacker (not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          token0,
    recipient:        attacker,
    zeroForOne:       true,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:    type(uint128).max,
    deadline:         block.timestamp,
    extensionData:    ""
}));
// pool.swap() is called with msg.sender = router.
// Extension checks allowedSwapper[pool][router] == true → passes.
// Attacker swaps successfully despite not being on the allowlist.
```