## Analysis

The M-04 bug class is: an untrusted/attacker-controlled contract is invoked through a decode/dispatch path with **no bounded gas budget**, letting it consume disproportionate resources during a routine, low-value operation, and this cost lands on parties who did not consent to pay for it. Push Chain's Go-level analog of that same failure mode is in the `isCEA` inbound-execution path, where `x/uexecutor` calls an attacker-deployed recipient contract with no caller-supplied gas cap.

### Root cause

`CallExecuteUniversalTx` in [1](#0-0)  issues a module-originated `DerivedEVMCall` against `recipientAddr` — an address that is fully attacker-controlled (any deployed contract that implements `executeUniversalTx`, verified only via `GetCodeHash != EmptyCodeHash`, see [2](#0-1) ). Unlike `CallUEAExecutePayload`, which forwards an explicit, user-signed `gasLimit` ( [3](#0-2) ), `CallExecuteUniversalTx` always passes `gasLimit=nil` — the inbound's own `UniversalPayload.GasLimit` field is never read or forwarded. Per the fork's own documentation, `gasLimit=nil` means "use a sensible default" ( [4](#0-3) ) — i.e. an internal, uncustomizable default rather than a caller-bounded value.

### Trigger path

An unprivileged attacker can:
1. Deploy an EVM contract on Push Chain implementing `executeUniversalTx(sourceChain, ceaAddress, payload, amount, prc20AssetAddr, txId)` that intentionally burns a very large amount of gas (loop, storage churn, etc.) and holds zero native `upc`.
2. Trigger a trivial deposit from any registered source chain with `IsCEA=true` and `Recipient` set to that malicious contract.
3. Honest Universal Validators observe the deposit and submit `MsgVoteInbound` — this message type is in the **gasless whitelist** ( [5](#0-4) ), so submitters pay no Cosmos fee for it.
4. On quorum, `ExecuteInboundFundsAndPayload` / `ExecuteInboundGasAndPayload` call `CallExecuteUniversalTx` ( [6](#0-5) , [7](#0-6) ) with no gas ceiling tied to anything the depositor declared.
5. The call runs inside a `CacheContext`; if `DeductGasFeesFromReceipt` then fails (recipient has no `upc` to pay for the gas it just burned), the state changes are discarded — but the computational work of executing the attacker's heavy contract has already been performed by every node finalizing that block, for a cost the attacker never pays.

Because the entry point (`MsgVoteInbound`) is gasless and the deposit amount can be arbitrarily small, this is a cheap, repeatable way for an unprivileged actor to force honest core-validator nodes to spend large, uncapped amounts of compute on every finalized inbound, without any of the normal EVM gas-fee disincentive applying to the attacker.

### Title
Uncapped gas limit on attacker-controlled CEA-recipient `executeUniversalTx` calls enables free, repeatable compute-exhaustion DoS - (File: `x/uexecutor/keeper/evm.go`)

### Summary
`CallExecuteUniversalTx` always passes `gasLimit=nil` to `DerivedEVMCall`, ignoring the inbound's own declared gas budget, when calling an attacker-supplied recipient contract for `isCEA` inbounds.

### Finding Description
`CallExecuteUniversalTx` ( [8](#0-7) ) is the only module-originated call in `evm.go` that targets a fully external/attacker-chosen address (`recipientAddr`) while supplying no explicit `gasLimit`. Every other module-originated call in the same file targets a fixed, protocol-owned contract (`UNIVERSAL_CORE`) whose behavior is trusted; `CallExecuteUniversalTx` is the one exception that invokes arbitrary user-deployed bytecode. The check that gates this path only verifies the recipient has *some* code ( [9](#0-8) ) — it does not, and cannot, vet what that code does. With no explicit cap, the call falls back to whatever the fork's "sensible default" is, decoupled from the size of the deposit or the payload that triggered it.

### Impact Explanation
An unprivileged attacker can deploy a gas-heavy `executeUniversalTx` implementation and trigger it via a trivial cross-chain deposit. Because `MsgVoteInbound` is gasless (no Cosmos tx fee) and the recipient itself can be funded with zero native `upc` (so any post-hoc gas-fee deduction simply rolls back via the `CacheContext`, per the F-2026-16738 mitigation), the honest core validators still perform the expensive EVM execution during ballot finalization at no cost to the attacker. This is a repeatable resource-exhaustion vector against every node finalizing inbounds, reachable purely through ordinary unprivileged deposit + honest-validator voting — no malicious validator, peer, or admin action required.

### Likelihood Explanation
Likely to be exploitable in practice: deploying a gas-heavy contract and sending a minimal cross-chain deposit are both entirely within reach of an ordinary user, and the flow (`isCEA` inbound to a smart-contract recipient) is a documented, tested code path (see `test/integration/uexecutor/inbound_cea_smart_contract_test.go`). The only mitigating factor is the block gas limit implicitly bounding a single call's cost, but nothing stops an attacker from repeating the trigger across many blocks/inbounds cheaply.

### Recommendation
Bound `CallExecuteUniversalTx`'s EVM execution the same way `CallUEAExecutePayload` is bounded: derive an explicit, protocol-enforced `gasLimit` (e.g. from `UniversalPayload.GasLimit` clamped to a governance-set maximum, or a fixed conservative constant) and pass it into `DerivedEVMCall` instead of `nil`, so a malicious recipient contract cannot consume more compute than the protocol is willing to fund for that class of call.

### Proof of Concept
1. Deploy `MaliciousRecipient` implementing `executeUniversalTx(...)` with an expensive loop/storage write and zero `upc` balance.
2. Bridge a minimal amount from a registered source chain with `IsCEA=true`, `Recipient=MaliciousRecipient`.
3. Have 3 of 4 UVs submit `MsgVoteInbound` (gasless, as in `test/integration/uexecutor/inbound_cea_smart_contract_test.go`).
4. Observe `CallExecuteUniversalTx` runs with no `gasLimit`, consuming excessive gas (compare to the existing test asserting `GasUsed > 0` with an intentionally light recipient, e.g. [10](#0-9) ), while `DeductGasFeesFromReceipt` fails and rolls back state — the attacker pays nothing but the network still performed the work.

### Citations

**File:** x/uexecutor/keeper/evm.go (L172-192)
```go
	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
```

**File:** x/uexecutor/keeper/evm.go (L646-692)
```go
// CallExecuteUniversalTx calls executeUniversalTx on a smart-contract recipient.
// This is used for isCEA inbounds whose recipient is a deployed contract (not a UEA).
func (k Keeper) CallExecuteUniversalTx(
	ctx sdk.Context,
	recipientAddr common.Address,
	sourceChain string,
	ceaAddress []byte,
	payload []byte,
	amount *big.Int,
	prc20AssetAddr common.Address,
	txId [32]byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	recipientABI, err := types.ParseRecipientContractABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse recipient contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		recipientABI,
		ueModuleAccAddress,
		recipientAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"executeUniversalTx",
		sourceChain,
		ceaAddress,
		payload,
		amount,
		prc20AssetAddr,
		txId,
	)
}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L81-87)
```go
			} else {
				// Non-UEA: check if recipient has code (smart contract) vs EOA
				codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
				if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
					// Smart contract: will call executeUniversalTx after deposit
					isSmartContract = true
				}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L233-255)
```go
				// Wrap the EVM call + fee deduction in a CacheContext so they
				// commit/revert together. If fee deduction fails, the EVM state
				// changes from executeUniversalTx are discarded — closes the
				// free-execution gap when the recipient contract has no native
				// UPC to cover gas. The deposit (above this scope) stays
				// committed regardless.
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
```

**File:** DERIVED_TRANSACTIONS.md (L42-59)
```markdown
    value, gasLimit *big.Int,
    commit, gasless, isModuleSender bool,
    manualNonce *uint64,
    method string,
    args ...interface{},
) (*types.MsgEthereumTxResponse, error)
```

Defined on the Push Chain `EVMKeeper` interface in [`x/uexecutor/types/expected_keepers.go`](./x/uexecutor/types/expected_keepers.go).

| Parameter | Purpose |
|---|---|
| `ctx` | SDK context — provides block, gas meter, store access |
| `abi` | Parsed contract ABI for encoding the call |
| `from` | The EVM address that will appear as the tx sender. Can be a derived user address or a module account address. |
| `contract` | Destination contract |
| `value` | Native value to attach (`*big.Int`, may be `nil` or `big.NewInt(0)`) |
| `gasLimit` | Explicit gas limit (`nil` -> use a sensible default). Critical for predictable receipts. |
```

**File:** app/txpolicy/gasless.go (L12-26)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L233-256)
```go
		// Wrap the EVM call + fee deduction in a CacheContext so they
		// commit/revert together. If fee deduction fails, the EVM state
		// changes from executeUniversalTx are discarded — closes the
		// free-execution gap when the recipient contract has no native
		// UPC to cover gas.
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)

		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L336-352)
```go
		// Verify executeUniversalTx PCTx has gas_used > 0
		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)
		require.GreaterOrEqual(t, len(utx.PcTx), 2, "should have deposit + executeUniversalTx PCTxs")

		callPcTx := utx.PcTx[1]
		require.Equal(t, "SUCCESS", callPcTx.Status)
		require.Greater(t, callPcTx.GasUsed, uint64(0), "executeUniversalTx should report gas used")

		// Verify upc balance decreased (gas was deducted)
		balanceAfter := chainApp.BankKeeper.GetBalance(ctx, contractAccAddr, "upc")
		require.True(t, balanceAfter.Amount.LT(balanceBefore.Amount),
			"smart contract upc balance should decrease after gas fee deduction (before=%s, after=%s)",
			balanceBefore.Amount, balanceAfter.Amount)
	})
```
