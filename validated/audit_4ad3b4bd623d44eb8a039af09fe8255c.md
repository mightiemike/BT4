Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router address, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously grants every non-allowlisted user the ability to bypass the per-user gate by routing through the router.

## Finding Description
**Pool passes its own `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension via `abi.encodeCall`: [2](#0-1) 

**Extension checks `sender`, which equals the router when the router calls the pool.**

`SwapAllowlistExtension.beforeSwap()` evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool (correct), and `sender` = whoever called `pool.swap()`. When the router is the caller, `sender` = router address.

**The router calls `pool.swap()` directly with no mechanism to forward the original user.**

`MetricOmmSimpleRouter.exactInputSingle()` stores the original `msg.sender` only in transient callback context for payment settlement, then calls `pool.swap()` as `msg.sender = router`: [4](#0-3) 

The original user's address is never forwarded to the extension as `sender`. The `extensionData` bytes are user-controlled and unauthenticated, so the extension cannot safely read the original user from them.

**The forced admin dilemma.**

For a curated pool to support router-based swaps at all, the admin must call `setAllowedToSwap(pool, router, true)`: [5](#0-4) 

Once the router is allowlisted, `allowedSwapper[pool][router]` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The per-user gate is completely neutralized for the router path.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption that only vetted counterparties could trade. This is a direct, fund-impacting bypass of a configured security control: LP principal is exposed to unrestricted oracle-price extraction by any address. This matches the allowed impact gate: admin-boundary break where a pool admin's security control is bypassed by an unprivileged path, and direct loss of LP principal.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected configuration for any curated pool that also wants to support the standard periphery router — the admin has no other way to enable router-based swaps for the allowlisted users. The condition is therefore likely to be met in production deployments that combine `SwapAllowlistExtension` with `MetricOmmSimpleRouter` support. The attack is repeatable by any unprivileged address with no special capability beyond calling the public router.

## Recommendation
The extension must identify the **original initiating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Authenticated `extensionData` field**: The router encodes the original `msg.sender` into `extensionData` and signs it, and the extension verifies it against a trusted router registry. This requires the extension to maintain a registry of trusted routers.

2. **Transient initiator slot on the pool**: The pool stores the original `msg.sender` in a transient slot before any extension call, and extensions read it via a pool view function. This mirrors how the router already stores its own callback context in transient storage.

Until fixed, pool admins must not allowlist the router address on pools using `SwapAllowlistExtension`; router-based swaps must be disabled for curated pools.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (necessary to allow any router-based swap for allowlisted users)
  alice is NOT individually allowlisted

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)          [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
  → swap executes; alice trades on the curated pool without being allowlisted

Result:
  allowedSwapper[pool][alice] == false, yet alice's swap settles at oracle price
  against LP capital deposited under a curated-counterparty assumption.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension configured
  2. Admin calls setAllowedToSwap(pool, address(router), true)
  3. Assert alice (non-allowlisted) calling pool.swap() directly reverts with NotAllowedToSwap
  4. Assert alice calling router.exactInputSingle({pool: pool, ...}) succeeds
  5. Confirm allowedSwapper[pool][alice] == false throughout
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
