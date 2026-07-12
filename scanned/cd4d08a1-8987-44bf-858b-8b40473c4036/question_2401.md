# Q2401: TransferNFT owner binding against NFT owner

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on token owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer via authz/feegrant path and compare signer to sender, causing `NFT owner` to diverge so the invariant `signer, sender, and authz grant cannot diverge to transfer victim NFTs` fails and the attacker can transfer a victim NFT by confusing signer or stale owner index state, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: transfer a victim NFT by confusing signer or stale owner index state by testing the owner binding angle against `NFT owner` during `transfer via authz/feegrant path and compare signer to sender`, with specific focus on token owner authority.
- Invariant to test: signer, sender, and authz grant cannot diverge to transfer victim NFTs; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
