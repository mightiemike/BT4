### Title
Attacker-controlled donations to the old TSS address can desynchronize independently-recomputed fund-migration signing hashes, stalling `FUND_MIGRATE` consensus - (File: `universalClient/tss/coordinator/coordinator.go`, `universalClient/tss/sessionmanager/sessionmanager.go`, `universalClient/chains/evm/tx_builder.go`)

### Summary
The external `LiquidityManager.moveLiquidity()` bug is a case where a raw, attacker-inflatable `balanceOf()` read is used to compute a value (`liquidityAmount`) that a downstream step then relies on being consistent/available, causing reverts when an attacker donates extra tokens into the contract. Push Chain's fund-migration flow has a structurally similar dependency: the amount to sweep from the old TSS address to the new TSS address is derived from a **live, independently-queried native balance** (`computeFundMigrationTransfer` in `universalClient/chains/evm/tx_builder.go:590-607`), and this balance is queried separately by the coordinator when building the signing setup (`createFundMigrationSignSetup` → `buildFundMigrationTransaction(..., claimedAmount: nil)` in `universalClient/tss/coordinator/coordinator.go:582-671`) and again, independently, by every participating validator when verifying that setup (`verifyFundMigrationSigningRequest` in `universalClient/tss/sessionmanager/sessionmanager.go:980-1082`, which calls `builder.GetFundMigrationSigningRequest` with `Balance: nil`, forcing a fresh on-chain query).

### Finding Description
`computeFundMigrationTransfer` derives the funds to move as `balance - (gasPrice*gasLimit) - l1GasFee`, where `balance` is read live from the external chain via `RPCClient.GetBalance` (`universalClient/chains/evm/rpc_client.go:172-183`) unless a `claimedAmount` is explicitly supplied. The signing hash produced from this value is what the DKLS TSS session actually signs.

The code comments make clear the team anticipated *one* race — a completed sweep transaction driving `balance` to zero between the coordinator's initial query and a later ACK/verify pass — and added a `claimedAmount` reconstruction path specifically to avoid that race (`buildFundMigrationTransaction` comment, coordinator.go:615-619). However, no equivalent protection exists for the opposite race: any unprivileged actor can send additional native currency to the old TSS address (a fully public, well-known EOA address once fund migration begins) at any time. Because:
- the coordinator's `createFundMigrationSignSetup` queries balance with `claimedAmount=nil` (coordinator.go:594), and
- each verifying validator's `verifyFundMigrationSigningRequest` independently re-queries balance with `Balance: nil` (sessionmanager.go:1041-1048),

these two (or more, across multiple validators) RPC calls happen at different wall-clock times / block heights. If an attacker's donation transaction to the old TSS address lands on-chain between the coordinator's query and a validator's verification query (or between two validators' verification queries), the computed `balance`, hence `maxTransfer`, hence the `SigningHash`, will differ. `verifyFundMigrationSigningRequest` explicitly rejects on any hash mismatch:
```
if !bytes.Equal(signingReq.SigningHash, req.SigningHash) {
    ... return fmt.Errorf("fund migration signing hash mismatch...")
}
```
This causes the validator to refuse to sign, which — repeated by a determined low-cost attacker who can time donations against the migration window — can indefinitely stall convergence of the DKLS threshold-signing round for `FUND_MIGRATE`, exactly analogous to how injected tokens caused `LiquidityManager.moveLiquidity()` to revert.

### Impact Explanation
Fund migration is triggered only after outbound is disabled and pending outbounds are drained for a chain (`x/utss/keeper/msg_initiate_fund_migration.go:31-47`), meaning it is a safety-critical one-shot operation for relocating all funds from a rotated-out TSS key. A stalled migration leaves locked funds on the old TSS key indefinitely un-migrated, which is a denial-of-service against a core, unprivileged-reachable protocol operation (the attacker only needs to be able to send a plain value transfer to a known public address on an external chain — no privileged access required). It does not, on its own, permit fund theft, since no signature ever validates against a wrong/attacker amount (the hash-mismatch check fails closed), but it can indefinitely block legitimate protocol state progression.

### Likelihood Explanation
Medium-to-low. Exploitation requires precise timing: the attacker's donation transaction must land on the external chain in the narrow window between two independent balance queries made by different validator processes (or the coordinator and a validator). This is inherently probabilistic and costs the attacker real funds (which are not lost — they get swept along with everything else once migration eventually succeeds), similar to the original report's likelihood characterization ("expensive... unlikely attack... difficult to sustain constantly"). Push Chain nodes are Cosmos SDK/Tendermint validators (not necessarily lacking a private mempool like the original OP-stack context), and the exact quorum/retry semantics of the DKLS session (whether a single mismatch triggers full session abort vs. localized non-participation) were not verified in this pass — this affects how sustainable the stall is and should be independently confirmed.

### Recommendation
Do not have each participant independently re-query live balance to reconstruct the signing amount for verification. Instead:
- Have the coordinator commit to a specific balance/amount and block height at initiation, propagate the exact `claimedAmount` (with the source block height) as part of the DKLS setup payload, and have all participants verify **that value** (optionally cross-checked against their own on-chain query for sanity, but not used to independently reconstruct the hash) — mirroring the `claimedAmount` reconstruction already used for the ACK path.
- Alternatively, pin the balance read to a specific, agreed-upon finalized block height shared by coordinator and all verifiers, so an attacker cannot straddle a donation between two queries.

### Proof of Concept
Not independently reproduced in this pass (no test harness run); the vulnerability is inferred from static analysis of:
- `universalClient/chains/evm/tx_builder.go:590-607` (`computeFundMigrationTransfer`, balance-based amount derivation)
- `universalClient/tss/coordinator/coordinator.go:580-671` (`createFundMigrationSignSetup` / `buildFundMigrationTransaction`, live balance query with `claimedAmount=nil`)
- `universalClient/tss/sessionmanager/sessionmanager.go:978-1082` (`verifyFundMigrationSigningRequest`, independent live balance re-query and strict hash-equality rejection)

A concrete reproduction would require: (1) standing up the fund-migration flow with ≥2 universal validators against a local/test EVM chain, (2) injecting a native-token transfer to the derived old-TSS address between the coordinator's setup-message construction and a second validator's `verifyFundMigrationSigningRequest` call, and (3) confirming the resulting `"fund migration signing hash mismatch"` error is returned, blocking that validator's participation in the round. This scenario was not executed due to tooling constraints (read-only codebase access); the report should be validated by a background Devin session with full build/test tooling if pursued further.