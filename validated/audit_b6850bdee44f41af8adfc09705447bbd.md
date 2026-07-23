Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to support router-mediated swaps), the per-user gate is nullified: any unpermissioned EOA can bypass the allowlist by calling the router. This breaks the curated-access invariant of pools using this extension and exposes LP principal to unauthorized counterparties.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routed
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` encodes and forwards this `sender` value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — the pool never sees the original EOA. The original user's address is stored in transient storage via `_setNextCallbackContext` for payment purposes only, and is never forwarded to the extension:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
  params.recipient, params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64, "", params.extensionData   // extensionData is user-supplied, not router-injected
);
```

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. No existing guard in the extension, pool, or router checks the original EOA identity.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` is designed to restrict which addresses may trade against its liquidity. Once the router is allowlisted (the only operational choice to support normal UX), the restriction is nullified for every user who routes through the router. Unpermissioned users can execute swaps at oracle-anchored prices, extracting value from LP positions intended to serve only a controlled set of counterparties. This constitutes a direct loss of LP principal and a broken core pool invariant (curated access control), meeting the "Broken core pool functionality causing loss of funds" criterion.

## Likelihood Explanation
The router is the primary production entry point for end users. Any pool that wants to support normal UX must either allowlist the router or require every user to call the pool directly. Allowlisting the router is the obvious operational choice, making this bypass reachable on every curated pool that uses the router. No privileged setup beyond normal pool configuration is required; any unpermissioned EOA can trigger it immediately.

## Recommendation
The extension must gate on the economic actor, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field inside `extensionData` (signed or encoded with a trusted router identifier), and `SwapAllowlistExtension` should decode and check that field instead of the raw `sender` argument.

2. **Alternatively, maintain a trusted-router registry in the extension.** When `sender` is a recognized router, require the real user identity to be supplied and verified via a signed payload in `extensionData`. When `sender` is not a router, check `sender` directly as today.

The deposit-side extension (`DepositAllowlistExtension`) does not share this flaw because it gates on `owner` (the position recipient), which the liquidity adder passes through unchanged from the caller's explicit argument.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, userA, true)` — intending to restrict swaps to `userA` only.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
8. `userB`'s swap executes successfully against the curated pool's liquidity, bypassing the intended per-user gate.