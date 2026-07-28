# Q3695: Recipient normalization zeroes or rewrites a meaningful target via Rawpayload Bytes Emitted From / Payload Is Only Source in DecodeRawPayload

## Question
Can an unprivileged attacker enter through a gasless `MsgExecutePayload` or `MsgMigrateUEA`, or a user-controlled source-chain payload that honest UVs later finalize with `raw_payload` bytes emitted from a user-controlled source-chain event when the payload is the only source of execution semantics for the inbound, and cause `DecodeRawPayload` to decode bytes into a different semantic object than honest observers or callers expect, so that it turn a payload-carrying inbound into execution against the wrong recipient or no recipient, breaking the invariant that normalization must not erase or misroute a recipient that affects value movement, and resulting in Direct theft/loss or permanent lock of funds?

## Target
- File/function: x/uexecutor/types/decode_payload.go::DecodeRawPayload
- Entrypoint: a gasless `MsgExecutePayload` or `MsgMigrateUEA`, or a user-controlled source-chain payload that honest UVs later finalize
- Attacker controls: `raw_payload` bytes emitted from a user-controlled source-chain event
- Exploit idea: Cause `DecodeRawPayload` to decode bytes into a different semantic object than honest observers or callers expect, so it can turn a payload-carrying inbound into execution against the wrong recipient or no recipient.
- Invariant to test: normalization must not erase or misroute a recipient that affects value movement
- Expected Immunefi impact: Direct theft/loss or permanent lock of funds
- Fast validation: write a decoder/keeper test that feeds the crafted raw payload through finalization and compare the executed payload with the source-chain intent
