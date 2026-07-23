Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating EOA, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router_address`. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for a curated pool), every public user bypasses the allowlist entirely by calling any router entry point.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool address (the extension caller), so the check is effectively `allowedSwapper[pool][sender]`.

`sender` is populated by `ExtensionCalling._beforeSwap`, which passes the `sender` argument it receives directly into the encoded call to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-176
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...)));
}
```

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router contract `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore sees `msg.sender = router_address`. The extension checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][EOA_address]`. The router has no mechanism to forward the originating user's identity to the extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

Existing guards are insufficient: the only check in `beforeSwap` is the `allowedSwapper` mapping lookup against the `sender` argument, which is structurally bound to the immediate caller of `pool.swap()`, not the originating EOA.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to approved counterparties loses all access control the moment the router is allowlisted. Any public user can execute swaps against the pool's LP reserves at oracle-derived prices, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This constitutes direct loss of LP principal and protocol fees, meeting Sherlock critical/high thresholds.

## Likelihood Explanation
The scenario is triggered by a single, well-motivated admin action. A pool admin who deploys a curated pool with `SwapAllowlistExtension` will observe that allowlisted EOAs cannot use the standard router (because the router is not individually allowlisted). The obvious remediation — `setAllowedToSwap(pool, router, true)` — is the exact action that opens the pool to all users. No attacker setup is required beyond calling the public router with any address. The exploit is repeatable and permissionless once the router is allowlisted.

## Recommendation
The extension must gate the economically relevant actor — the originating EOA — not the intermediate contract. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks this value. This requires a convention between router and extension.
2. **Document and enforce incompatibility**: Add a revert in `beforeSwap` if `sender` matches any known router address, and document that `SwapAllowlistExtension` is only safe for direct pool calls and must not be combined with a router-allowlist entry.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for allowlisted users.
3. Attacker (not individually allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: attacker,
       ...
     })
4. Pool receives swap() with msg.sender = router.
5. Extension checks allowedSwapper[pool][router] == true → passes.
6. Swap executes. Attacker receives output tokens from LP reserves.
   Allowlist is fully bypassed.
```