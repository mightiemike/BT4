Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict which addresses may swap against a pool. Its `beforeSwap` hook receives `sender` — the direct caller of `pool.swap()` — and checks it against a per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router contract address, not the end user. Once the pool admin allowlists the router (the only way to enable router-mediated swaps on an allowlisted pool), every user routing through it passes the check unconditionally, collapsing the per-user gate to a per-contract gate.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` passes `sender` through without modification to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that identity against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` is the intermediary, it calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← user-controlled, cannot be trusted for identity
  );
``` [4](#0-3) 

The `extensionData` field passed to the pool is taken directly from `params.extensionData`, which is fully user-controlled and cannot be used as a trusted identity source. There is no mechanism in the router to encode the originating `msg.sender` in an authenticated way.

The allowlist maps `allowedSwapper[pool][swapper]`. For any router-mediated swap to succeed on an allowlisted pool, the pool admin must add the router to this map. The moment the router is allowlisted, `allowedSwapper[pool][router]` returns `true` for every user who routes through it — the per-user gate collapses to a per-contract gate. [5](#0-4) 

## Impact Explanation
Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (a required operational step to support normal UX on an allowlisted pool), the allowlist no longer gates individual users — it gates only the router contract. Unauthorized users gain full swap access to a pool that was intended to be restricted, enabling them to execute swaps against LP capital that the pool admin explicitly prohibited. This is a direct, fund-impacting break of the pool's intended access-control invariant: LP assets are consumed by swaps from addresses that should have been blocked, constituting unauthorized settlement against restricted LP capital.

## Likelihood Explanation
The scenario requires no malicious setup, no non-standard tokens, and no privileged cooperation beyond the pool admin performing the expected operational action of enabling router access. Any unprivileged user can exploit this:
1. A pool is deployed with `SwapAllowlistExtension` configured in the `beforeSwap` order.
2. The pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps (a natural operational step).
3. Any user — including one explicitly blocked via `allowedSwapper[pool][attacker] = false` — calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting that pool.
4. The extension sees `sender` = router address, the check passes, and the swap executes.

This is repeatable by any address at any time after the router is allowlisted.

## Recommendation
The `sender` identity passed to extension hooks must reflect the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should encode the originating `msg.sender` into `extensionData` using a trusted, authenticated field (e.g., ABI-encoded with a router-specific prefix or EIP-712 signature), and `SwapAllowlistExtension` should decode and verify it when present.
2. **Extension-level**: `SwapAllowlistExtension` should expose a separate allowlist for trusted forwarders (e.g., the router) and, when `sender` is a known forwarder, decode the real user from `extensionData` before performing the allowlist check.

The simplest safe default is to never allowlist the router address directly; instead, require that all allowlisted swaps arrive via direct `pool.swap()` calls, and document this constraint explicitly in the extension's NatSpec.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order set)
  allowedSwapper[pool][router] = true    ← admin enables router access
  allowedSwapper[pool][attacker] = false ← attacker is explicitly blocked

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})
    → router calls pool.swap(recipient=attacker, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] == true  ✓ passes
    → swap executes, attacker receives output tokens

Result:
  Attacker swaps against a pool they are explicitly blocked from,
  receiving output tokens while the pool's LP capital is consumed
  without the access restriction the admin configured.
```

Foundry test outline:
1. Deploy `SwapAllowlistExtension`, configure a pool with it in `beforeSwap` order.
2. Call `setAllowedToSwap(pool, router, true)` and `setAllowedToSwap(pool, attacker, false)`.
3. Verify direct `pool.swap()` from `attacker` reverts with `NotAllowedToSwap`.
4. Verify `MetricOmmSimpleRouter.exactInputSingle` from `attacker` succeeds and returns output tokens.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
