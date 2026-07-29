## Analysis

The report's underlying bug class is: **a value that determines solvency/executability is sampled once at origination and is never revalidated; when real-world conditions move against that frozen value, the protocol has no corrective/liquidation path, so the affected party is stuck absorbing the consequence.**

The closest native analog in this repository is the **destination-chain gas price used for outbound (withdrawal) transactions**: it is fetched once from the `ChainMetaOracle`/gas-price vote, frozen into the `OutboundTx`/`OutboundCreatedEvent`, TSS-signed exactly once, and then the broadcaster retries the *same* signed, fixed-price transaction forever — with no fee-bump, re-signing, timeout, or refund path if the destination chain's real gas price rises above what was locked in.

### Title
Outbound gas price is locked in once at creation and never revalidated or re-priced, permanently stalling bridged funds with no automatic recovery - (File: `x/uexecutor/keeper/create_outbound.go`, `universalClient/chains/evm/tx_builder.go`, `universalClient/tss/txbroadcaster/evm.go`)

### Summary
When Push Chain creates an outbound (a withdrawal/side-effect to an external EVM chain), it snapshots the destination chain's gas price exactly once — from the on-chain `GasPrice`/`ChainMeta` oracle at the moment `attachOutboundsToUtx` runs [1](#0-0)  — and bakes that value immutably into the `OutboundTx.gas_price` / `OutboundCreatedEvent.GasPrice` fields [2](#0-1) . The Universal Client's EVM `TxBuilder` builds and TSS-signs a legacy transaction using that exact fixed gas price [3](#0-2) , and the broadcaster's retry loop resubmits the identical signed transaction indefinitely, using nonce-consumption as the *only* give-up signal — there is no gas re-pricing or fee-bump logic anywhere in the retry path [4](#0-3) [5](#0-4) .

### Finding Description
This mirrors the report's exact pattern: a value (gas price here, collateral value in the NFT case) is assessed a single time and then relied upon for the entire remaining lifecycle of the position/transaction, with no mechanism to re-check or correct it as real conditions change.

Concretely:
1. `attachOutboundsToUtx` computes the outbound's gas price/fee/limit once via `GetOutboundTxGasAndFees` at UTX-outbound-creation time and persists it on the `OutboundTx` record [6](#0-5) .
2. The signed, fixed-price legacy transaction is deterministic and TSS-signed once; EVM outbound flow explicitly states it has no expiry/deadline concept ("EVM relies on nonce-based finality") [7](#0-6) .
3. If the destination chain's live gas price/base fee subsequently rises above the frozen value (ordinary, expected market volatility, or an unprivileged attacker simply congesting the destination chain right after the oracle vote to spike its price), the fixed-price legacy tx can never be included; the resolver only distinguishes "still pending" vs. "nonce consumed by something else" — it never triggers a re-price [8](#0-7) .
4. The protocol's own design docs acknowledge there is **no automatic resolution** for a stuck `PendingOutbounds` entry: ballot expiry deliberately does not clear it (to avoid double-pay/double-delivery risk), and recovery is explicitly "governance-driven, not chain-driven" [9](#0-8) .

Meanwhile, the funds corresponding to that outbound have already been debited/burned on Push Chain's side (PRC20 burn / bank transfer) before the outbound was ever attempted on the destination chain, so the user is left with funds that are neither delivered externally nor refunded internally — exactly the "lender stuck holding depreciated collateral with no liquidation path" situation from the report, just substituting gas-price staleness for collateral-price staleness.

### Impact Explanation
An ordinary, unprivileged user's withdrawal can become permanently stuck with no protocol-level (non-privileged) recovery path once the locked-in gas price becomes insufficient relative to the destination chain's real market price — this falls under "permanent freezing... of user or protocol-controlled funds" and "denial of service... reachable without privileged control" in the allowed impact set. Because the fix documented for the report's bug (communicate the risk, or add a corrective mechanism) is exactly the gap here — no re-pricing, no timeout-based refund, no fee-bump — this is a genuine, code-level analog rather than a purely operational nuisance: the retry loop has no terminal/self-healing state other than eventual nonce consumption, which by design does not happen for a permanently-underpriced tx.

### Likelihood Explanation
Likelihood is driven by ordinary gas-price volatility on EVM destination chains (common on L1/L2 congestion spikes), and can additionally be intentionally triggered by an unprivileged actor who submits high-fee transactions on the destination chain immediately after a chain-meta gas price vote is finalized, ensuring any outbound created in that window locks in a stale, too-low price. No validator, admin, or TSS-participant compromise is required.

### Recommendation
Re-validate/re-quote the destination-chain gas price at broadcast time (or periodically during the SIGNED/BROADCASTED retry loop) and support a fee-bump/re-sign path when the originally locked-in price becomes insufficient relative to current network conditions; alternatively, add a bounded timeout after which the outbound is safely rolled back and refunded to the user via `handleFailedOutbound`'s existing revert/refund logic, rather than leaving `PendingOutbounds` in an indefinite governance-only limbo.

### Proof of Concept
1. User submits an inbound that produces an outbound (`TxType_FUNDS`) targeting an EVM destination chain.
2. `attachOutboundsToUtx` snapshots the current oracle gas price P₀ into `OutboundTx.GasPrice` and creates the `PendingOutbounds` entry [1](#0-0) .
3. Before the outbound is signed/broadcast, an unprivileged actor (or ordinary market conditions) drives the destination chain's real gas price/base fee above P₀.
4. `TxBuilder.GetOutboundSigningRequest`/`BroadcastOutboundSigningRequest` build/broadcast a legacy tx at fixed price P₀ [10](#0-9) ; the tx is rejected/never included by the network.
5. `broadcastOutboundEVM` keeps the event `SIGNED`/`BROADCASTED` and retries with the exact same signed, underpriced tx indefinitely — the resolver's only exit conditions are "tx found" or "nonce consumed," neither of which occurs [11](#0-10) [8](#0-7) .
6. The user's funds (already deducted on Push Chain) remain frozen; per the module's own documentation, this requires manual/governance intervention rather than any automatic on-chain resolution [9](#0-8) .

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L355-371)
```go
			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** proto/uexecutor/v1/types.proto (L159-180)
```text
  string destination_chain = 1;    // chain where this outbound is sent
  string recipient         = 2;    // recipient on destination chain
  string amount            = 3;    // token amount
  string external_asset_addr = 4; // asset addr destination chain
  string prc20_asset_addr    = 5; // prc20 contract addr
  string sender = 6; // sender of the outbound tx
  string payload = 7; // payload to be executed
  string gas_limit = 8; // gas limit to be used for the outbound tx
  TxType tx_type = 9; // outbound tx type
  OriginatingPcTx pc_tx = 10; // pc_tx that originated the outbound
  OutboundObservation observed_tx = 11; // observed tx on destination chain
  string id = 12; // id of outbound tx
  Status outbound_status = 13; // status of outbound tx
  RevertInstructions revert_instructions = 14;
  PCTx pc_revert_execution = 15;
  string gas_price = 16; // gas price on destination chain at time of outbound
  string gas_fee = 17;   // gas fee paid to relayer on destination chain
  PCTx   pc_refund_execution = 18; // PC tx that executed the gas refund (non-nil if refund ran)
  string refund_swap_error   = 19; // non-empty if swap-refund failed and we fell back to no-swap
  string gas_token           = 20; // gas token PRC20 address used to pay relayer fee
  string abort_reason        = 21; // Human-readable reason why the outbound was aborted
}
```

**File:** universalClient/chains/evm/tx_builder.go (L80-153)
```go
// GetOutboundSigningRequest creates a signing request from outbound event data.
// EVM doesn't consume data.SigningDeadline — deadlines are SVM-only; EVM relies
// on nonce-based finality.
func (tb *TxBuilder) GetOutboundSigningRequest(
	ctx context.Context,
	data *uetypes.OutboundCreatedEvent,
	nonce uint64,
) (*common.UnsignedSigningReq, error) {
	if data == nil {
		return nil, fmt.Errorf("outbound event data is nil")
	}
	if data.TxID == "" {
		return nil, fmt.Errorf("txID is required")
	}
	if data.DestinationChain == "" {
		return nil, fmt.Errorf("destinationChain is required")
	}

	gasPrice := new(big.Int)
	if data.GasPrice != "" {
		if _, ok := gasPrice.SetString(data.GasPrice, 10); !ok {
			return nil, fmt.Errorf("invalid gas price in event data: %s", data.GasPrice)
		}
	}
	if gasPrice.Sign() == 0 {
		return nil, fmt.Errorf("gas price is zero or missing in outbound event")
	}

	gasLimit, err := parseGasLimit(data.GasLimit)
	if err != nil {
		return nil, err
	}

	amount := new(big.Int)
	amount, ok := amount.SetString(data.Amount, 10)
	if !ok {
		return nil, fmt.Errorf("invalid amount: %s", data.Amount)
	}

	assetAddr := ethcommon.HexToAddress(data.AssetAddr)

	txType, err := parseTxType(data.TxType)
	if err != nil {
		return nil, fmt.Errorf("invalid tx type: %w", err)
	}

	funcName := tb.determineFunctionName(txType, assetAddr)

	txData, err := tb.encodeFunctionCall(funcName, data, amount, assetAddr, txType)
	if err != nil {
		return nil, fmt.Errorf("failed to encode function call: %w", err)
	}

	txValue := big.NewInt(0)
	if assetAddr == (ethcommon.Address{}) {
		txValue = amount
	}

	tx := types.NewTransaction(
		nonce,
		tb.vaultAddress,
		txValue,
		gasLimit.Uint64(),
		gasPrice,
		txData,
	)

	signer := types.NewEIP155Signer(big.NewInt(tb.chainIDInt))
	txHash := signer.Hash(tx).Bytes()

	return &common.UnsignedSigningReq{
		SigningHash: txHash,
		Nonce:       nonce,
	}, nil
```

**File:** universalClient/tss/txbroadcaster/evm.go (L13-24)
```go
// broadcastOutboundEVM broadcasts a signed EVM outbound transaction.
//
// All validators produce the same signed tx (deterministic TSS output), so the
// tx hash is known before broadcasting (computed from the assembled signed tx).
//
// Flow:
//  1. Build and broadcast the signed tx (tx hash is always returned, even on error)
//  2. Success → BROADCASTED with tx hash
//  3. Error: tx already on chain (mined by another node, or "already known") → BROADCASTED
//  4. Error otherwise: check finalized nonce on chain:
//     - nonce consumed → BROADCASTED with tx hash (resolver will REVERT)
//     - nonce NOT consumed → keep SIGNED, retry next tick
```

**File:** universalClient/tss/txbroadcaster/evm.go (L45-82)
```go
	// Broadcast — tx hash is computed before sending, so it's returned even on RPC error
	outboundData := data.OutboundCreatedEvent
	txHash, broadcastErr := builder.BroadcastOutboundSigningRequest(ctx, signingReq, &outboundData, signature)

	if broadcastErr == nil {
		b.markBroadcasted(event, chainID, txHash)
		return
	}

	// Broadcast failed — check if the tx landed on chain anyway (another node, or "already known")
	if txHash == "" {
		log.Warn().Err(broadcastErr).Msg("failed to assemble tx, will retry next tick")
		return
	}

	// First: is the tx already mined on chain (e.g., another node broadcast it)?
	// "already known" RPC errors fall into this bucket — the broadcast effectively
	// succeeded, and once the tx mines we can promote without waiting for the
	// nonce check.
	if found, _, _, _, vErr := builder.VerifyBroadcastedTx(ctx, txHash); vErr == nil && found {
		log.Debug().Err(broadcastErr).Str("tx_hash", txHash).
			Msg("broadcast errored but tx is on chain, marking BROADCASTED")
		b.markBroadcasted(event, chainID, txHash)
		return
	}

	tssAddress := ""
	if b.getTSSAddress != nil {
		var addrErr error
		tssAddress, addrErr = b.getTSSAddress(ctx)
		if addrErr != nil {
			log.Warn().Err(addrErr).Msg("failed to get TSS address for nonce check, will retry next tick")
			return
		}
	}

	b.checkNonceAndMarkBroadcasted(ctx, event, builder, chainID, txHash, tssAddress, data.SigningData.Nonce, broadcastErr)
}
```

**File:** universalClient/tss/txresolver/evm.go (L10-27)
```go
// Decision flow for EVM-broadcasted events (outbound and fund migration both
// follow this shape):
//
//   - VerifyBroadcastedTx error                      → stay BROADCASTED (retry)
//   - Tx found, insufficient confirmations           → stay BROADCASTED (retry)
//   - Tx found, status=1 (success)                   → COMPLETED / vote success
//   - Tx found, status=0 (reverted on chain)         → REVERT  / vote failure with tx hash
//   - Tx not found, signed nonce < finalized nonce   → REVERT  / vote failure (another tx
//                                                                consumed our nonce slot)
//   - Tx not found, signed nonce >= finalized nonce  → rewind to SIGNED so the broadcaster
//                                                      re-broadcasts (covers mempool drop)
//   - Tx not found, nonce check unavailable          → stay BROADCASTED (retry)
//
// The nonce IS the give-up signal; there is no max-retry counter. The two
// flows differ only in (a) which vote function records success/failure and
// (b) where the signer address comes from — current TSS for outbound, OLD TSS
// (derived from the event's old pubkey) for fund migration.
//
```

**File:** universalClient/tss/txresolver/evm.go (L85-103)
```go
	}

	signer, signedNonce, ok := r.outboundSigner(ctx, event)
	if !ok {
		return
	}
	verdict, finalizedNonce := txflow.CheckNonce(ctx, builder, signer, signedNonce)
	nlog := log.With().Uint64("signed_nonce", signedNonce).Uint64("finalized_nonce", finalizedNonce).Logger()
	switch verdict {
	case txflow.NonceUnknown:
		nlog.Debug().Msg("could not fetch finalized nonce, will retry next tick")
	case txflow.NonceConsumed:
		nlog.Debug().Msg("EVM outbound tx not found and nonce already finalized → REVERT")
		_ = r.voteOutboundFailureAndMarkReverted(ctx, event, txID, utxID, "", 0, "0",
			"tx not executed on destination chain")
	case txflow.NonceAvailable:
		r.rewindToSigned(event, chainID, signedNonce, finalizedNonce)
	}
}
```

**File:** x/uexecutor/keeper/gas_fee.go (L23-63)
```go
// GetOutboundTxGasAndFees calls UniversalCore.getOutboundTxGasAndFees(prc20, gasLimitWithBaseLimit)
// to get gasToken, gasFee, protocolFee, gasPrice, and chainNamespace.
// Pass gasLimitWithBaseLimit=0 to use the contract's baseLimit.
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
```

**File:** x/uexecutor/README.md (L273-282)
```markdown
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```
