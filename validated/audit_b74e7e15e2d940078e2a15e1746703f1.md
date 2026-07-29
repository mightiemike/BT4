### Title
Unauthorized Fund Redirection via Revert Recipient Manipulation - ([File: x/uexecutor/keeper/msg_vote_inbound.go])

### Summary
A vulnerability exists in the `x/uexecutor` module where a malicious actor can redirect reverted funds from a victim's cross-chain transaction to an attacker-controlled address. This occurs because the `Inbound` transaction's `RevertInstructions`, which contain the `fund_recipient` address, are provided by the observer (the Universal Validator) and are not validated against the original source-chain sender. An attacker who can influence the `Inbound` variant that reaches consensus can set a malicious `fund_recipient`, causing funds to be sent to them if the transaction fails or is reverted on Push Chain.

### Finding Description
The Push Chain `x/uexecutor` module processes cross-chain inbounds by tallying votes from Universal Validators (UVs). Each UV submits a `MsgVoteInbound` containing an `Inbound` struct [1](#0-0) . This struct includes `RevertInstructions`, which define a `fund_recipient` [2](#0-1) .

When an inbound ballot finalizes, the `Inbound` data from the winning variant is used to create a `UniversalTx` [3](#0-2) . If the execution of this inbound fails (e.g., due to invalid payload or UEA issues), the system triggers a revert flow using `buildRevertOutbound` [4](#0-3) . 

The core issue is that the `fund_recipient` is taken directly from the `Inbound` struct without verifying that it matches the source-chain `sender`. In the `handleFailedOutbound` logic, the system explicitly prefers the `fund_recipient` from `RevertInstructions` over the original `sender` [5](#0-4) .

While UVs are assumed to be honest in the "Honest Validator" model, the system's "variant-aware" design [6](#0-5)  allows multiple versions of the same logical transaction to exist. If an attacker can convince enough validators to vote for a variant with a modified `fund_recipient` (e.g., via social engineering or by exploiting a bug in the off-chain observation logic of multiple nodes), the protocol will canonically accept the attacker's address as the destination for all future reverts and gas refunds [7](#0-6) .

### Impact Explanation
An attacker can steal funds and gas refunds from legitimate users. If a high-value cross-chain transfer fails for any reason (e.g., target contract revert, insufficient liquidity), the Push Chain protocol will automatically "revert" the funds to the address specified in the `Inbound` variant that reached consensus. By injecting a malicious `fund_recipient`, the attacker ensures that they receive the returned funds instead of the original sender. This results in permanent loss of funds for the user.

### Likelihood Explanation
The likelihood is low under the assumption of honest validators, as it requires 2/3 of the UV set to agree on a malicious variant. However, the protocol's architecture explicitly allows for divergence in observations. If the off-chain `puniversald` clients have a deterministic bug in how they parse `RevertInstructions` or if an attacker can feed malicious RPC data to enough nodes, the consensus mechanism will finalize the poisoned variant.

### Recommendation
The `x/uexecutor` module should enforce that the `fund_recipient` in `RevertInstructions` must be cryptographically bound to the source-chain `sender` or be equal to the `sender` address by default. Validation should be added in `NormalizeForTxType` or `ValidateForExecution` [8](#0-7)  to ensure that if a `fund_recipient` is provided, it matches the authorized party from the source chain.

### Proof of Concept
1. **User A** initiates a cross-chain transfer of 1000 PRC20 tokens from Ethereum to Push Chain.
2. **Attacker** observes the transaction and submits a `MsgVoteInbound` where `Inbound.Sender` is **User A**, but `Inbound.RevertInstructions.fund_recipient` is set to **Attacker Address**.
3. Due to an observation divergence or exploit, 2/3 of validators finalize the variant containing the **Attacker Address**.
4. The execution on Push Chain fails (e.g., the target UEA is not pre-funded or the payload reverts).
5. `ExecuteInboundFunds` calls `buildRevertOutbound` [4](#0-3) .
6. The resulting `OutboundTx` uses the malicious `fund_recipient`.
7. When the outbound is finalized, `handleFailedOutbound` sends the 1000 tokens to the **Attacker Address** [9](#0-8) .

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L18-18)
```go
func (k Keeper) VoteInbound(ctx context.Context, universalValidator sdk.ValAddress, inbound types.Inbound) error {
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L109-114)
```go
	utx := types.UniversalTx{
		Id:         universalTxKey,
		InboundTx:  &inbound,
		PcTx:       nil,
		OutboundTx: nil,
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L136-136)
```go
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
```

**File:** proto/uexecutor/v1/types.proto (L95-100)
```text
message RevertInstructions {
  option (amino.name) = "uexecutor/revert_instructions";
  option (gogoproto.equal) = true;

  string fund_recipient = 1;       // where funds go in revert/refund
}
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L77-77)
```go
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
```

**File:** x/uexecutor/keeper/outbound.go (L108-112)
```go
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}
```

**File:** x/uexecutor/keeper/outbound.go (L201-205)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
```

**File:** x/uexecutor/keeper/inbound.go (L27-32)
```go
func (k Keeper) RecordInboundVote(
	ctx context.Context,
	inbound types.Inbound,
	voter string,
	ballotID string,
) error {
```
