[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/uexecutor/types/migration_payload.go (L39-46)
```go
	for fieldName, value := range uintFields {
		if value != "" {
			bi, ok := new(big.Int).SetString(value, 10)
			if !ok || bi.Sign() < 0 {
				return errors.Wrapf(sdkerrors.ErrInvalidRequest, "%s must be a valid unsigned integer", fieldName)
			}
		}
	}
```

**File:** x/uexecutor/types/abi.go (L928-937)
```go
func NewAbiMigrationPayload(proto *MigrationPayload) (AbiMigrationPayload, error) {
	if proto.Migration == "" {
		return AbiMigrationPayload{}, errors.New("invalid migration payload")
	}
	return AbiMigrationPayload{
		Migration: common.HexToAddress(proto.Migration),
		Nonce:     utils.StringToBigInt(proto.Nonce),
		Deadline:  utils.StringToBigInt(proto.Deadline),
	}, nil
}
```

**File:** x/uexecutor/keeper/evm.go (L196-227)
```go
func (k Keeper) CallUEAMigrateUEA(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	migration_payload *types.MigrationPayload,
	signature []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiMigrationPayload, err := types.NewAbiMigrationPayload(migration_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		nil,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"migrateUEA",
		abiMigrationPayload,
		signature,
	)
}
```
