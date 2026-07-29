### Title
`MsgExecutePayload` deducts gas fees from a victim UEA's balance even when signature verification fails, allowing unauthorized griefing/draining of unowned account funds - (File: x/uexecutor/keeper/msg_execute_payload.go)

### Summary
This is the closest analog to the ZeroLend `unstakeLP`/`unstakeToken` bug class: a message lets an unprivileged caller reference a resource they don't own (there, an NFT `tokenId`; here, a victim's `UniversalAccountId`/UEA) and trigger fund-affecting logic against it while the actual authorization check (there, NFT ownership; here, `verificationData` signature) is allowed to fail *after* funds have already moved.

### Finding Description
`MsgExecutePayload` is explicitly designed so `Signer != Owner` is safe (per `x/uexecutor/README.md:211-238`): any address may submit the message on behalf of any `UniversalAccountId`, and the UEA contract's `executeUniversalTx` is supposed to be the sole authorization gate, reverting on bad signatures with **"No state changes survive a failed signature check."**

However, the Cosmos-layer keeper method actually wired into the message handler, `Keeper.ExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go:16-97`, invoked from `msgServer.ExecutePayload` at `x/uexecutor/keeper/msg_server.go:43-55`), does not defer gas-fee deduction behind the signature check the way the newer `ExecutePayloadV2` helper does. Compare:

- `ExecutePayloadV2` (`x/uexecutor/keeper/execute_payload.go:35-53`) wraps `CallUEAExecutePayload` + `DeductGasFeesFromReceipt` in a `CacheContext`; if fee deduction fails, `writeCache()` is never called and *all* state (including the reverted EVM call) rolls back.
- The keeper method actually used by `MsgExecutePayload`, `Keeper.ExecutePayload` (`msg_execute_payload.go:87-97`), calls:
  ```go
  receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)
  if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
      return fmt.Errorf("gas fee deduction failed: %w", feeErr)
  }
  if execErr != nil {
      return execErr
  }
  ```
  Gas fee deduction runs directly against `sdkCtx` (not a discardable cache) **before** `execErr` is checked. If `CallUEAExecutePayload` reverts (e.g., because `verificationData` doesn't recover to the victim's owner key) but still returns a non-nil receipt with `GasUsed > 0` (typical EVM revert behavior — gas is consumed and reported even on `REVERT`), `DeductGasFeesFromReceipt` (`x/uexecutor/keeper/fees.go:97-148`) unconditionally burns `gasCost` from the victim UEA's account via `DeductAndBurnFees` (`fees.go:16-37`), which does a real `SendCoinsFromAccountToModule` + `BurnCoins` against `ueaAddr`.

Critically, `gasCost` is computed from `MaxFeePerGas`/`MaxPriorityFeePerGas`/`GasLimit` taken from the **attacker-supplied** `UniversalPayload` (`fees.go:111-124`, `types.NewAbiUniversalPayload`), not from anything the victim signed — only `execErr` (via `verificationData`) is meant to gate whether the payload is "real," and that gate is checked *after* the burn already happened.

### Impact Explanation
An unprivileged attacker who knows or guesses any victim's `UniversalAccountId` (chain namespace + chain id + owner — all public, derivable from any observed source-chain address) can submit `MsgExecutePayload` transactions with a garbage/invalid `VerificationData` and a self-chosen `UniversalPayload` with a high `MaxFeePerGas`. As long as the resulting EVM call returns a receipt with nonzero `GasUsed` (any revert past signature-recovery/dispatch inside the UEA contract typically does), Push Chain burns real `upc` from the **victim's UEA balance** — funds the attacker never owned and never had authorization to spend — with no cost to the attacker (the message is gasless per `app/txpolicy/gasless.go:19`). This is a temporary/permanent freezing and unauthorized burn of protocol/user-controlled funds, matching the in-scope "unauthorized burn ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting" impacts, directly mirroring the external report's pattern of using someone else's owned resource without an ownership/authorization check gating the effect.

### Likelihood Explanation
High reachability: `MsgExecutePayload` is a gasless, permissionless, "any user" message per the module's own documentation table (`x/uexecutor/README.md:204`), requiring no prior relationship with the victim beyond knowing their `UniversalAccountId`. The attacker pays nothing (gasless whitelist), and can repeat the call indefinitely to drain a targeted UEA in increments up to whatever `GasUsed` the revert path produces each time. Exact confirmation of how much gas a failing `CallUEAExecutePayload` revert reports (and whether it's consistently nonzero) requires runtime verification against the compiled `UEA_EVM.sol`/`UEA_SVM.sol` bytecode, which was not directly inspectable here — this is the main open uncertainty.

### Recommendation
Mirror the `ExecutePayloadV2` pattern in the keeper method actually used by `MsgExecutePayload`: wrap `CallUEAExecutePayload` and `DeductGasFeesFromReceipt` in a `CacheContext`, and only call `writeCache()` when `execErr == nil`. This ensures gas is deducted from the UEA only for payloads that pass the contract's signature/authorization check, consistent with the module's stated invariant that "No state changes survive a failed signature check."

### Proof of Concept
Not independently executed in this session due to tool limitations (no code execution/build access). A concrete PoC would:
1. Deploy/derive a victim `UniversalAccountId` UEA and fund it with `upc`.
2. As an unrelated attacker account, submit `MsgExecutePayload` with the victim's `UniversalAccountId`, an attacker-chosen `UniversalPayload` (high `MaxFeePerGas`/`GasLimit`), and an invalid `VerificationData`.
3. Observe `ms.ExecutePayload` returning an error (signature check failed) while `chainApp.BankKeeper.GetBalance(ctx, ueaAccAddr, "upc")` has decreased — confirming funds were burned from the victim despite the authorization failure.

This should be validated on the actual repository/build (via a Devin session with code execution) to confirm the `GasUsed` behavior of `CallUEAExecutePayload` on revert and to quantify the exact drainable amount per call.