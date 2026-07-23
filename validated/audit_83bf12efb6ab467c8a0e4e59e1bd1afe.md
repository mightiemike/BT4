Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is documented as gating `swap` by swapper address per pool, but its `beforeSwap` hook inspects `sender`, which is `msg.sender` of the pool — the immediate caller — not the end user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address. A pool admin who allowlists the router to enable router-based trading inadvertently grants every router caller the same access as individually allowlisted addresses, completely defeating the per-user curation policy.

## Finding Description
**Root cause — wrong identity checked:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is the first argument forwarded by the pool. The check resolves to `allowedSwapper[pool][sender]`.

**Pool dispatches `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap() — the router, not the end user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then encodes and forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

**Router makes itself `msg.sender` of the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

This makes the router the `msg.sender` of the pool, so `sender` in the extension is the router address, not the end user.

**Bypass path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` as `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to enable router-based trading for `userA`.
4. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(userB, ...)` — pool dispatches `_beforeSwap(msg.sender=router, recipient=userB, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `userB` successfully trades on a pool from which they were supposed to be excluded.

**Existing guards are insufficient:** The `recipient` address (the actual end user) is available as the second parameter of `beforeSwap` but is silently discarded (`address,`). The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner passed explicitly by the pool), which correctly identifies the economic actor regardless of the intermediary caller.

## Impact Explanation
The `SwapAllowlistExtension` is the primary on-chain mechanism for curating who may trade on a pool. When the router is allowlisted — a natural and expected configuration step — the guard silently fails open for all router users. Any address excluded from the per-user allowlist can trade freely by routing through `MetricOmmSimpleRouter`, defeating the curation policy entirely. On pools where the allowlist restricts access to professional counterparties or limits exposure during sensitive periods, this allows arbitrary users to extract value from LP positions at prices the LPs did not intend to offer to the general public, constituting a direct LP-fund-impacting consequence. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural and expected action: any pool admin who wants to support the standard periphery flow will add the router to the allowlist, not realising that doing so grants every router user the same access as individually allowlisted addresses. The router is a canonical, factory-known contract, so allowlisting it is a low-friction, plausible configuration step. No special attacker capability is required beyond calling the public `router.exactInputSingle` function.

## Recommendation
The extension must check the **economic actor** (the end user), not the **transport layer** (the router). The `recipient` address is already available as the second parameter of `beforeSwap` and is currently ignored. For single-hop swaps, checking `recipient` instead of `sender` correctly identifies the beneficiary. Alternatively, require the actual user address to be encoded in `extensionData` and verify it in the extension, with the router encoding `msg.sender` there. The `DepositAllowlistExtension` pattern — checking the explicitly passed owner rather than the caller — should be mirrored here.

## Proof of Concept
Foundry integration test:
1. Deploy `SwapAllowlistExtension` and a pool with it wired as `beforeSwap`.
2. As pool admin, call `setAllowedToSwap(pool, userA, true)`.
3. As pool admin, call `setAllowedToSwap(pool, router, true)`.
4. As `userB` (not allowlisted), call `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Assert the call succeeds (no `NotAllowedToSwap` revert).
6. Confirm `userB` received output tokens despite never being added to the allowlist.
7. For contrast: call `pool.swap(userB, ...)` directly as `userB` and assert it reverts with `NotAllowedToSwap`.