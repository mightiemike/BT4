Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating EOA, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` as seen by `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the originating EOA. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate for every non-allowlisted user, breaking the curated-pool invariant entirely.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim and dispatches it to every configured extension via `_callExtensionsInOrder` (L149-177).

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

So when `bob_EOA` calls `router.exactInputSingle(...)`, the pool sees `msg.sender = router_address`, and the extension checks `allowedSwapper[pool][router]`. If the router is allowlisted (necessary for any allowlisted user to use the standard periphery UX), this check passes for **every** caller of the router regardless of whether they are individually allowlisted.

`DepositAllowlistExtension` does not share this flaw because it checks `owner` (the explicit economic beneficiary argument), which is invariant across direct and adder-mediated paths:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

There is no configuration that simultaneously permits router-mediated swaps for allowlisted EOAs and blocks non-allowlisted EOAs from using the same router.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted counterparties loses that protection entirely once the router is allowlisted. Non-allowlisted users can execute arbitrary swaps against the oracle-anchored pool, exposing LP funds to toxic flow or adverse selection the allowlist was designed to prevent. Because the pool is oracle-anchored with no internal price discovery, LP losses from adversarial flow are direct and immediate. This constitutes broken core access-control functionality with direct LP fund exposure, meeting the Medium threshold.

## Likelihood Explanation
Pool admins deploying curated pools will routinely allowlist the router so that their vetted users can access the standard periphery UX (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). The bypass requires no special privileges: any EOA calls `router.exactInputSingle` or `router.exactInput` targeting the curated pool. The attack is repeatable in every block and requires no capital beyond the swap input.

## Recommendation
The extension must gate on the originating EOA, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This is the safer fix because it does not rely on pool admins understanding the indirect bypass path.
2. **Documentation-only policy**: Document clearly that the router must never be allowlisted on curated pools, and that allowlisted users must call `pool.swap()` directly. Add an explicit NatSpec warning to `SwapAllowlistExtension`. This is weaker because it relies on admin awareness of a non-obvious constraint.

## Proof of Concept
```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls: extension.setAllowedToSwap(pool, alice_EOA, true)
  pool admin calls: extension.setAllowedToSwap(pool, router_address, true)
    ↑ necessary so alice can use the router

Attack:
  bob_EOA (not allowlisted) calls:
    router.exactInputSingle({
      pool:        curated_pool,
      recipient:   bob_EOA,
      zeroForOne:  true,
      amountIn:    X,
      ...
    })

  router → pool.swap(recipient=bob, ...) [msg.sender = router]
  pool   → _beforeSwap(sender=router, ...)
  pool   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
             allowedSwapper[pool][router] == true  ✓
             → swap executes

Result: bob_EOA trades on the curated pool despite not being allowlisted.
        The allowlist invariant is broken for every non-allowlisted user
        who routes through the public router.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist `alice` and the router, confirm `bob` (not allowlisted) can successfully call `router.exactInputSingle` targeting the pool and receive output tokens.