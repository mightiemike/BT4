Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper, allowing any user to bypass per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to `msg.sender` of `pool.swap` — the direct caller. When users route through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any admin who allowlists the router to enable router-based swaps for permitted users simultaneously opens the gate to every unpermitted user.

## Finding Description
**Root cause — extension checks pool's `msg.sender`, not the originating user:**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of `pool.swap`) as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension (`metric-core/contracts/ExtensionCalling.sol` L149-177).

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key), and `sender` is the direct caller of `pool.swap`.

**Router is always the direct caller:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly (L72-80), making the router `msg.sender` to the pool. The same applies to `exactInput` (L103-112) and `_exactOutputIterateCallback` (L220-228). In all cases, `sender` in the extension equals the router address, not the originating user.

**Bypass path:**
1. Admin deploys pool with `SwapAllowlistExtension` to restrict swaps.
2. Admin allowlists individual users: `allowedSwapper[pool][alice] = true`.
3. Admin allowlists the router so allowlisted users can use it: `allowedSwapper[pool][router] = true`.
4. Charlie (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
5. Router calls `pool.swap(charlie, ...)` → pool calls `_beforeSwap(msg.sender=router, ...)` → extension checks `allowedSwapper[pool][router] == true` → passes → Charlie's swap executes.

**No existing guard prevents this:** The `onlyPool` modifier in `BaseMetricExtension` only validates that `msg.sender` is a registered pool; it does not validate that `sender` represents the actual originating user. There is no mechanism in the extension or router to propagate the true caller identity.

## Impact Explanation
Unauthorized users can execute swaps against LP positions in pools intended for restricted participants (e.g., KYC-gated, institutional-only, or market-maker-restricted pools). LPs deposited under the assumption that only vetted counterparties would trade; unauthorized swaps consume LP liquidity at oracle-derived prices. This constitutes a direct loss of LP principal and fee income from legitimate counterparties, matching the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" allowed impact categories.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router — a natural and expected administrative action. There is no alternative: any admin who wants allowlisted users to use the standard periphery router must allowlist it. The extension provides no per-user router delegation mechanism, so the misconfiguration is the only way to make the allowlist compatible with router-based swaps. The condition is therefore not hypothetical; it is the default operational state for any allowlist-restricted pool that supports router usage.

## Recommendation
The extension must gate by the originating user, not the direct pool caller. The preferred fix mirrors the `DepositAllowlistExtension` pattern: the router should encode `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` should decode and check that value when `sender` is a known router. Alternatively, the extension can maintain a registry of trusted routers and, when `sender` is a trusted router, extract the real user from `extensionData`. A simpler but less flexible approach is to prohibit router allowlisting entirely and require users to call `pool.swap` directly for allowlisted pools.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // intended permissioned user
  allowedSwapper[pool][router] = true   // admin adds router to enable router swaps for alice

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: restrictedPool,
        recipient: charlie,
        ...
    })

  → router calls pool.swap(charlie, ...)                          // MetricOmmSimpleRouter.sol L72-80
  → pool calls _beforeSwap(msg.sender=router, ...)               // MetricOmmPool.sol L230-231
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)        // SwapAllowlistExtension.sol L37
  → allowedSwapper[pool][router] == true → passes
  → charlie's swap executes, draining LP liquidity
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist `alice` and `router`, call `exactInputSingle` from `charlie`, assert swap succeeds and `charlie` receives output tokens despite not being individually allowlisted.