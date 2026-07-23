Having read the actual source files, I can now validate the claim against the real code.

**Key findings from the actual code:**

`SwapAllowlistExtension.beforeSwap` (line 37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```
Here `msg.sender` is the pool (the caller of the extension), and `sender` is the address passed from the pool — which is `msg.sender` of the pool's `swap()` call, i.e., the router.

`MetricOmmPool.swap` (line 231): passes `msg.sender` as `sender` to `_beforeSwap`.

`MetricOmmSimpleRouter.exactInputSingle` (lines 72–80): calls `pool.swap(params.recipient, ...)` directly — no originating user forwarded.

The bypass is real and confirmed by the actual code. The router is the `sender` seen by the extension, not the originating user.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the originating user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (required for any allowlisted user to use the standard periphery), the allowlist is silently bypassed for every user — any unprivileged address can swap on a restricted pool by calling the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // router address when called via MetricOmmSimpleRouter
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no mechanism to forward the originating user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
);
```

The pool admin faces an impossible choice:
- **Allowlist the router** → `allowedSwapper[pool][router] == true` passes for every user, regardless of their individual allowlist status.
- **Don't allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all, breaking the standard swap flow.

Existing guards are insufficient: the extension has no access to `tx.origin` or any forwarded originating-user field; `extensionData` is caller-supplied and unauthenticated.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd users, protocol partners). Once the router is allowlisted — a necessary step for allowlisted users to use the standard periphery — any unprivileged address can execute swaps on the restricted pool by calling `MetricOmmSimpleRouter` directly. This breaks the core access-control invariant of the allowlist extension and constitutes broken core pool functionality: unauthorized swap execution by an unprivileged path, matching the allowed impact gate ("Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path").

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is the standard user-facing entry point for swaps. Any pool operator who deploys a `SwapAllowlistExtension` and wants their allowlisted users to use the router must allowlist the router, triggering the bypass. The attacker requires no special privileges — only the ability to call a public contract function.

## Recommendation
`SwapAllowlistExtension` must check the originating user rather than the immediate pool caller. Two approaches:

1. **Encode the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires the extension to authenticate the source of `extensionData` (e.g., by also checking that `sender` is a trusted router).
2. **Check `sender` only for direct swaps; require authenticated forwarding for router swaps**: Require that any trusted router is separately registered, and that it always encodes the originating user in `extensionData` in an authenticated way.

The analogous pattern in `DepositAllowlistExtension` correctly checks `owner` (the position owner passed explicitly by the pool), not `sender` (the caller). The swap extension should similarly receive and check the true originating user identity.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` (necessary for `userA` to use the router).
3. As `userB` (not allowlisted), call `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The pool receives `msg.sender = router`; the extension checks `allowedSwapper[pool][router] == true` and passes.
5. `userB` successfully swaps on the restricted pool, bypassing the allowlist entirely.

Foundry test: deploy pool + extension, configure as above, prank as an unlisted address, call `exactInputSingle`, assert the swap succeeds (no `NotAllowedToSwap` revert).