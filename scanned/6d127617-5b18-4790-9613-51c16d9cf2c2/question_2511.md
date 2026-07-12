# Q2511: TransferNFT owner binding against collection index

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on token identity preservation, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer via authz/feegrant path and compare signer to sender, causing `collection index` to diverge so the invariant `old owner index removes the token exactly when new owner index adds it` fails and the attacker can create two apparent owners and use one path to bridge while the other sells or burns, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: create two apparent owners and use one path to bridge while the other sells or burns by testing the owner binding angle against `collection index` during `transfer via authz/feegrant path and compare signer to sender`, with specific focus on token identity preservation.
- Invariant to test: old owner index removes the token exactly when new owner index adds it; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
