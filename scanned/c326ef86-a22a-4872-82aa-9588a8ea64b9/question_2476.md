# Q2476: TransferNFT owner binding against NFT owner

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer via authz/feegrant path and compare signer to sender, causing `NFT owner` to diverge so the invariant `old owner index removes the token exactly when new owner index adds it` fails and the attacker can bypass owner checks with malformed denom or token id storage keys, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: bypass owner checks with malformed denom or token id storage keys by testing the owner binding angle against `NFT owner` during `transfer via authz/feegrant path and compare signer to sender`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: old owner index removes the token exactly when new owner index adds it; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
