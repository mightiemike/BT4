Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Real User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` is used, the router becomes `msg.sender` at the pool. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unprivileged caller can bypass the allowlist by routing through the router, completely defeating the access control.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool — the real user's address is stored only in transient callback context, never forwarded as `sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a two-sided identity mismatch. If the admin allowlists the router (required for any allowlisted user to swap via the router), the extension sees `sender = router` for every caller — allowlisted or not — and passes unconditionally. If the admin does not allowlist the router, allowlisted users cannot use the router at all.

## Impact Explanation
This is a broken admin-boundary / broken core pool functionality finding. A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific addresses has that restriction completely bypassed by any unprivileged caller who routes through `MetricOmmSimpleRouter`. The attacker executes swaps in a pool whose access policy was designed to prevent them, which can result in direct loss of funds to LPs (e.g., in a pool intended only for KYC'd counterparties or specific market makers). Both the bypass path (router allowlisted) and the broken-functionality path (router not allowlisted, legitimate users blocked) are direct consequences of the same root cause.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token balance, or prior state is required to call `exactInputSingle`. The bypass is deterministic: every call through the router presents the router address to the extension. Pool admins have no on-chain mechanism to distinguish "router called on behalf of allowlisted user X" from "router called on behalf of attacker Y" because the router does not forward the originating user's address as `sender`.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should check the economically relevant actor, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** if the intent is to gate who receives tokens.
2. **Require the router to forward the originating user** — add a `payer` or `originator` field to `extensionData` and have the extension decode and verify it, with the router encoding `msg.sender` before the pool call.

Option 2 is more robust because it preserves the router's role as a payment intermediary while still gating the real user identity.

## Proof of Concept
```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, address(router), true)
   — required so that any allowlisted user can swap through the router.
3. Attacker (address NOT in allowedSwapper) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool:          <target pool>,
           recipient:     attacker,
           zeroForOne:    true,
           amountIn:      X,
           ...
       }))
4. Pool.swap is entered with msg.sender = router.
5. _beforeSwap passes sender = router to SwapAllowlistExtension.
6. Extension evaluates: allowedSwapper[pool][router] == true → no revert.
7. Swap executes. Attacker receives tokens from a pool that was supposed
   to be restricted to allowlisted addresses only.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
