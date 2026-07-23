Audit Report

## Title
SwapAllowlistExtension Checks the Router Address Instead of the End User, Allowing Any Caller to Bypass the Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap`, so `sender` is always the router's address. Allowlisting the router — the only way to let intended users trade through it — simultaneously grants every unprivileged address on-chain the ability to bypass the per-user gate, completely defeating the access-control invariant.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value forwarded from `MetricOmmPool.swap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    ...
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router itself calls `pool.swap`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

Because the router is the direct caller of `pool.swap`, `sender` inside `beforeSwap` is always the router's address — never the actual end user. The same applies to `exactInput` (multi-hop, L104) and `exactOutputSingle`/`exactOutput` paths. The pool admin faces an impossible choice: not allowlisting the router means allowlisted users cannot use the router at all; allowlisting the router means every address on-chain can call `exactInputSingle`/`exactInput`/`exactOutput` and the check passes unconditionally, since `allowedSwapper[pool][router] == true`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a KYC-gated pool, a private institutional pool, or a pool restricted to specific market makers) can be accessed by any unprivileged user via the public router. The unauthorized user executes swaps at oracle-derived prices, exposing LP positions to unrestricted adverse-selection flow that LPs explicitly opted out of. This is a broken access-control invariant with direct LP fund impact: LPs bear adverse-selection risk from counterparties they never intended to face.

## Likelihood Explanation
The bypass requires only that the pool admin allowlists the router — a natural and expected action for any pool that wants its allowlisted users to be able to use the standard periphery. The router is a public, permissionless contract. Once the router is allowlisted, any address can call it with no special privileges, no flash loan, and no malicious token. The condition is trivially reachable in any realistic deployment.

## Recommendation
Pass the actual end-user identity through the hook rather than the direct pool caller. Two viable approaches:

1. **Encode the originating user in `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. The extension must verify the attested identity cannot be spoofed by a caller who constructs `extensionData` directly (e.g., by requiring the pool to validate the encoding or by checking a router-specific prefix/signature).
2. **Standardize an "originator" field in the extension interface**: Add a dedicated originator field so the router can attest the real user, and the allowlist verifies the attested identity rather than the direct caller.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // intended: only alice may swap
  allowedSwapper[pool][router] = true   // admin adds this so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  Execution path:
    charlie → router.exactInputSingle(...)
      router → pool.swap(...)           // msg.sender in pool = router
        _beforeSwap(sender=router, ...)
          SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  → check passes
        swap executes at oracle price
        charlie receives token output

Result:
  charlie, who is not in the allowlist, successfully swaps in a pool
  configured to be restricted to alice only. LPs are exposed to
  unrestricted counterparty flow they explicitly opted out of.
```