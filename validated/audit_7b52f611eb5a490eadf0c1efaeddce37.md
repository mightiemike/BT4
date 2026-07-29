### Title
Unauthorized outbound (fund drain) creation via forged `UniversalTxOutbound` event reaching `EVMHooks.PostTxProcessing` - ([File: x/uexecutor/keeper/evm_hooks.go])

### Summary
The reported `onERC721Received` reentrancy bug is a Check-Effects-Interactions violation: an external call performed mid-function lets attacker-controlled code trigger a state-mutating callback before the caller has finished updating its own bookkeeping, producing an inconsistent record. The structural analog in this repo is `EVMHooks.PostTxProcessing`, which fires as a callback after **every** EVM transaction (`app/app.go` wires `app.EVMKeeper.SetHooks(uexecutorkeeper.NewEVMHooks(...))`), and unconditionally scans the transaction's logs for a `UniversalTxOutboundEventSig` emitted by the `UNIVERSAL_GATEWAY_PC` address, converting it directly into a new `UniversalTx` + `PendingOutbounds` entry (`x/uexecutor/keeper/create_outbound.go:BuildOutboundsFromReceipt`) — with no verification that the underlying value was actually locked/burned through the honest ballot-voted inbound path.

### Finding Description
`EVMHooks.PostTxProcessing` (`x/uexecutor/keeper/evm_hooks.go:28-67`) is invoked by the cosmos-evm fork after any EVM transaction commits, regardless of whether that transaction originated from a real user's `MsgEthereumTx` or from the module's own `DerivedEVMCall`. It does not check who the top-level `msg.sender`/origin was, nor does it distinguish "this outbound resulted from an inbound that passed 2/3 Universal Validator quorum" from "this outbound resulted from a contract nested-calling the gateway."

The state transition is: any log with `Address == UNIVERSAL_GATEWAY_PC` and `Topics[0] == UniversalTxOutboundEventSig` is decoded (`x/uexecutor/types/gateway_pc_event_decode.go:DecodeUniversalTxOutboundFromLog`) and turned into a `types.OutboundTx` with attacker-influenced `DestinationChain`, `Recipient`, `Amount`, `Prc20AssetAddr`, `RevertInstructions`, etc. (`x/uexecutor/keeper/create_outbound.go:69-91`), which is appended to a UTX and indexed in `PendingOutbounds`. Universal Validators then observe this `PendingOutbound`, sign it via TSS, and broadcast the withdrawal to the destination chain — the same trust path that a legitimately voted inbound uses.

`MsgExecutePayload` is described as "any user, gasless (the UEA itself authenticates the request)" (`x/uexecutor/README.md:199-207`), and its execution path (`CallUEAExecutePayload` in `x/uexecutor/keeper/evm.go`) runs the user-supplied `UniversalPayload` as a real, committed EVM transaction (`commit=true`) via `DerivedEVMCall`. If the payload's target/calldata makes the UEA call into the gateway contract's outbound-emitting function directly (bypassing the legitimate PRC20-burn/fee-deduction code path that the real vault-withdraw flow uses), the resulting receipt still contains a `UniversalTxOutboundEventSig` log from the gateway address, and `PostTxProcessing` cannot tell the difference between that and a genuine, quorum-backed withdrawal.

This mirrors the reported bug's core defect class: a downstream callback (`PostTxProcessing`/`onERC721Received`) is triggered by an interaction that the calling code does not fully control, and the callback mutates the ledger of record (`UniversalTx`/`PendingOutbounds` here, `tokensStaked`/`idToOwner` there) using data from that uncontrolled interaction, producing state that downstream consumers (Universal Validators, TSS signers, indexers) implicitly trust as authoritative.

### Impact Explanation
If the gateway contract's outbound-emitting entry point is reachable by an ordinary user through a self-authenticated `MsgExecutePayload` without going through the actual value-locking/burn logic that legitimate withdrawals perform, this allows unauthorized outbound creation that Universal Validators will sign and broadcast — i.e., unauthorized release/drain of protocol-controlled funds on the destination chain, which is explicitly in the "Allowed Impact" list ("unauthorized release ... of user or protocol-controlled funds", "unauthorized module-originated EVM execution ... in universal execution flows").

### Likelihood Explanation
I was **not able to fully confirm exploitability** with the tools available: the production `UniversalGatewayPC` Solidity contract's actual access control on its withdraw/outbound function is not indexed in this codebase snapshot — only a test double is visible (`test/utils/contracts_setup.go:325-346`), which explicitly documents that its own withdraw functions "DO NOT run validation," "DO NOT burn PRC20 tokens," etc., purely for the purpose of exercising the Cosmos-side pipeline. The real production contract's guards (whether it enforces that only a legitimate PRC20 burn/vault-debit precedes the event emission, and whether it restricts *who* can invoke that code path) live outside `x/`, `precompiles/usigverifier/`, and `universalClient/` (likely in an external Solidity repo not indexed here). Because of this, I cannot state with confidence whether the production gateway blocks a user-payload-driven forged emission. Given the size-limited indexing of this codebase, the actual `UniversalGatewayPC.sol` bytecode/source may not be available to Ask; a full audit of the deployed gateway contract logic is needed to determine if this Cosmos-side trust gap is actually reachable by an unprivileged user.

### Recommendation
- Have `PostTxProcessing` / `BuildOutboundsFromReceipt` only accept `UniversalTxOutbound` events emitted from EVM transactions that the module itself initiated (e.g., only when the enclosing call originated from a `DerivedEVMCall` tied to a specific, already-validated inbound/`UniversalTx`, not from arbitrary user-submitted transactions or payloads).
- Verify, on the Cosmos side, that a matching PRC20 burn/vault debit accompanies the `UniversalTxOutbound` log before creating the `PendingOutbounds` entry, rather than trusting the event data alone.
- Confirm (outside this repo, in the Solidity gateway source) that the function which emits `UniversalTxOutboundEventSig` cannot be invoked by an arbitrary caller/contract without first passing through the legitimate fee/burn logic — this is the load-bearing invariant the Cosmos-side hook currently assumes but cannot verify.

### Proof of Concept
Not fully constructible from this repository alone: doing so requires the production `UniversalGatewayPC` Solidity source/bytecode (not present in the indexed files) to determine whether its outbound-emitting function can be reached by a user-controlled `MsgExecutePayload` call without the offsetting burn/fee logic. The Cosmos-side `PostTxProcessing` -> `BuildOutboundsFromReceipt` path is demonstrably purely log-driven and origin-agnostic (see `test/integration/uexecutor/evm_hooks_and_outbound_test.go:491-574`, where a hand-crafted synthetic log — with no accompanying PRC20 burn or inbound vote — is fed into `PostTxProcessing` and successfully produces a full `UniversalTx` + pending outbound). A background Devin session with access to the full monorepo (including the Solidity contracts repo) would be needed to determine reachability and complete the PoC. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** x/uexecutor/keeper/evm_hooks.go (L25-67)
```go
// PostTxProcessing is called by the EVM module after transaction execution.
// It inspects the receipt and creates UniversalTx + Outbound only if
// UniversalTxWithdraw event is detected.
func (h EVMHooks) PostTxProcessing(
	ctx sdk.Context,
	sender common.Address,
	msg core.Message,
	receipt *ethtypes.Receipt,
) error {
	if receipt == nil || len(receipt.Logs) == 0 {
		return nil
	}

	h.k.Logger().Debug("evm hook post-tx processing",
		"tx_hash", receipt.TxHash.Hex(),
		"sender", sender.Hex(),
		"log_count", len(receipt.Logs),
		"gas_used", receipt.GasUsed,
	)

	protoReceipt := &evmtypes.MsgEthereumTxResponse{
		Hash:    receipt.TxHash.Hex(),
		GasUsed: receipt.GasUsed,
		Logs:    convertReceiptLogs(receipt.Logs),
	}

	// Build pcTx representation
	pcTx := types.PCTx{
		Sender:      sender.Hex(),
		TxHash:      protoReceipt.Hash,
		GasUsed:     protoReceipt.GasUsed,
		BlockHeight: uint64(ctx.BlockHeight()),
		Status:      "SUCCESS",
	}

	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L16-105)
```go
func (k Keeper) BuildOutboundsFromReceipt(
	ctx context.Context,
	utxId string,
	receipt *evmtypes.MsgEthereumTxResponse,
) ([]*types.OutboundTx, error) {

	outbounds := []*types.OutboundTx{}
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	k.Logger().Debug("building outbounds from receipt", "utx_id", utxId, "tx_hash", receipt.Hash, "log_count", len(receipt.Logs))

	for _, lg := range receipt.Logs {
		if lg.Removed {
			continue
		}

		if strings.ToLower(lg.Address) != universalGatewayPC {
			continue
		}

		if len(lg.Topics) == 0 {
			continue
		}

		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.UniversalTxOutboundEventSig) {
			continue
		}

		event, err := types.DecodeUniversalTxOutboundFromLog(lg)
		if err != nil {
			return nil, fmt.Errorf("failed to decode UniversalTxWithdraw: %w", err)
		}

		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}

		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}

		k.Logger().Debug("outbound built from receipt",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"dest_chain", outbound.DestinationChain,
			"amount", outbound.Amount,
			"tx_type", outbound.TxType.String(),
		)
		outbounds = append(outbounds, outbound)
	}

	k.Logger().Debug("outbounds built from receipt", "utx_id", utxId, "count", len(outbounds))
	return outbounds, nil
}
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L31-99)
```go
func DecodeUniversalTxOutboundFromLog(log *evmtypes.Log) (*UniversalTxOutboundEvent, error) {
	if len(log.Topics) == 0 || log.Topics[0] != UniversalTxOutboundEventSig {
		return nil, fmt.Errorf("not a UniversalTxOutbound event")
	}
	if len(log.Topics) < 4 {
		return nil, fmt.Errorf("insufficient topics")
	}

	event := &UniversalTxOutboundEvent{
		TxID:   log.Topics[1],
		Sender: common.HexToAddress(log.Topics[2]).Hex(),
		Token:  common.HexToAddress(log.Topics[3]).Hex(),
	}

	// ABI types
	stringType, _ := abi.NewType("string", "", nil)
	bytesType, _ := abi.NewType("bytes", "", nil)
	uint256Type, _ := abi.NewType("uint256", "", nil)
	addressType, _ := abi.NewType("address", "", nil)
	uint8Type, _ := abi.NewType("uint8", "", nil)

	arguments := abi.Arguments{
		{Type: stringType},  // chainId
		{Type: bytesType},   // target
		{Type: uint256Type}, // amount
		{Type: addressType}, // gasToken
		{Type: uint256Type}, // gasFee
		{Type: uint256Type}, // gasLimit
		{Type: bytesType},   // payload
		{Type: uint256Type}, // protocolFee
		{Type: addressType}, // revertRecipient
		{Type: uint8Type},   // txType
		{Type: uint256Type}, // gasPrice
	}

	values, err := arguments.Unpack(log.Data)
	if err != nil {
		return nil, fmt.Errorf("failed to unpack UniversalTxOutbound: %w", err)
	}

	if len(values) != 11 {
		return nil, fmt.Errorf("unexpected number of unpacked values: %d", len(values))
	}

	i := 0
	event.ChainId = values[i].(string)
	i++
	event.Target = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.Amount = values[i].(*big.Int)
	i++
	event.GasToken = values[i].(common.Address).Hex()
	i++
	event.GasFee = values[i].(*big.Int)
	i++
	event.GasLimit = values[i].(*big.Int)
	i++
	event.Payload = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.ProtocolFee = values[i].(*big.Int)
	i++
	event.RevertRecipient = values[i].(common.Address).Hex()
	i++
	event.TxType = SolidityTxTypeToProto(values[i].(uint8))
	i++
	event.GasPrice = values[i].(*big.Int)

	return event, nil
}
```

**File:** x/uexecutor/README.md (L197-207)
```markdown
## Messages (`MsgServer`)

| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |

> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.
```

**File:** test/utils/contracts_setup.go (L325-346)
```go
// ---------------------------------------------------------------------------------------
// NOTE: The UniversalGatewayPC contract deployed here is a TEST-ONLY version.
//
// The withdraw() and withdrawAndExecute() functions inside this test contract:
//
//   - DO NOT run validation (_validateCommon)
//   - DO NOT compute gas fees via UniversalCore
//   - DO NOT pull PRC20 fees into VaultPC
//   - DO NOT burn PRC20 tokens
//   - DO NOT interact with any external contracts
//
// Instead, both functions simply **emit UniversalTxWithdraw with hardcoded values**:
//
//	chainId   = "eip155:11155111"
//	gasToken  = fixed test address
//	gasFee    = 111
//
// This behavior is intentional because Cosmos integration tests only need to verify:
//   - ABI correctness
//   - Event emission structure
//   - Outbound pipeline handling
//   - UE/UEM processing logic on the Cosmos side
```

**File:** test/integration/uexecutor/evm_hooks_and_outbound_test.go (L491-574)
```go
func TestPostTxProcessing_WithSyntheticOutboundEvent(t *testing.T) {
	t.Run("synthetic UniversalTxOutbound event creates UTX and outbound", func(t *testing.T) {
		chainApp, ctx, _ := utils.SetAppWithValidators(t)

		destChain := "eip155:11155111"
		chainConfig := uregistrytypes.ChainConfig{
			Chain:          destChain,
			VmType:         uregistrytypes.VmType_EVM,
			PublicRpcUrl:   "https://sepolia.drpc.org",
			GatewayAddress: "0x28E0F09bE2321c1420Dc60Ee146aACbD68B335Fe",
			Enabled: &uregistrytypes.ChainEnabled{
				IsInboundEnabled:  true,
				IsOutboundEnabled: true,
			},
		}
		require.NoError(t, chainApp.UregistryKeeper.AddChainConfig(ctx, &chainConfig))

		usdcAddr := utils.GetDefaultAddresses().ExternalUSDCAddr
		prc20Addr := utils.GetDefaultAddresses().PRC20USDCAddr
		tokenConfig := uregistrytypes.TokenConfig{
			Chain:        destChain,
			Address:      usdcAddr.String(),
			Name:         "USD Coin",
			Symbol:       "USDC",
			Decimals:     6,
			Enabled:      true,
			LiquidityCap: "1000000000000000000000000",
			TokenType:    1,
			NativeRepresentation: &uregistrytypes.NativeRepresentation{
				Denom:           "",
				ContractAddress: prc20Addr.String(),
			},
		}
		require.NoError(t, chainApp.UregistryKeeper.AddTokenConfig(ctx, &tokenConfig))

		gatewayAddr := uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address
		eventSigHash := common.HexToHash(uexecutortypes.UniversalTxOutboundEventSig)
		txIdHash := common.HexToHash("0x0000000000000000000000000000000000000000000000000000000000000001")
		senderHash := common.HexToHash("0x000000000000000000000000" + utils.GetDefaultAddresses().DefaultTestAddr[2:])
		tokenHash := common.HexToHash("0x000000000000000000000000" + prc20Addr.Hex()[2:])
		recipient := common.HexToAddress("0x527f3692f5c53cfa83f7689885995606f93b6164")

		data, err := encodeUniversalTxOutboundData(
			destChain, recipient.Bytes(), big.NewInt(1000000),
			common.Address{}, big.NewInt(111), big.NewInt(21000),
			[]byte{}, big.NewInt(0),
			common.HexToAddress(utils.GetDefaultAddresses().DefaultTestAddr),
			2, big.NewInt(1000000000),
		)
		require.NoError(t, err)

		evmLog := &ethtypes.Log{
			Address: common.HexToAddress(gatewayAddr),
			Topics:  []common.Hash{eventSigHash, txIdHash, senderHash, tokenHash},
			Data:    data,
			Removed: false,
		}
		receipt := &ethtypes.Receipt{
			TxHash:  common.HexToHash("0xsynth001"),
			GasUsed: 50000,
			Logs:    []*ethtypes.Log{evmLog},
		}

		sender := common.HexToAddress(utils.GetDefaultAddresses().DefaultTestAddr)
		hooks := uexecutorkeeper.NewEVMHooks(chainApp.UexecutorKeeper)

		err = hooks.PostTxProcessing(ctx, sender, core.Message{}, receipt)
		require.NoError(t, err)

		querier := uexecutorkeeper.NewQuerier(chainApp.UexecutorKeeper)
		allResp, err := querier.AllUniversalTx(
			sdk.WrapSDKContext(ctx),
			&uexecutortypes.QueryAllUniversalTxRequest{Pagination: &query.PageRequest{}},
		)
		require.NoError(t, err)
		require.NotEmpty(t, allResp.UniversalTxs, "UTX should be created from synthetic event")

		utx := allResp.UniversalTxs[0]
		require.NotEmpty(t, utx.OutboundTx, "outbound should be attached to UTX")
		require.Equal(t, destChain, utx.OutboundTx[0].DestinationChain)
		require.Equal(t, "1000000", utx.OutboundTx[0].Amount)
		require.Equal(t, uexecutortypes.TxType_FUNDS, utx.OutboundTx[0].TxType)
		require.Equal(t, uexecutortypes.Status_PENDING, utx.OutboundTx[0].OutboundStatus)
	})
```

**File:** app/app.go (L792-794)
```go
	)

	app.EVMKeeper.SetHooks(uexecutorkeeper.NewEVMHooks(app.UexecutorKeeper))
```
