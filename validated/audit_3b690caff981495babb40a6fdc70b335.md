[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/uvalidator/keeper/msg_server.go (L57-63)
```go
	if params.Admin != msg.Signer {
		ms.k.Logger().Warn("msg: AddUniversalValidator unauthorized",
			"expected_admin", params.Admin,
			"got_signer", msg.Signer,
		)
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}
```

**File:** x/uvalidator/keeper/msg_server.go (L143-149)
```go
	if params.Admin != msg.Signer {
		ms.k.Logger().Warn("msg: UpdateUniversalValidatorStatus unauthorized",
			"expected_admin", params.Admin,
			"got_signer", msg.Signer,
		)
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}
```

**File:** x/uvalidator/types/msg_update_universal_validator_status.go (L41-44)
```go
func (msg *MsgUpdateUniversalValidatorStatus) GetSigners() []sdk.AccAddress {
	addr, _ := sdk.AccAddressFromBech32(msg.Signer)
	return []sdk.AccAddress{addr}
}
```
