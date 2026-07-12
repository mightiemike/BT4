# Q2430: TransferNFT owner binding against owner index

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on collection membership, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer around token id validation edge cases and owner index updates, causing `owner index` to diverge so the invariant `collection state remains single-owner after rapid transfer/send ordering` fails and the attacker can bypass owner checks with malformed denom or token id storage keys, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: bypass owner checks with malformed denom or token id storage keys by testing the owner binding angle against `owner index` during `transfer around token id validation edge cases and owner index updates`, with specific focus on collection membership.
- Invariant to test: collection state remains single-owner after rapid transfer/send ordering; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
