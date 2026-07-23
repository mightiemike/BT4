Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][original_user]`. Any user can bypass a curated pool's swap allowlist by routing through the public router, or allowlisted users are blocked from using the router entirely.

## Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← pool's caller, not the original user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

**Extension allowlist check**

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap` — the router, not the original user.

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The pool's `msg.sender` is therefore the router, not the original EOA:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

The original user's address (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool's `swap` call as the swapper identity.

**Two broken scenarios**

| Pool admin intent | What happens |
|---|---|
| Allowlist the router so users can swap through it | Every user — including non-allowlisted ones — passes the check; allowlist is completely bypassed |
| Allowlist individual user addresses | Those users cannot use the router at all; their swaps revert because the router is not allowlisted |

**Contrast with DepositAllowlistExtension**

`DepositAllowlistExtension.beforeAddLiquidity` does not share this flaw — it checks `owner` (the position owner explicitly passed to `addLiquidity`), which the liquidity adder preserves correctly:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, ...)
  ...
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
```

**Test coverage gap**

The existing test suite only exercises the direct-pool path through `TestCaller` (which calls `pool.swap` directly, so `msg.sender` = `TestCaller` = the allowlisted address). No test exercises the router → pool path with `SwapAllowlistExtension` active:

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L68-74
function test_allowedSwapSucceeds() public {
  swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
  _swap(0, users[0], false, int128(1000), type(uint128).max);
  // _swap uses TestCaller directly, not MetricOmmSimpleRouter
}
```

## Impact Explanation

**High** — direct policy bypass on curated pools. If the pool admin allowlists the router (the natural setup for a public periphery), any unprivileged user can swap on a pool intended to be restricted. This breaks the core access-control invariant of the extension and allows unauthorized trading, which can drain LP value on pools designed for permissioned participants. The corrupted value is the extension's boolean gate decision: `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][original_user]`, causing the extension to return `IMetricOmmExtensions.beforeSwap.selector` (allow) when it should revert.

## Likelihood Explanation

**High** — `MetricOmmSimpleRouter` is the primary supported swap entry point for EOA users. All four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) share the same flaw. Any user aware of the router can exploit this without any special setup, privileged access, or unusual token behavior. The bypass is repeatable and requires no front-running or timing.

## Recommendation

The extension must gate the **original user**, not the intermediary contract. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` at the router level before calling the pool**: The router reads the allowlist and reverts before forwarding the call, keeping the extension as the authoritative gate for direct pool calls.

Pool admin documentation should also explicitly warn that allowlisting the router grants access to all router users.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (natural setup: "let users swap through the router")
3. Non-allowlisted attacker calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Router calls pool.swap(attacker, ...) — pool's msg.sender = router.
5. _beforeSwap passes sender = router to the extension.
6. Extension checks allowedSwapper[pool][router] → true → swap proceeds.
7. Attacker successfully swaps on a pool intended to be restricted.

Conversely, if the admin allowlists individual users instead of the router,
those users' router.exactInputSingle calls revert because
allowedSwapper[pool][router] is false — breaking the expected user flow.
```