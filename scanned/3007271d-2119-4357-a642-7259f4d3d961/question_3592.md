# Q3592: ValidateMsgTransferDecorator.AnteHandle wrapped messages against ante pass/fail

## Question
Can unprivileged transaction signer or authz/feegrant grantee enter through `ValidateMsgTransferDecorator.AnteHandle` by submit value-moving messages directly and inside authz or feegrant transactions while controlling message set, MsgTransfer fields, signer, feegrant/authz wrapping, focusing on authz nested message inspection, under the precondition that the wrapped transaction is otherwise valid and signed, then submit a transaction containing bank, nft-transfer, and authz-wrapped messages, causing `ante pass/fail` to diverge so the invariant `ante validation cannot be bypassed for enabled value-moving transfer messages` fails and the attacker can route assets through a message type excluded from ante but accepted by keeper, leading to High signed transaction/authz/feegrant path spending, locking, burning, transferring, or delegating assets contrary to signer intent?

## Target
- File/function: app/ante.go::ValidateMsgTransferDecorator.AnteHandle
- Entrypoint: ante handler for signed Cosmos SDK transactions
- Attacker controls: message set, MsgTransfer fields, signer, feegrant/authz wrapping, transaction ordering
- Exploit idea: route assets through a message type excluded from ante but accepted by keeper by testing the wrapped messages angle against `ante pass/fail` during `submit a transaction containing bank, nft-transfer, and authz-wrapped messages`, with specific focus on authz nested message inspection.
- Invariant to test: ante validation cannot be bypassed for enabled value-moving transfer messages; additionally, ante validation cannot be bypassed by message wrapping.
- Expected Immunefi impact: High signed transaction/authz/feegrant path spending, locking, burning, transferring, or delegating assets contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app ante integration test with signed, authz, feegrant, and multi-message transactions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
