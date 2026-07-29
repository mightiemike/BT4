### Title
Unauthorized cross-account payload execution via `IsCEA` inbound bypasses UEA signature verification and drains arbitrary Universal Executor Accounts - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/execute_payload.go`)

### Summary
For `IsCEA=true` inbounds (`FUNDS_AND_PAYLOAD` / `GAS_AND_PAYLOAD`), the `Recipient` field — the target UEA to execute a payload against — is taken directly from attacker-controlled source-chain event data with **no binding to the inbound `Sender`**. When that recipient resolves to an existing UEA, the keeper runs the payload via `ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, payload, verificationData)` using the `uexecutor` module account as `evmFrom`. Per this repo's own documented authorization model (`x/uexecutor/README.md`), the UEA contract only enforces the owner's signature check when `evmFrom != UNIVERSAL_EXECUTOR_MODULE`; when the module itself is the caller, no signature verification happens. Combined, this lets an unprivileged attacker who merely calls the source-chain gateway (choosing `IsCEA=true`, an arbitrary `Recipient` = victim's UEA, and an arbitrary `UniversalPayload`) get Push Chain's honest validators to relay and finalize a payload that executes **as the victim's UEA**, with no proof the attacker owns that account.

### Finding Description
The intended authorization model (documented in `x/uexecutor/README.md:211-238`) is: `MsgExecutePayload`'s `Signer` need not equal the UEA owner because the **UEA contract itself** cryptographically verifies `VerificationData` against the owner's stored public key whenever the EVM caller (`evmFrom`) is not the trusted `UNIVERSAL_EXECUTOR_MODULE` address. When `evmFrom == UNIVERSAL_EXECUTOR_MODULE`, that check is bypassed by design — this is normally safe because the module always derives the target UEA deterministically from the *actual, validator-observed* `Sender` of the inbound event (see the non-CEA branches of `execute_inbound_funds_and_payload.go:104-158` and `execute_inbound_gas_and_payload.go:101-156`, which call `CallFactoryToGetUEAAddressForOrigin` keyed on `Sender`).

The `IsCEA` path breaks this binding. In both:
- `x/uexecutor/keeper/execute_inbound_funds_and_payload.go:53-102` (and the UEA-path execution at `execute_inbound_funds_and_payload.go:285-290`)
- `x/uexecutor/keeper/execute_inbound_gas_and_payload.go:61-99` (and the UEA-path execution at `execute_inbound_gas_and_payload.go:288-298`)

`ueaAddr` is set to `common.HexToAddress(utx.InboundTx.Recipient)` — a field taken verbatim from the inbound event, which for CEA inbounds is explicitly *not* derived from `Sender` (confirmed by the integration test `isCEA=true uses recipient directly without factory lookup by sender universalAccountId`, `test/integration/uexecutor/inbound_cea_payload_test.go:656-720`, which demonstrates a "different sender" with no owned UEA can still target an arbitrary, pre-existing UEA). `ValidateForExecution` (`x/uexecutor/types/inbound.go:126-175`) only checks that `Recipient` is a syntactically valid hex address for `IsCEA` inbounds — it never checks any relationship between `Sender` and `Recipient`.

Once the UEA-check (`CallFactoryGetOriginForUEA`) confirms `Recipient` is *some* deployed UEA (any UEA, belonging to any account, not necessarily the caller), the keeper unconditionally calls `k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)` — `evmFrom` is the module account, so per the documented model the UEA's owner-signature check is skipped. `utx.InboundTx.UniversalPayload` (attacker-supplied `To`/`Data`/`Value`) then executes **from the victim's UEA as `msg.sender`**. There is no code path in these files that verifies `VerificationData` actually authorizes this specific `Recipient`/payload combination when the CEA branch is taken.

This is structurally identical to the H-01 report: a function accepts an "owner"/"recipient" parameter that is trusted to belong to the caller but is never checked against the caller's actual identity, and the underlying protection (owner signature / allowance check) is bypassed because the call is routed through a "trusted" intermediary (the vault in H-01; the `UNIVERSAL_EXECUTOR_MODULE` here) that itself performs no ownership check.

### Impact Explanation
An attacker who has never interacted with a victim's UEA can:
1. Observe (or already know) any deployed UEA address on Push Chain holding PRC20 balances (e.g. from an earlier legitimate deposit).
2. Submit an ordinary transaction to the source-chain gateway contract that emits a `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` event with `IsCEA=true`, `Recipient = victimUEA`, and `UniversalPayload = { To: prc20TokenAddr, Data: transfer(attacker, balance) }`.
3. Wait for honest Universal Validators to observe this (real, on-chain) event and vote it via `MsgVoteInbound` — the UVs are only attesting that the event occurred, which it did.
4. Once quorum is reached, Push Chain's keeper executes the payload through the module account directly against `victimUEA`, bypassing the UEA's owner-signature check, transferring the victim's PRC20 balance to the attacker.

This is a direct, unauthorized draining of user-controlled PRC20 funds by an unprivileged external attacker, matching the "stealing/draining of user-controlled funds" and "unauthorized module-originated EVM execution" categories in scope.

### Likelihood Explanation
High. The trigger requires only an ordinary (unprivileged) transaction on a supported source chain calling the existing gateway contract with attacker-chosen `Recipient`/payload fields and `IsCEA=true` — no validator, admin, or key compromise is needed. Validators behave honestly and simply relay what actually happened on-chain, which is exactly the exploit condition. The integration test suite already demonstrates that arbitrary `Recipient` values (unrelated to `Sender`) are accepted and processed by the `IsCEA` code path.

### Recommendation
For `IsCEA` inbounds whose `Recipient` resolves to an existing UEA, do not execute the attached `UniversalPayload` through the trusted module sender unless `VerificationData` is independently verified against the UEA's owner (i.e., route through the same signature-checked path used for regular `MsgExecutePayload`/non-CEA execution, or explicitly disallow arbitrary third-party UEAs as CEA targets — only fund/deposit into them, never execute payloads on their behalf unless cryptographically authorized). At minimum, require that CEA-targeted UEA payload execution supply valid `VerificationData` that the UEA contract will check (i.e., never invoke `ExecutePayloadV2` with `evmFrom = UNIVERSAL_EXECUTOR_MODULE` for a `Recipient` that was not derived from the inbound's own authenticated `Sender`).

### Proof of Concept
1. Attacker deploys/knows a victim UEA (`0xVictimUEA`) that holds PRC20 token balance `T`.
2. Attacker calls the source-chain gateway with a `FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`) call specifying:
   - `Sender = attacker's own address`
   - `Recipient = 0xVictimUEA`
   - `IsCEA = true`
   - `UniversalPayload.To = T` (the PRC20 token contract)
   - `UniversalPayload.Data = transfer(attacker, victimBalance)`
   - `VerificationData = ""` (irrelevant/arbitrary)
3. 3 honest UVs vote `MsgVoteInbound` with the observed (real) event data; quorum passes.
4. `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` resolves `Recipient` as a valid UEA (`CallFactoryGetOriginForUEA` returns true) and calls `ExecutePayloadV2(ctx, ueModuleAddr, 0xVictimUEA, payload, "")`.
5. `CallUEAExecutePayload` dispatches `executeUniversalTx` with `evmFrom = UNIVERSAL_EXECUTOR_MODULE`; per the documented model, the UEA skips the owner-signature check for this caller and executes `T.transfer(attacker, victimBalance)` as `msg.sender = 0xVictimUEA`, draining the victim's tokens to the attacker.

Note: full confirmation that `UEA_EVM.sol`'s `executeUniversalTx` truly skips signature verification when `msg.sender == UNIVERSAL_EXECUTOR_MODULE` requires reading the `push-chain-core-contracts` repository (out of scope of the indexed code here); this analysis relies on the explicit statement of that behavior in `x/uexecutor/README.md:229-237` within this repository.