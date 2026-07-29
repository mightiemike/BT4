Based on my investigation, the rescue-eligibility check explicitly requires that the CEA deposit itself (`originalUtx.PcTx[0]`) must have `Status != "FAILED"` in order to skip rescue — i.e. rescue is only triggered when the *first* PRC20 deposit step fails, meaning funds never landed on Push Chain at all: [1](#0-0) .

In the `isCEA` smart-contract path, however, the flow is: (1) `depositPRC20` mints/deposits PRC20 into the non-UEA recipient address unconditionally as long as it "has code", and only *after* that succeeds does it attempt `CallExecuteUniversalTx` to invoke `executeUniversalTx` on that contract [2](#0-1) [3](#0-2) . There is no check analogous to ERC721's `onERC721Received`/interface-support check confirming the destination contract actually implements `executeUniversalTx` (or PRC20 handling) before the deposit is committed — the "smart contract" classification is based purely on `GetCodeHash` returning non-empty bytecode [4](#0-3) .

If the deposit (`depositPcTx.Status`) succeeds but the subsequent `executeUniversalTx` call fails/reverts (`contractErr != nil` or the callee simply lacks that function selector) because the recipient contract was never designed to receive/route PRC20, the code path only records a `callPcTx.Status = "FAILED"` entry and returns — no `INBOUND_REVERT` outbound is created for isCEA failures by design (`"isCEA failures never create an INBOUND_REVERT outbound"`) [5](#0-4) [6](#0-5) . And because the rescue-eligibility gate for CEA inbounds specifically requires `PcTx[0].Status == "FAILED"` (the *deposit* PCTx, index 0), a scenario where the deposit succeeds but the interface-less contract call fails does **not** satisfy the rescue condition, since `PcTx[0]` (the deposit) is `SUCCESS`, not `FAILED` [7](#0-6) .

However, I was not able to fully confirm from the indexed code whether the deployed `UniversalCore`/PRC20 handler contracts themselves enforce any interface check (e.g., ERC165 `supportsInterface`) before minting to an arbitrary contract address, since that logic lives in the Solidity contracts referenced by ABI only (`x/uexecutor/types/abi.go`) and their source is in a separate `push-chain-core-contracts` repository not indexed here. This is a material gap: the actual on-chain guard (or lack of one) inside `depositPRC20Token`/`UniversalCore` Solidity code determines whether this is fully exploitable or partially mitigated at the contract layer.

### Title
Unconditional PRC20 deposit to non-UEA recipients before verifying they support `executeUniversalTx`, with no rescue path once the deposit itself succeeds - (File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go)

### Summary
For `isCEA` inbound transfers where the recipient is a plain smart contract (not a UEA), `x/uexecutor` deposits PRC20 tokens into that contract based solely on `GetCodeHash` returning non-empty bytecode, then separately attempts to call `executeUniversalTx` on it. There is no upfront verification (analogous to ERC721's `onERC721Received` check) that the target contract actually implements the `executeUniversalTx` interface or has any way to move/use the deposited PRC20 tokens. If the deposit succeeds but the follow-up call fails because the contract doesn't implement the expected interface, the funds are stuck in that contract with no automatic revert and no rescue eligibility, because the rescue-eligibility check for CEA inbounds is gated on the *deposit* PCTx (`PcTx[0]`) status being `FAILED`, which it is not in this scenario.

### Finding Description
In `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload`, when `IsCEA` is true and the recipient resolves to neither a UEA (per `CallFactoryGetOriginForUEA`) it is classified `isSmartContract` purely by `k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr) != EmptyCodeHash` [8](#0-7) . Regardless of this classification, `depositPRC20` is executed unconditionally when `inboundAmount.Sign() > 0`, minting real PRC20 value into the recipient address [9](#0-8) . Only afterward, in a separate step gated on `isSmartContract`, does the code attempt to invoke `executeUniversalTx` on the recipient via `CallExecuteUniversalTx` [3](#0-2) . Any contract with deployed bytecode — including one that has no `executeUniversalTx` selector, no way to transfer out ERC20-like tokens, or is otherwise incompatible — passes the `isSmartContract` gate and receives the deposit before the interface mismatch is discovered.

The isCEA branch is explicitly documented as never creating an `INBOUND_REVERT` outbound on failure [5](#0-4) , and the failed `executeUniversalTx` call is only recorded as a `FAILED` `PCTx` entry, with no fund-movement consequence [6](#0-5) . The dedicated rescue mechanism (`AttachRescueOutboundFromReceipt`) exists to recover stuck funds, but for CEA inbounds it only activates when the *first* `PcTx` entry (the deposit) has `Status == "FAILED"` [7](#0-6) . In the scenario above, the deposit succeeded (`PcTx[0].Status == "SUCCESS"`), so this rescue path cannot be triggered, and the funds remain locked in the non-conformant contract on Push Chain with no built-in recovery route.

### Impact Explanation
This is a permanent-loss/freezing-of-funds scenario reachable by any unprivileged actor: any inbound source-chain transaction whose sender designates (via the CEA `Recipient` field, which is attacker-controlled since it originates from the source-chain gateway event) an arbitrary contract address on Push Chain that has bytecode but does not implement `executeUniversalTx` will cause the bridged PRC20 value to be minted into that contract with no automated way to move it back out. This matches the "permanent freezing ... of user or protocol-controlled funds" impact category.

### Likelihood Explanation
Likelihood is high for accidental occurrence (any misconfigured or incompatible destination contract triggers it) and plausible for deliberate griefing/self-inflicted loss by a user who mistakenly or intentionally sets `Recipient` to a non-UEA contract. It requires no privileged access — an ordinary CEA inbound (attacker/user-originated cross-chain deposit) is sufficient.

### Recommendation
Before executing `depositPRC20` for the non-UEA `isSmartContract` case, verify that the recipient contract actually supports the expected interface (e.g., a lightweight `supportsInterface`/selector-existence check, or perform the `executeUniversalTx` call first and only deposit if it can succeed, or combine deposit + call atomically via `CacheContext` so a failed `executeUniversalTx` also rolls back the deposit). Additionally, extend the rescue-eligibility check in `AttachRescueOutboundFromReceipt` to also cover the case where the deposit succeeded but the subsequent `executeUniversalTx` call failed for a CEA inbound, so funds aren't permanently stranded with no recovery path.

### Proof of Concept
1. Attacker/user triggers a source-chain gateway event with `TxType_FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`), `IsCEA = true`, and `Recipient` set to a deployed Push Chain contract address that has bytecode but does not implement `executeUniversalTx` (e.g., a plain ERC20 or an unrelated contract).
2. Universal Validators vote in the inbound; `ExecuteInboundFundsAndPayload` resolves `Recipient` — `CallFactoryGetOriginForUEA` returns `isUEA = false`; `GetCodeHash` shows the address has code, so `isSmartContract = true` [8](#0-7) .
3. `depositPRC20` mints the bridged PRC20 amount into that contract and records `depositPcTx.Status = "SUCCESS"` [9](#0-8) .
4. `CallExecuteUniversalTx` reverts because the contract has no matching function selector; `callPcTx.Status = "FAILED"` is recorded, and the function returns with no outbound created [6](#0-5) .
5. Because `originalUtx.PcTx[0].Status == "SUCCESS"` (the deposit, not the failed call), any attempt to trigger `AttachRescueOutboundFromReceipt` for this UTX fails the CEA eligibility check and returns an error, so no rescue outbound can be created [7](#0-6) .
6. The PRC20 balance remains permanently held by the non-conformant contract with no protocol-level path to move it out.

Note: I could not verify from the indexed repository whether the Solidity-side `UniversalCore`/PRC20 `depositPRC20Token` handler (source not present in this Go-only index) independently enforces any interface check before minting — this would need to be confirmed in the `push-chain-core-contracts` repository or via a full Devin session with file-system access, since the index here only contains the ABI, not the contract source.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L239-250)
```go
		// Rescue eligibility differs by inbound type:
		//
		//  CEA inbounds: the deposit (first PCTx) must have failed, meaning the funds
		//  never arrived on Push Chain and are still locked on the source chain.
		//
		//  Non-CEA inbounds: the auto-generated INBOUND_REVERT outbound must exist and
		//  have reached REVERTED status, meaning TSS could not return the funds to the
		//  source chain and they are stuck (held by the gateway contract or in escrow).
		if originalUtx.InboundTx.IsCEA {
			if len(originalUtx.PcTx) == 0 || originalUtx.PcTx[0] == nil || originalUtx.PcTx[0].Status != "FAILED" {
				return fmt.Errorf("rescue: UTX %s CEA deposit did not fail", originalUtxId)
			}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L81-101)
```go
			} else {
				// Non-UEA: check if recipient has code (smart contract) vs EOA
				codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
				if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
					// Smart contract: will call executeUniversalTx after deposit
					isSmartContract = true
				}
				// EOA: just deposit, skip executeUniversalTx (no contract to call)
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
			}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L103-103)
```go
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L208-257)
```go
	// Smart contract path: call executeUniversalTx and return
	if isSmartContract {
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

			amount := new(big.Int)
			amount, ok := amount.SetString(utx.InboundTx.Amount, 10)
			if !ok {
				contractErr = fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
			} else {
				txId := common.HexToHash(utx.Id)

				var payload []byte
				if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
					payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
				}

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
			}
		}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L259-282)
```go
		callPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		if contractReceipt != nil {
			callPcTx.TxHash = contractReceipt.Hash
			callPcTx.GasUsed = contractReceipt.GasUsed
		}
		switch {
		case contractErr != nil:
			callPcTx.ErrorMsg = contractErr.Error()
		case feeErr != nil:
			callPcTx.ErrorMsg = fmt.Sprintf("gas fee deduction failed: %s", feeErr.Error())
		default:
			callPcTx.Status = "SUCCESS"
		}
		if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &callPcTx)
			return nil
		}); updateErr != nil {
			return updateErr
		}
		return nil
```
