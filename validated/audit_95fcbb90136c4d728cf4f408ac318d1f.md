Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end user, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which equals `msg.sender` at the time `pool.swap()` is called. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router address, not the end user. Any pool admin who allowlists the router — the only way to let allowlisted users swap via the router — simultaneously grants every unpermissioned address the ability to bypass the allowlist by routing through the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, passing `params.extensionData` verbatim from the caller — it does **not** encode the end user's address (`msg.sender`) into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. The pool admin faces an impossible choice:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for allowlisted users |
| Router **allowlisted** | Every address bypasses the allowlist via the public router |

No configuration simultaneously permits allowlisted users to use the router and blocks non-allowlisted users. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
Any unpermissioned address can swap on a pool restricted by `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). Pools intended to be restricted to specific counterparties — KYC'd LPs, protocol-owned addresses, whitelisted market makers — become fully open to arbitrary swappers the moment the pool admin allowlists the router. This is a broken core pool functionality: the extension's sole purpose is to gate swaps, but it is rendered ineffective by the standard periphery router path.

## Likelihood Explanation
The bypass is reachable by any unpermissioned user the moment the pool admin allowlists the router. Allowlisting the router is a natural and expected administrative action for any pool that wants to support the standard periphery swap path for its allowlisted users. The router is a public, permissionless contract. No privileged setup beyond the pool admin's routine configuration is required, and the bypass is repeatable by any address.

## Recommendation
The allowlist check must be keyed to the economically relevant actor — the end user — not the intermediate router. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it after confirming `msg.sender` (the pool) is a known pool and `sender` is a known trusted router.

2. **Trusted-router registry in the extension**: The extension maintains a registry of trusted routers; for router calls it reads the end user from a standardised field in `extensionData`.

The simplest correct fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension, after confirming the immediate `sender` is a known router, decodes the real swapper from `extensionData` for the allowlist check.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap` is called with `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he is not allowlisted for.

If the pool admin omits step 3, Alice also cannot use the router — `allowedSwapper[pool][router]` → `false` → revert. The allowlist is broken in both directions.