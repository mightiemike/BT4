The code confirms the vulnerability. Key facts:

- `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `pool.swap()` takes no explicit `sender` parameter — the pool always uses `msg.sender` [2](#0-1) 
- `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap(params.recipient, ...)` with no way to forward the original caller's address as `sender` [3](#0-2) 
- `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router [4](#0-3) 

The claim is fully supported by production code.

---

Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via the public router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` from the pool's perspective — the address that called `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's allowlist status rather than the actual user's. Any pool admin who adds the router to the allowlist (the only way to enable legitimate router-mediated swaps) inadvertently opens the gate to every user on the network.

## Finding Description
`MetricOmmPool.swap()` captures `msg.sender` and passes it verbatim as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the direct caller of pool.swap()
  recipient,
  ...
);
```

`pool.swap()` has no explicit `sender` parameter — the identity is always `msg.sender`. `MetricOmmSimpleRouter.exactInputSingle()` calls:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,   // recipient only; no sender forwarding
  params.zeroForOne,
  ...
);
```

So the pool sees `msg.sender = router`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check passes whenever `allowedSwapper[pool][router] == true`. Since the router must be allowlisted for any legitimate user to trade via it, this condition is always true in any operational pool that supports router access — making the allowlist a no-op for all router-mediated swaps.

## Impact Explanation
LP providers who deploy capital into a pool with `SwapAllowlistExtension` do so under the assumption that only explicitly allowlisted counterparties can trade against their positions. Any user — including those explicitly excluded — can trade against those LP positions by routing through `MetricOmmSimpleRouter`, causing direct loss of principal to LP providers whose positions are consumed by unauthorized counterparties. This is a broken core pool functionality causing direct loss of LP assets, matching the allowed impact gate.

## Likelihood Explanation
The router is a public, permissionless contract requiring no special privilege. The only precondition — that the router is allowlisted — is the expected and necessary configuration for any pool that wants legitimate users to access it via the standard periphery. The exploit path is a single call to `MetricOmmSimpleRouter.exactInputSingle()` from any EOA.

## Recommendation
`MetricOmmSimpleRouter` should forward the originating user's address as the effective swapper identity. Since `pool.swap()` does not accept an explicit `sender` parameter and always uses `msg.sender`, the correct fix is to pass `msg.sender` (the original user) through the callback data or an alternative mechanism so the extension can verify it. The cleanest approach is to add an explicit `sender` parameter to `pool.swap()` that the pool passes to extension hooks instead of `msg.sender`, allowing the router to supply the true originating user. Alternatively, the router could use a trusted-forwarder pattern where the pool reads the originating address from a verified transient storage slot set by the router before calling `swap()`.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` registered in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so legitimate users can trade via `MetricOmmSimpleRouter`.
3. Unauthorized user `U` (not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: U, ...})`.
4. Router calls `pool.swap(recipient=U, ...)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, recipient=U, ...)` → extension receives `sender=router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Swap executes; `U` receives output tokens. The allowlist provided zero protection.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
