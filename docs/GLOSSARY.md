# Glossary

## TL;DR

- Provenance is the record of where data came from. irlpls shows it as **eight links**, the same eight on every page; each shows one narrow thing, and each has a named limit.
- A signature shows who said something and that it has not changed since. It does not show that it is true.
- Each link has one level: **Evidenced**, **Claimed**, **Reported** (a time only) or **Not stated**. irlpls never collapses them into one badge or score.

## The eight links

| # | Link | In the field | What it shows | What it cannot show | Read from (card field) |
|---|---|---|---|---|---|
| 1 | Real person | Enrolment | Someone passed an identity check before any work. | That they did this capture. | `human` (Person) |
| 2 | Captured locally | Capture | A listed product made the capture; where, only if the position is authenticated. | That the scene was real; a screen can be re-photographed. | `camera` + `location` (Camera, Place): the weaker one counts |
| 3 | Genuine device | Device or app attestation | The app or hardware passed a server-side check on a date. | That it was not compromised afterwards. | `application` (App) |
| 4 | Sealed capture | Signing | Who signed, and that the file and its statements are unchanged since. | That the statements are true. | `camera` + `content`, only with a stated signing mechanism (C2PA, a manifest) |
| 5 | Known timestamp | Time-stamping | It existed no later than time T. | That it was captured at T. | `time`, only when the time is attested (RFC 3161, a log); a time the card only states is **Reported** |
| 6 | Tracked in transit | Chain of custody | Who held it, and when, hop by hop. | That it is unchanged (the seal shows that), or anything about an unlogged hop. | `provenance.custody` (optional) |
| 7 | Independently verified | Appraisal | A named party other than the signer checked it against stated rules. | More than those rules cover. | `corroboration` (Cross-check) |
| 8 | Delivered to you | Delivery | What was checked, and what was not, bound to your order. | That your use is sound. | `provenance.receipt` (optional) |

A **guarantee** is the card field behind a link: a Capture Card states seven of them (`human`, `application`, `camera`, `time`, `location`, `content`, `corroboration`, STANDARD Section 3), each with a level, a method and what is still uncertain, plus the optional `provenance.custody` and `provenance.receipt`. The eight links are read from those fields by the rules in the last column. People see the links; the card's JSON holds the guarantees.

Levels: **Evidenced** (a documented method and public evidence anyone can check), **Claimed** (the provider's word), **Reported** (link 5 only: the card states a time, but nothing attests it), **Not stated** (the card says nothing, or offers nothing).

## A to Z

**Analog hole.** Re-photographing a screen or print with a genuine camera yields a validly signed image of a false scene.

**Appraisal.** A verifier's assessment of evidence against reference values and policy (RFC 9334).

**Assertion.** A statement made by the signer (C2PA). NIST uses the word differently, for a message carrying an authentication result.

**Assurance level.** An ordinal scale of confidence with defined criteria (NIST SP 800-63; C2PA conformance levels). A number without criteria is not one.

**Attestation.** Two meanings. In RFC 9334 it is the result of an independent appraisal. In SLSA and in-toto it is a signed statement about an artifact, which is closer to a claim.

**Authenticity.** Informally, "genuine". No technical check establishes it, so irlpls does not use it as a label.

**Camera attestation.** Evidence that a signing product ran on genuine hardware. It does not show what the lens saw.

**Chain of custody.** Documented, unbroken handling from collection to use (evidence law).

**Claim.** A piece of asserted information (RFC 9334). In C2PA a claim is a signed structure that references assertions.

**Corroboration.** Independent confirmation from sources with different operators, sensors and pipelines. Sources sharing an upstream feed count as one.

**Evidence.** In RFC 9334, claims from an attester that a verifier has yet to appraise. It is unappraised, not proof. On irlpls, "Evidenced" means a documented method with public evidence anyone can check.

**Guarantee.** The card field behind a link (see The eight links). Seven per card, each with its own level.

**Hash (fingerprint).** A fixed-length digest of content. Any change to the content changes it.

**Injection attack.** Feeding synthetic frames into a capture pipeline, for example through a virtual camera, so that the device signs them.

**Integrity.** The content is unchanged since signing.

**Key rotation.** Replacing a signing key on a schedule. Old public keys stay published so that old signatures still check.

**Liveness.** A check that a live subject, not a photo or replay, is present. Injection can defeat it.

**Manifest.** A signed bundle of assertions and bindings attached to an asset (C2PA).

**Non-repudiation.** The signer cannot plausibly deny signing. It concerns the signer, not the data.

**Nonce.** A one-time value issued by the buyer at order time. Bound into the capture, it shows the capture came after the request (unverified as a complete guarantee).

**Provenance.** Information about the origin and history of an asset (C2PA) or how outputs were produced (SLSA). It is a record, not a verdict.

**Relying party.** The party that acts on a verification result (RFC 9334; NIST).

**Revocation.** Withdrawing trust in a key, certificate or attestation, usually after compromise. Validators have to check it, or old trust persists.

**Sealed capture.** Link 4: a capture signed on the device at the moment of recording, so later edits show up as a broken binding.

**Signature.** A value made with a private key that shows the signer held that key and that the signed bytes are unchanged.

**Time-stamp authority.** A third party that signs a hash with a time (RFC 3161). It shows existence no later than that time.

**Transparency log.** An append-only, publicly auditable record of signings (Sigstore).

**Trust anchor.** A root key or certificate that a verifier accepts without proof (C2PA).

**Trust list.** The set of trust anchors a verifier accepts. Whoever curates the list sets who counts as trusted.

**Validation state.** C2PA's outcome for a manifest: well-formed, valid, or trusted (from memory, unverified). "Trusted" means the signer is on the list, not that the content is true.

**Verifier.** The party that appraises evidence and issues a result (RFC 9334).

## Words irlpls avoids, and why

| Word | Why not | Instead |
|---|---|---|
| Verified (alone) | W3C and NIST give it a narrow technical meaning, but readers hear "true". The one exception is the name of link 7, **Independently verified**, which always means what its row says: a named party other than the signer checked stated rules; it says nothing about truth. | "Signature valid", "checked by (party)". |
| Authentic | No method establishes it; vendor "authentic" verdicts are not evidence. | "Signed by X, unchanged since". |
| Proven | Evidence here is checkable, not conclusive. | "Evidenced: checkable by anyone". |
| Trusted | In C2PA it means the signer is on a list, not that the content is true. | "Signer on the trust list". |
| Certified | Implies an audit and a guarantee that irlpls does not give. | Name the audit and its date. |
