Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the originating user. If the pool admin allowlists the router so that legitimate users can trade through it, the check degenerates to `allowedSwapper[pool][router] == true`, which passes for every caller regardless of individual allowlist status.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

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

At this point `msg.sender` inside `MetricOmmPool.swap` is the **router address**, so `sender` forwarded to the extension is the router, not the originating user. The extension evaluates `allowedSwapper[pool][router]` — a single entry that covers every user who routes through the router. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

The dilemma for pool admins:
- **Do not allowlist the router** → individually allowlisted users cannot use the router at all
- **Allowlist the router** → every address on-chain can bypass the allowlist by routing through the router

No existing guard in `SwapAllowlistExtension`, `MetricOmmPool`, or `MetricOmmSimpleRouter` checks or forwards the originating `msg.sender` to the extension.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's LP liquidity, extracting value at oracle-anchored prices that the pool's LPs did not consent to provide to arbitrary counterparties. This constitutes a broken core pool invariant (curated access) and a direct loss of LP principal — matching the "admin-boundary break bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is the canonical production swap entry point. Pool admins who configure `SwapAllowlistExtension` and want their allowlisted users to have a normal UX will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

## Recommendation
The extension must check the **originating user**, not the intermediary. The preferred fix is to modify `MetricOmmSimpleRouter` to encode the originating `msg.sender` into `extensionData` and modify `SwapAllowlistExtension.beforeSwap` to decode and check that identity when the immediate `sender` is a known, trusted router address. Alternatively, the pool/extension interface could be extended with a dedicated `originator` field that the router populates, allowing the extension to check the true economic actor.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
  - bob is NOT in the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls curated_pool.swap(bob, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the curated pool's LP liquidity
     despite never being individually allowlisted.

Result: bob bypasses the curated allowlist and trades against LP funds
        that were only meant to be accessible to alice and other approved counterparties.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
