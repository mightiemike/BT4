### Title
Incorrect `sender` recorded on refund/revert `PCTx` entries corrupts canonical `UniversalTx` audit state - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
This is the same bug class as the C4 finding: a cross-chain accounting struct records the wrong "sender" — the contract/module address that technically executed the on-chain action instead of the actual originator — while the fund-moving parameters (amount, recipient) are computed correctly elsewhere. In Push Chain's `x/uexecutor` module, `handleFailedOutbound` and `applyGasRefund` construct `types.PCTx{Sender: outbound.Sender, ...}` immediately before issuing a module-originated `DerivedEVMCall` (`CallPRC20Deposit` / `CallUniversalCoreRefundUnusedGas`), whose real on-chain EVM sender is always the `uexecutor` module account (`ueModuleAccAddress`), not `outbound.Sender`.

### Finding Description
Per `x/uexecutor/README.md`, `PCTx.sender` is documented as "who initiated it (user-derived address, or uexecutor module)" [1](#0-0) . `DERIVED_TRANSACTIONS.md` confirms module-originated calls (`CallPRC20Deposit`, `CallUniversalCoreRefundUnusedGas`, etc.) always use `ueModuleAccAddress` as the EVM `from` address [2](#0-1) , confirmed at each call site (e.g. `CallPRC20Deposit`, `CallUniversalCoreRefundUnusedGas`) which explicitly pass `ueModuleAccAddress` as sender [3](#0-2) [4](#0-3) .

However, in `handleFailedOutbound` (funds re-mint on revert) and `applyGasRefund` (excess-gas refund), the constructed `PCTx` record is populated with `Sender: outbound.Sender` — the original outbound's sender field (the user/UEA address from the source event) — instead of the module account that actually executed the `DerivedEVMCall`: [5](#0-4) [6](#0-5) 

This mismatches the documented semantics of `PCTx.sender` and produces a canonical `UniversalTx` record whose `pc_tx[].sender` field for these module-driven entries does not reflect the actual EVM signer of the corresponding tx hash — exactly the "struct stores incorrect sender while other fields (recipient/amount) are correct" pattern from the C4 report.

### Impact Explanation
The actual funds movement (recipient address, amount, and asset) is computed independently and correctly in `recipient`/`recipientAddr` in both functions, so no direct fund loss results from this specific mismatch alone — mirroring why the original finding was downgraded from High to Medium. The impact here falls under the explicitly allowed "corruption of ... canonical UniversalTx state" category: the append-only `UniversalTx.PcTx[]` audit trail — which is the single source of truth read by Universal Validators, indexers, the JSON-RPC layer, and the explorer (per the module README) — will permanently and irreversibly (the UTX is "append-mostly... nothing is overwritten") record a false sender for module-executed revert/refund transactions. Any off-chain reconciliation, dispute resolution, or automated tooling that trusts `PcTx.sender` to identify which EVM address actually produced a given tx hash will draw incorrect conclusions for every reverted/refunded outbound.

### Likelihood Explanation
This triggers on every ordinary, unprivileged flow that reaches a failed outbound or an outbound with excess gas: any outbound that Universal Validators observe as failed (`handleFailedOutbound`) or that has unused gas (`applyGasRefund`) will always produce this mismatched record — no privileged action or validator misbehavior is required, and it can be triggered by an ordinary user's cross-chain payload that results in a destination-chain revert.

### Recommendation
In both `handleFailedOutbound` and `applyGasRefund` (`x/uexecutor/keeper/outbound.go`), set `PCTx.Sender` to the actual EVM `from` address used for the `DerivedEVMCall` (`ueModuleAccAddress`, obtainable via `k.GetUeModuleAddress(ctx)`) rather than `outbound.Sender`, consistent with how other module-originated `PCTx` entries elsewhere in the codebase should record the true executing address.

### Proof of Concept
1. A user's cross-chain payload creates an `OutboundTx` with `Sender = <UEA/user address>`.
2. Universal Validators vote the outbound as failed via `MsgVoteOutbound`, invoking `handleFailedOutbound`.
3. `handleFailedOutbound` calls `k.CallPRC20Deposit(ctx, ..., recipient, amount)`, which internally issues a `DerivedEVMCall` with `from = ueModuleAccAddress` [7](#0-6) .
4. Simultaneously, the code builds `pcTx := types.PCTx{Sender: outbound.Sender, ...}` [8](#0-7)  and appends it (via `outbound.PcRevertExecution = &pcTx` and `k.UpdateOutbound`) to the immutable, canonical `UniversalTx.PcTx[]` record.
5. Querying `GetUniversalTx` for this UTX shows a `PcTx` entry whose `tx_hash` corresponds to a real EVM transaction signed/sent by the `uexecutor` module account, but whose recorded `sender` field is the unrelated user/UEA address — a permanent, unfixable discrepancy in the canonical on-chain record.

Note: I was unable to fully verify within the available iterations whether any downstream consumer (e.g. `query_server_v2.go` or off-chain indexers referenced in the README) makes trust decisions based on `PcTx.sender` beyond display/audit purposes; if such logic exists, the severity could be higher than Medium.

### Citations

**File:** x/uexecutor/README.md (L70-77)
```markdown
message PCTx {
  string tx_hash      = 1;  // hash of the EVM tx the core validator produced (DerivedEVMCall)
  string sender       = 2;  // who initiated it (user-derived address, or uexecutor module)
  uint64 gas_used     = 3;  // populated from the tx receipt
  uint64 block_height = 4;  // Push Chain block this was committed in
  string status       = 6;  // "SUCCESS" or "FAILED"
  string error_msg    = 7;  // populated when status == "FAILED"
}
```

**File:** DERIVED_TRANSACTIONS.md (L106-129)
```markdown
### 2. Module-as-sender (protocol-initiated EVM work)

When `x/uexecutor` itself needs to issue an EVM call (deposit PRC20s, push chain-meta, refund unused gas, ...) the sender is the `uexecutor` module account. Module accounts don't have private keys, so this would be impossible via a normal `MsgEthereumTx` — you can't sign one. `DerivedEVMCall` with `isModuleSender=true` solves it:

```go
ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)
nonce, _ := k.GetModuleAccountNonce(ctx)
_, _ = k.IncrementModuleAccountNonce(ctx)

return k.evmKeeper.DerivedEVMCall(
    ctx,
    abi,
    ueModuleAccAddress, // module account as sender
    handlerAddr,
    big.NewInt(0),
    nil,
    true,               // commit
    false,              // gasless = false (we still want gas in the receipt)
    true,               // isModuleSender = true
    &nonce,             // manualNonce = explicit
    "depositPRC20Token",
    prc20Address, amount, to,
)
```
```

**File:** x/uexecutor/keeper/evm.go (L274-302)
```go
	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
```

**File:** x/uexecutor/keeper/evm.go (L613-644)
```go
	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	// fee is uint24 in Solidity — pass as *big.Int (go-ethereum ABI packs non-standard widths as *big.Int)
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"refundUnusedGas",
		gasToken,
		amount,
		recipient,
		withSwap,
		fee,
		minPCOut,
	)
}
```

**File:** x/uexecutor/keeper/outbound.go (L107-124)
```go
		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
```

**File:** x/uexecutor/keeper/outbound.go (L201-211)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}
```
