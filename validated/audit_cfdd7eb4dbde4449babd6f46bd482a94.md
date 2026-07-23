Audit Report

## Title
SwapAllowlistExtension Allowlist Fully Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every user — including those explicitly excluded — can bypass the per-user gate by routing through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` encodes that value verbatim as the `sender` field and dispatches it to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the pool's `msg.sender` the router contract address:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:
- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all.
- **Allowlist the router** → every user, including those explicitly excluded, can bypass the per-user gate by routing through the router.

Bypass path:
```
Disallowed user
  → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
      → pool.swap(recipient, ...)   [msg.sender = router]
          → _beforeSwap(sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  → allowedSwapper[pool][router] == true  ✓  (bypass)
```

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified market makers, institutional counterparties, or protocol-controlled bots). Once the router is allowlisted — the only way to support router-mediated swaps for legitimate users — the restriction is completely nullified. Any address can execute swaps against the pool's liquidity at oracle-derived prices, draining LP principal and fees in ways the pool admin explicitly intended to prevent. The allowlist guard provides zero protection on the router path, constituting broken core pool functionality and direct loss of user principal.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint for the protocol. Pool admins who configure a swap allowlist and also want to support standard router usage will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA or contract can call `exactInputSingle` on the router. The precondition (router allowlisted) is the expected production configuration for any allowlisted pool that supports periphery routing.

## Recommendation

The extension must gate the actual end user, not the intermediary router. The cleanest fix is for the router to append `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap`, and for `SwapAllowlistExtension.beforeSwap` to decode and check that value when `sender` is a known router address. Alternatively, the router can enforce its own per-user allowlist check before calling the pool, and the pool allowlist gates only the router address — but this requires the router to be a trusted, non-upgradeable contract.

## Proof of Concept

```solidity
// Setup:
//   1. Deploy pool with SwapAllowlistExtension.
//   2. Pool admin allowlists alice (legitimate user) and the router.
//   3. Bob (not allowlisted) calls the router — swap succeeds.

contract BypassPoC {
    IMetricOmmSimpleRouter router;
    address pool;
    address token0;

    function bypass(address bob) external {
        // Bob is NOT in allowedSwapper[pool][bob]
        // Router IS in allowedSwapper[pool][router]
        // Bob routes through the router → extension sees sender=router → passes

        vm.prank(bob);
        router.exactInputSingle(
            IMetricOmmSimpleRouter.ExactInputSingleParams({
                pool: pool,
                tokenIn: token0,
                recipient: bob,
                deadline: block.timestamp + 1,
                amountIn: 1e18,
                amountOutMinimum: 0,
                zeroForOne: true,
                priceLimitX64: 0,
                extensionData: ""
            })
        );
        // Bob's swap executes despite not being on the allowlist.
    }
}
```