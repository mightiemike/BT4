Audit Report

## Title
SwapAllowlistExtension Gates the Immediate Pool Caller Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of `MetricOmmPool.swap()` — the immediate pool caller. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. Any pool that allowlists the router (the only way to enable router-mediated swaps for legitimate users) becomes fully bypassable by any non-allowlisted user who routes through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`) and `sender` is the first argument forwarded by the pool from its own `msg.sender`.

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-231
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
```

`ExtensionCalling._beforeSwap` encodes this `sender` and forwards it to the extension:

```solidity
// ExtensionCalling.sol lines 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly without encoding the real user identity into `extensionData`:

```solidity
// MetricOmmSimpleRouter.sol lines 71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

The pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` — not the end-user. The router stores `msg.sender` only in transient callback context for payment purposes, never in `extensionData` for the extension to verify. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner passed explicitly as a separate argument), not `sender` (the immediate caller), so the deposit gate correctly identifies the economic actor regardless of whether `MetricOmmPoolLiquidityAdder` is used.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or institutional addresses is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user can execute arbitrary swaps against the pool's LP funds, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This constitutes a direct loss of LP principal and fee revenue to unauthorized parties — a broken core pool access-control invariant with direct fund impact.

## Likelihood Explanation
The bypass requires the router to be allowlisted. This is a natural and expected configuration: any pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router. Once the router is allowlisted, the bypass is trivially reachable by any public user with no special privileges. The attacker only needs to call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool. No special setup, flash loans, or privileged access is required beyond the router being allowlisted.

## Recommendation
The extension must identify the end-user, not the immediate pool caller. Two approaches:

1. **Pass end-user identity via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to encode the correct identity.
2. **Maintain a trusted router registry**: When `sender` is a known router, require the extension to receive the real user identity through `extensionData`; reject calls from known routers that do not supply it.

The simplest safe fix is to not allowlist the router at all and require end-users to call `pool.swap()` directly on allowlisted pools, but this breaks router usability. The correct long-term fix is option 1 with a verified encoding scheme in the router.

## Proof of Concept
```
Setup:
  - Pool P has SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin allowlists router R: allowedSwapper[P][R] = true
  - Alice (allowlisted): allowedSwapper[P][Alice] = true
  - Bob (not allowlisted): allowedSwapper[P][Bob] = false

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → pool's msg.sender = Router R
  3. Pool calls _beforeSwap(sender=R, ...)
  4. ExtensionCalling encodes sender=R and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[P][R] == true → passes
  6. Bob's swap executes against LP funds despite not being allowlisted

Result: Bob bypasses the swap allowlist and trades against restricted LP funds.

Foundry test sketch:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - setAllowedToSwap(pool, router, true)
  - Confirm setAllowedToSwap(pool, bob, false)
  - vm.prank(bob); router.exactInputSingle({pool: pool, ...})
  - Assert swap succeeds (no NotAllowedToSwap revert)
```