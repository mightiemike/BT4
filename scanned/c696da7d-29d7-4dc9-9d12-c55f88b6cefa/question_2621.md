# Q2621: BurnNFT owner binding against NFT existence

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then burn after IBC send failure or timeout state is pending, causing `NFT existence` to diverge so the invariant `burn cannot destroy escrowed backing for an outstanding IBC packet` fails and the attacker can use signer mismatch to burn an NFT owned by another account, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: use signer mismatch to burn an NFT owned by another account by testing the owner binding angle against `NFT existence` during `burn after IBC send failure or timeout state is pending`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: burn cannot destroy escrowed backing for an outstanding IBC packet; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
