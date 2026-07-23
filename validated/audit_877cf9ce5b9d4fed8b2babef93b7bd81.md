Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of User Address, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. Any pool admin who allowlists the router address to support standard periphery usage inadvertently grants every user unrestricted swap access, completely defeating the allowlist guard.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions**

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim as `sender` to every configured extension: [2](#0-1) 

**Step 2 — Router is `msg.sender` to the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool sees the router contract as `msg.sender`, not the end user: [3](#0-2) 

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181).

**Step 3 — Extension checks the router address, not the user**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router) and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router address: [4](#0-3) 

The check `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`. The originating user's address is never consulted.

**Resulting invariant break**

A pool admin who allowlists the router (a natural action to support the canonical periphery interface) opens the pool to every user. The `allowedSwapper` mapping keyed by `swapper` address is rendered meaningless for any router-mediated swap.

## Impact Explanation
Any unprivileged user can bypass a `SwapAllowlistExtension`-protected pool by routing through `MetricOmmSimpleRouter`. LPs suffer direct loss of principal because they receive trades from counterparties they explicitly excluded via the allowlist. The pool's curated-counterparty invariant is broken: LP assets are exposed to oracle-derived prices against unintended counterparties, constituting a direct loss of LP principal above Sherlock thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, documented swap interface. Any pool admin deploying `SwapAllowlistExtension` who also wants to support the standard router will allowlist the router address, triggering the bypass. The attacker requires no special privileges — a single `exactInputSingle` call suffices. The condition is reachable by any user on any allowlist-protected pool that has also allowlisted the router.

## Recommendation
The extension must gate the economic actor, not the intermediary. The most robust fix is to pass the original user through the router by adding a `payer` or `originator` field to the swap call path so the pool can forward the true initiator to extensions. Alternatively, the `SwapAllowlistExtension` could check `recipient` (the address receiving output tokens), since the router always sets `recipient` to the user-supplied address — though this is semantically weaker and can be gamed. As a short-term fix without protocol changes, the router could detect whether the target pool has an active swap allowlist and revert, forcing users to call the pool directly.

## Proof of Concept
```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists the router so allowlisted users can use the standard interface.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) calls the router.
// Extension sees sender = address(router), which IS allowlisted → passes.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp,
    priceLimitX64: 0,
    extensionData: ""
}));
// Swap executes successfully despite attacker not being on the allowlist.
```

The root cause is confirmed at `SwapAllowlistExtension.beforeSwap` L37: `allowedSwapper[msg.sender][sender]` evaluates `allowedSwapper[pool][router]` — the individual user's address is never consulted. [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
