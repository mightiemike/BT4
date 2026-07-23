Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which resolves to `msg.sender` of `pool.swap()` — the router, not the end user. When `MetricOmmSimpleRouter` is allowlisted (the natural admin action to enable UI-based trading), every non-allowlisted user can bypass the curated pool's swap gate by routing through the router. The pool's entire access-control policy is silently nullified.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check on `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` — the actual economic actor:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol:32-38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  ...
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The two extensions are architecturally inconsistent. The deposit guard keys on the economic actor (`owner`); the swap guard keys on the immediate caller (`sender`/router). No existing guard in the router or pool re-checks the real user identity before the extension decision is made.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists `MetricOmmSimpleRouter` (the standard step to enable periphery-based trading) inadvertently opens the gate to every address. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`, the router forwards the call to the pool with `msg.sender = router`, the extension sees the allowlisted router address, and the swap proceeds. The curated pool's entire access-control policy is silently nullified. In pools where the allowlist enforces KYC, regulatory compliance, or LP-protection boundaries, this constitutes a direct loss of LP principal protection and broken core pool functionality — matching the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" and "Admin-boundary break" allowed impacts.

## Likelihood Explanation
The trigger is a standard, expected admin action: allowlisting the official periphery router so that users can interact through the supported UI. Any pool that (a) configures `SwapAllowlistExtension` and (b) allowlists the router — a combination that is both natural and the supported flow — is fully exposed. No attacker privilege is required beyond calling the public router. The bypass is repeatable and unconditional once the router is allowlisted.

## Recommendation
Replace the `sender` check with the actual user identity. The deposit allowlist's pattern — ignoring `sender` and checking the explicit economic-actor parameter — should be the model:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData`; the extension decodes and checks it. This is the robust solution and mirrors how `owner` is threaded through `addLiquidity`.
2. **Check `recipient`** as a simpler approximation, noting that `recipient` can be a contract in multi-hop routes and may not always equal the economic actor.

## Proof of Concept
1. Pool admin deploys pool with `SwapAllowlistExtension`; calls `setAllowedToSwap(pool, alice, true)`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable periphery access.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. `bob`'s swap executes in the curated pool despite never being allowlisted.

Direct call by `bob` to `pool.swap()` correctly reverts (`allowedSwapper[pool][bob]` is `false`), confirming the bypass is router-specific and fully reproducible as a Foundry integration test.