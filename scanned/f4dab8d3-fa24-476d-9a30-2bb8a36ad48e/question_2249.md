# Q2249: MintNFT owner binding against token id uniqueness

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then mint via authz/feegrant path and compare signer to denom owner, causing `token id uniqueness` to diverge so the invariant `authz/feegrant cannot widen mint authority beyond the denom owner` fails and the attacker can mint an unbacked IBC voucher that can be withdrawn on another chain, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: mint an unbacked IBC voucher that can be withdrawn on another chain by testing the owner binding angle against `token id uniqueness` during `mint via authz/feegrant path and compare signer to denom owner`, with specific focus on denom owner authority.
- Invariant to test: authz/feegrant cannot widen mint authority beyond the denom owner; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
