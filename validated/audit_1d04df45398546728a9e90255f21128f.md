Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating Caller, Enabling Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual user. Any pool admin who allowlists the router to permit legitimate users to use it simultaneously opens the pool to every unpermissioned address that routes through the same router.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender`:**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at lines 230–240. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router address.

**Step 2 — Extension receives the router address as `sender`:**
`ExtensionCalling._beforeSwap()` (lines 149–177) encodes and forwards the `sender` argument unchanged to every configured extension via `_callExtensionsInOrder`.

**Step 3 — Extension checks the wrong actor:**
`SwapAllowlistExtension.beforeSwap()` (line 37) evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user.

**Step 4 — Router provides no original-caller forwarding:**
`MetricOmmSimpleRouter.exactInputSingle()` (lines 71–80) calls `pool.swap()` with no mechanism to pass the originating `msg.sender` into the extension hook. The `extensionData` field is passed through as-is from the user's call parameters, but the router itself does not inject the caller's address.

**Impossible choice for pool admin:**
- Router NOT allowlisted → allowlisted users cannot use the router at all.
- Router IS allowlisted → `allowedSwapper[pool][router] == true`, so every address on the network passes the check by routing through the router.

**Tests miss the router path:**
The unit tests call `extension.beforeSwap()` directly with `vm.prank(address(pool))` and supply the swapper address as `sender`, never exercising the router intermediary path.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER` to restrict trading to KYC'd addresses, institutional partners, or any curated set is fully open to any unpermissioned address that routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool; LP positions are exposed to unrestricted swap flow that the allowlist was designed to prevent. The exact corrupted value is the `allowedSwapper[pool][sender]` extension decision: it evaluates `true` for the router when it should evaluate `false` for the unpermissioned end user. This constitutes a broken core access-control invariant with direct loss exposure to LP assets.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary documented user-facing entry point. Any pool admin who wants allowlisted users to be able to use the standard router must call `setAllowedToSwap(pool, router, true)`. This is the natural, expected configuration. Once the router is allowlisted, the bypass is reachable by any address with no special privileges, no flash loans, and no unusual token behavior — a single call to `exactInputSingle` suffices.

## Recommendation

The `sender` forwarded to extension hooks must represent the original human/EOA caller:

1. **In the router**: inject the original `msg.sender` into `extensionData` (or a dedicated calldata field) so the pool can forward it to extensions as the true initiator.
2. **In `SwapAllowlistExtension`**: if the pool cannot supply the original caller, the extension should reject any `sender` that is a known router/contract unless that contract is explicitly allowlisted with a separate flag, and document that allowlisting the router opens the gate to all users.
3. **Alternatively**: add a protocol-level mechanism (e.g., EIP-2771-style trusted forwarder) so the pool always receives the originating EOA regardless of routing intermediaries.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use `MetricOmmSimpleRouter`.
3. Attacker (address not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amountIn, priceLimitX64, "", extensionData)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Attacker receives token output from the restricted pool, bypassing the allowlist entirely.