# BookCraft AI Chatbot — Architecture Amendments

> **Document Version:** 1.0 &nbsp;•&nbsp; **Status:** Approved for Implementation &nbsp;•&nbsp; **Companion to:** *Architecture Reference v2.0*, *Implementation Guide v1.0*
>
> This document records architectural decisions D-070 through D-080. It is the authoritative source for these decisions. Once the diffs in §3 are applied to the canonical Architecture Reference, this amendment becomes a historical record of the change.

---

## Document Information

| Attribute | Value |
|---|---|
| Document type | Architecture Decision Records (amendments) |
| Triggers | (1) Rule-pack review against `Bot.zip`; (2) marketing-content decoupling work in `bookcraft_knowledge.zip`; (3) empirical verification of `optimization_report.md` claims |
| Audience | Engineering, Operations, Finance, Legal, Leadership |
| Cross-reference style | `AR §N.M` for Architecture Reference, `IG §N.M` for Implementation Guide, `D-NNN` for decisions |
| Net architectural impact | One new component (Funnel Signal Engine), one new principle (engine-owns-numbers), revised cost projections, formalized preprocessor contract, CD verification requirement |

> **D-081 update:** Tri-Match now emits funnel-stage votes in shadow mode with Decision Layer weight 0.
> This supersedes D-070/D-022 only for the question of whether Tri-Match may classify funnel
> stage. The original D-070 rationale remains historical context.

### How to read this document

1. **§1 Cover Note** — high-level summary by stakeholder domain (read first if you only have 5 minutes)
2. **§2 ADRs** — D-070 through D-080, each in standard ADR format
3. **§3 Diff Catalog** — explicit list of edits needed in the canonical Architecture Reference and Implementation Guide
4. **§4 Out-of-Scope** — what this amendment deliberately does not address

---

## 1. Cover Note

This amendment resolves eleven open questions surfaced by a review of the Tri-Match rule pack (`Bot.zip`) against the locked architecture. The questions divide into three groups: ones the architecture genuinely overlooked, ones where the architecture was right but lacked specificity, and ones that came from rule-pack defects that don't require architectural change.

### What changed for engineering

- **One new component** — the Funnel Signal Engine (D-070) — runs alongside Tri-Match on user text and contributes a fourth signal source to the Decision Layer. **Builds in Phase 4** alongside the LLM ensemble; does not block Phase 1-3 delivery.
- **Preprocessor sidecar contract is formalized** (D-079). Component 13 now has a typed, versioned contract for `_negation_cues.json`, `_typography_normalization.json`, and `_compound_word_variants.json`. **Builds in Phase 1** as part of the existing preprocessor work.
- **Pipeline output verification becomes a CD gate** (D-075). Every data-shipping pipeline (rule packs, prompts, eval corpora, document templates) ends with an invariant-assertion pass that blocks promotion on failure. **Adds a CI/CD step**; no new components.
- **Tri-Match shortcut promotion gets explicit recall floors per layer** (D-072). Each layer's promotion (`exact` → `regex` → `pattern`) now requires a measured recall+precision floor on the eval corpus, not a calendar-based gate.

### What changed for finance

- **Cost projections are revised honestly** (D-073). The mature monthly cost ceiling moves from $1,857 to a range of $1,890–$1,950 because the realistic shortcut hit rate at maturity is 30-40%, not 55%, given current rule-pack recall and the constraint that semantic-layer shortcuts remain disallowed (D-021). The original $1,857 figure is preserved as an "optimistic with semantic corroboration" scenario contingent on a future architectural change.
- **No new vendor or service costs.** The Funnel Signal Engine reuses existing infrastructure (TEI, Postgres, the same per-rule data store as Tri-Match).

### What changed for operations

- **The Funnel Signal Engine has its own dashboard** (similar shape to Tri-Match's). Empirical-precision tracking, shadow-mode metrics, and shortcut-promotion gating mirror Tri-Match's existing patterns.
- **CD pipeline gains a mandatory verifier step** (D-075). Pipeline failures now block promotion regardless of whether earlier passes succeeded.
- **Eval harness expands with named subsets** (D-074). Negation, hedge, and counterfactual cases become first-class regression-tracked subsets, alongside the existing intent-accuracy corpus.

### What changed for legal

Nothing. The legal-document workstream (Component 8, Phase 6) is untouched. None of the amendments alter document generation, the verifier, retraction window, or phased rollout for NDA/agreements.

### What this amendment deliberately does not do

- It does not modify any decision in D-001 through D-069 silently. Where modifications are needed (D-022, D-023, §10.2 cost model), the affected decision is named and the modifying decision number explicitly declared.
- It does not absorb rule-pack defects into the architecture. Issues that are rule-data problems are flagged for the rule-pack workstream rather than designed around.
- It does not resolve questions that require business strategy input (e.g., whether to invest in a CRM event stream is a product/CRM-vendor decision, not an architectural one — D-077 specifies the architectural shape but defers the build decision to product).

---

## 2. Architecture Decision Records

Each ADR follows the format: **Decision** (one sentence) → **Context** → **Alternatives considered** → **Rationale** → **Consequences**.

---

### D-070: Funnel Signal Engine as a fourth Decision Layer source

**Decision:** Introduce a Funnel Signal Engine as a new component that runs deterministic rules over user text per turn and contributes funnel-stage signals to the Decision Layer at calibrated weight, with weight 0 at launch (shadow). This modifies D-022 — Tri-Match itself does not classify funnel stage, but a sibling deterministic engine does, in shadow mode.

**Context.** D-022 states "Tri-Match does NOT classify funnel stage (LLM-only)." The rule pack, however, ships `funnel_stage_intents.json` with 1,267 rules. Some of these are user-text patterns ("I'm ready to sign", "send me the contract") that have legitimate per-turn signal value; others are CRM database field patterns that don't apply to chat. The architecture had no place for either. Discarding both would forfeit signal value; absorbing both into Tri-Match would conflate two genuinely different rule populations.

**Alternatives considered.**
- **Strict honor of D-022** (discard all funnel rules). Forfeits signal that is real on the user-text rules and contradicts the rule-pack work already done.
- **Modify D-022 to let Tri-Match classify funnel.** Conflates Tri-Match's role (intent classification) with funnel classification (a contextual signal that depends heavily on conversation history). Tri-Match would need to load two different rule populations, calibrate two different weight tables, and reason about funnel-stage stickiness rules from §6.4.3. Surface area grows; testability suffers.
- **Define a new component** (chosen). Funnel Signal Engine reuses Tri-Match's machinery (rule storage, hot-reload, calibration counters, evidence aggregation) but operates as its own typed input to the Decision Layer. Stays clear of D-022's intent-classification scope.

**Rationale.** Two genuinely different concerns deserve two components even when they share infrastructure. Tri-Match votes on `query_intent` and `service_intent`; Funnel Signal Engine votes on `funnel.stage`. The Decision Layer is already designed for multi-source weighted voting with per-source weights tracked over time (AR §6.11). Adding a fourth source has well-understood mechanics. Initial weight 0 at launch means the engine's vote is **logged but not consequential** until calibration data justifies a weight increase — the same shadow pattern used for Tri-Match itself (D-019).

**Consequences.**
- **Engineering:** New component (Component 14). Phase 4 build alongside Tri-Match revision and Decision Layer integration. Code reuses Tri-Match's `Engine`, `RuleRepository`, calibration counters; differences are in the rule corpus and the dimension being voted on. Estimated 30% of Tri-Match's build cost.
- **Operations:** New dashboard (matches Tri-Match's shape). New environment variable `FUNNEL_SIGNAL_MODE` mirroring `TRIMATCH_MODE`.
- **Finance:** Negligible. Reuses existing infrastructure.
- **Legal:** None.

D-070 supersedes D-022 in the narrow respect that funnel signals from deterministic rules are now permitted, contributed by a separate component rather than by Tri-Match. D-022's substantive intent — that funnel classification is contextual and the LLM ensemble remains the primary classifier — is preserved by setting the Funnel Signal Engine's launch weight to 0.

---

### D-071: Funnel rule partitioning by consumption pattern

**Decision:** Partition `funnel_stage_intents.json` into two files at ingest time: `funnel_signal_rules_userlang.json` (consumed by the Funnel Signal Engine per turn, D-070) and `funnel_signal_rules_crm.json` (consumed by the CRM event consumer per CRM event, D-077). Partition by the existing `section` field on each rule; do not reclassify by parsing regexes. The metadata-only rule `FS-INI-051` (matches any message ≥ 10 chars without pricing) is dropped — its semantics belong in thread state (AR §6.1), not in a regex.

**Context.** The funnel rule corpus mixes two consumption patterns. User-language rules (e.g., "I'm ready to sign", from sections "B) Inbound Message Content", "G) Chat Widget", "L) Buyer Communications") fire on chat messages. CRM rules (e.g., `opportunity_stage = "ClosedWon"`, from sections "A) CRM Lifecycle", "L) INTERNAL CRM FLAGS") fire on CRM database events that the chatbot doesn't see. Running both populations against user text produces large volumes of guaranteed misses; running both against CRM events misses the user-text matches entirely. Each population has a different consumer.

**Alternatives considered.**
- **Run both against user text.** Wastes per-turn compute on CRM rules that can never match. Pollutes calibration counters with false-negative noise.
- **Discard the CRM rules.** Forfeits a future capability. The rule authors clearly intended them to fire on CRM events; that consumer just doesn't exist yet.
- **Partition** (chosen). Each population goes to its appropriate consumer. CRM rules sit unconsumed until D-077's CRM event consumer is built; user-language rules ship to the Funnel Signal Engine immediately.

**Rationale.** Partition by `section` is robust because the rule authors organized rules by consumption context already. Re-deriving the partition by regex parsing would be heuristic and error-prone. The single edge case (`FS-INI-051`) is a metadata predicate that a regex can't faithfully express; deleting it is the right call.

**Consequences.**
- **Engineering:** Add a partition step to the rule-pack ingest pipeline. CRM rules live in version control but aren't loaded by any running component until D-077 ships.
- **Operations:** Two rule files instead of one; two `Tri-Match-style` dashboards (user-language and CRM) once both consumers exist.
- **Finance:** None.
- **Legal:** None.

---

### D-072: Recall floors gate per-layer Tri-Match shortcut promotion

**Decision:** Each Tri-Match shortcut layer (`exact`, `regex`, `pattern`) is promoted into `TRIMATCH_SHORTCUT_LAYERS` (D-020) only after meeting both a precision floor (≥ 0.97) and a recall floor on a labeled corpus subset specific to that layer. Recall floors are: `exact ≥ 0.20`, `regex ≥ 0.35`, `pattern ≥ 0.45`. D-021 (no shortcuts on `semantic` or `fuzzy` layers) is unchanged.

**Context.** D-020 introduced per-layer shortcut promotion; D-023 set the confidence threshold at 0.95. Neither specified recall — the implicit assumption was that high precision was sufficient. But the projected shortcut hit rate in §10.2 (55% at maturity) is *recall*-bounded: a perfectly precise rule that fires on 1% of messages contributes 1% to hit rate. The current rule pack achieves roughly 30% recall on common service-intent phrasings (verified against the rule pack's own `fuzzy` examples). Without recall floors, layer promotion can satisfy precision while delivering negligible cost benefit, and the cost projections in §10.2 become unjustifiable.

**Alternatives considered.**
- **No recall gate, just precision.** Allows layer promotion that doesn't move the cost curve. Continues to leave cost projections aspirational.
- **Single recall floor across all shortcut layers.** Doesn't reflect that `exact` is intrinsically narrower than `regex`, which is intrinsically narrower than `pattern`. A 0.45 floor on `exact` would be unreachable; a 0.20 floor on `pattern` would underutilize the layer.
- **Per-layer recall floors** (chosen). Each layer has a floor calibrated to its expected recall ceiling. Promotion is staged and measurable.

**Rationale.** Recall floors make the cost-decay narrative in §2.1 ("cost decreases with maturity") an *earned* property rather than an assumed one. A team that ships layer promotion without recall improvement learns immediately that promotion didn't help; a team that lifts recall through better rules earns the cost benefit. The numeric floors are calibrated to hit a 35% combined shortcut rate when all three layers are promoted — sufficient to bend the cost curve meaningfully without overpromising.

**Consequences.**
- **Engineering:** The eval harness (IG §2.8) gains per-layer recall measurement on a labeled corpus subset. Layer promotion is gated in code — `TRIMATCH_SHORTCUT_LAYERS` won't accept a layer until the eval reports both floors met.
- **Operations:** New monitoring dashboard panels: per-layer recall trend, per-layer precision trend, gap-to-floor.
- **Finance:** Layer promotion timing is now data-driven, not calendar-driven. The cost-decay schedule shifts from §10.2's projections to whatever the rule-pack workstream actually delivers (see D-073).
- **Legal:** None.

---

### D-073: Honest cost projections under realistic Tri-Match recall

**Decision:** The mature-state per-turn cost ceiling in AR §10.2 is revised from $0.0258 to $0.0270 (representing 30% Tri-Match shortcut hit rate at maturity, rather than 55%). Monthly mature-state cost moves from $1,857 to $1,944. The original $1,857 figure is preserved as an "optimistic with semantic corroboration" scenario, contingent on a future ADR that would relax D-021 in a defined way.

**Context.** The cost projections in §10.2 assume 55% Tri-Match shortcut hit rate at maturity. That figure depends on shortcut-eligible layers (per D-021: only `exact`, `regex`, `pattern`) collectively achieving 55% recall on the conversation corpus while maintaining ≥ 0.95 precision. The current rule pack achieves roughly 30% recall on common phrasings; even with the rule-pack workstream broadening "general" rules, the realistic ceiling for these three layers is in the 35–45% range, not 55%. The 55% figure was reachable in the original projection only by implicitly assuming the semantic layer would also shortcut, which D-021 forbids.

**Alternatives considered.**
- **Hold the $1,857 figure** and pressure the rule-pack workstream to hit 55% recall on shortcut-eligible layers. Likely unattainable without paraphrase coverage that's effectively a semantic problem.
- **Hold $1,857 and revisit D-021** to allow semantic shortcuts at higher threshold. Reasonable but premature; semantic-layer shortcuts have failure modes that haven't been tested in production yet.
- **Revise cost projections to reflect realistic recall** (chosen). Honest about the trade-off; preserves the optimistic figure as a contingent target.

**Rationale.** The "cost decreases with maturity" property in §2.1 stays true under the revised numbers — they still bend downward, just less aggressively. Engineering plans against $1,944 instead of $1,857; finance budgets a $90/month cushion that disappears when (and if) D-021 is relaxed. This is more defensible than holding to a number the system can't deliver against current architectural constraints.

**Consequences.**
- **Engineering:** None. The architecture doesn't change; only the projections do.
- **Operations:** Cost dashboard updated with new target line and the optimistic-scenario line.
- **Finance:** Annual mature-state LLM-cost budget moves from ~$22,300 to ~$23,300 — a $1,000/year delta. Within typical budget noise.
- **Legal:** None.

---

### D-074: Negation, hedge, and counterfactual integration contract

**Decision:** The shared preprocessor (Component 13) reads `_negation_cues.json` at startup, computes negation/hedge/counterfactual spans on every message via spaCy dependency parsing, and emits them as typed fields on `ProcessedMessage`. Tri-Match, Funnel Signal Engine, and Extraction respect these spans by applying multiplicative damping to evidence that overlaps a span: 0.0 for `negated=true` (full suppression), 0.4 for `hedged=true`, 0.0 for `counterfactual=true`. The eval harness gains three named subsets — Negation, Hedge, Counterfactual — tracked separately for regression detection.

**Context.** AR §6.13 says the preprocessor produces `negation_spans`. D-036 (extraction) says "negation flag suppresses auto-escalation but records mention." The wiring between *which component reads which sidecar* and *how matches inside spans are damped* has not been specified. Empirically, the bare regex layer has no mechanism to suppress matches inside negation: "I'm not interested in audiobooks" matches the audiobook regex with full weight. The `_negation_cues.json` sidecar exists but the architecture doesn't say who reads it.

**Alternatives considered.**
- **Each consumer reads the sidecar independently.** Three components doing the same negation detection, with risk of drift between implementations.
- **Apply damping in the Decision Layer, post-aggregation.** Loses the per-rule attribution needed for calibration counters; a rule's `times_overruled` count should reflect when the rule fired *and was correct in absence of negation*, not when the rule fired *period*.
- **Preprocessor owns the sidecar, consumers respect spans** (chosen). Single source of truth for negation detection. Consumers apply damping at evidence-emission time, so calibration counters reflect the right event ("rule fired but evidence was damped because of negation").

**Rationale.** Damping factors are calibrated, not arbitrary. `negated=0.0` reflects that "I'm not asking about price" should produce zero pricing-intent weight. `hedged=0.4` reflects that "thinking about ghostwriting later" carries some signal but not the full weight of a non-hedged statement. `counterfactual=0.0` reflects that "if I were to need a cover" is conditional and shouldn't move the funnel. The eval harness with named subsets ensures any future regression on negation handling shows up immediately rather than diluting into the overall accuracy number.

**Consequences.**
- **Engineering:** Component 13's `ProcessedMessage` schema gains `hedge_spans` and `counterfactual_spans` alongside `negation_spans` (already specified). Tri-Match, Funnel Signal Engine, and Extraction each gain a span-overlap check at evidence emission. The eval harness in IG §2.8 adds three named test subsets sourced from negated/hedged/counterfactual examples.
- **Operations:** New eval-harness panels for the three subsets. Regression alerting if any subset's accuracy drops > 5pp from baseline.
- **Finance:** None.
- **Legal:** None.

---

### D-075: Pipeline output verification as a CD gate

**Decision:** Every data-shipping pipeline (rule packs, prompt updates, eval-harness regenerations, document templates) ends with a verification pass that asserts each prior pass's invariants on the actual output artifact. The verifier exits non-zero on any failed invariant. CD blocks promotion of any pipeline whose verifier exits non-zero, regardless of whether earlier passes succeeded. This is added to AR §8.2 as a contractual requirement, not a per-pipeline workstream artifact.

**Context.** The previous rule-pack optimization claimed three fixes that did not land in the data: smart-quote repair shipped a malformed regex, bounded-`.*` skipped a sidecar file, scoped case-insensitivity flag was applied to zero rules. Each was a verification gap, not a code-change gap. Pipelines that say "I did X" without verifying X-on-output-artifact are unreliable. This pattern is not specific to rule packs — any pipeline that transforms data and emits artifacts has the same risk.

**Alternatives considered.**
- **Verify only critical pipelines.** Subjective and inevitably misses something. Document templates (Phase 6) are critical too; rule packs are critical now; eval harnesses are critical for trust in regression alerts.
- **Manual verification step.** Doesn't scale; the entire point of CD is automation.
- **Mandatory verifier as CD gate** (chosen). Each pipeline owns its own verifier; the principle is uniform; CD enforces the gate.

**Rationale.** This generalizes the rule-pack workstream's `pass_08_verify.py` from a workstream artifact to an architectural requirement. The verifier is per-pipeline because each pipeline's invariants differ; the *requirement that one exist and gate CD* is universal. This closes the class of failures where "I did X" was reported but never verified.

**Consequences.**
- **Engineering:** Each pipeline maintainer adds a verifier as part of pipeline definition. New pipeline reviews check for verifier presence. Existing pipelines (rule pack, eval harness, prompt updates) gain verifiers as a follow-up sprint.
- **Operations:** CI runs verifier; failed verifier produces clear logs identifying which invariant failed and where in the artifact. Failed verifier blocks promotion via standard CD gate mechanics.
- **Finance:** None.
- **Legal:** None.

---

### D-076: Priority scale schema asymmetry — documentation, not change

**Decision:** Document the intentional asymmetry between `priority_scale` (excludes `Negative`) and the per-rule `priority` enum and `priority_weights` mapping (include `Negative`). The asymmetry is preserved because `Negative` is a property of exclusions, not a tier on the scale. Update the schema's `description` field and the meta block of every rule file to state this explicitly.

**Context.** `_schema.json` defines `priority_scale` as `["Critical", "High", "Moderate", "Low"]` (no `Negative`) but the per-rule `priority` enum and `priority_weights` mapping include `Negative`. Reviewer reads this as a defect; rule authors meant it as a semantic distinction.

**Alternatives considered.**
- **Add `Negative` to `priority_scale`** with a doc-string. Misleading: `Negative` isn't a tier on the scale, it's a marker of negative weight.
- **Document the intentional asymmetry** (chosen). Preserves the semantic distinction; resolves the audit-trail concern.

**Rationale.** This is the smallest of the amendments. The cost of a documentation update is ~30 minutes; the cost of misinterpreting `Negative` as a positive tier (which "adding it to the scale" risks) is much larger.

**Consequences.**
- **Engineering:** Update `_schema.json` description; update meta block in 10 rule files.
- **Operations:** None.
- **Finance:** None.
- **Legal:** None.

---

### D-077: CRM event consumer (deferred to product)

**Decision:** The architecture defines the *shape* of a CRM event consumer that processes `funnel_signal_rules_crm.json` rules per CRM event and emits funnel-stage signals into the Decision Layer through the same interface as the Funnel Signal Engine (D-070). The actual *build* of this consumer is deferred to product/CRM-vendor decisions; the architecture does not commit to a build phase. Until built, CRM rules remain in version control as a future-work corpus.

**Context.** D-071 partitions funnel rules into user-language and CRM populations. The user-language consumer is the Funnel Signal Engine (D-070). The CRM consumer doesn't exist; the architecture's §3.5 mentions "logical decoding → Kafka → async workers" but no component reads CRM events to update funnel stage. Either the architecture defines the consumer (committing to build it) or it documents that CRM rules are unconsumed at launch.

**Alternatives considered.**
- **Discard CRM rules entirely.** Forfeits future capability the rule authors invested in.
- **Build the consumer now.** Premature: CRM-vendor selection and integration are product/sales decisions; a consumer built before knowing the source schema would be over-engineered.
- **Define the shape, defer the build** (chosen). Architecture stays consistent with D-071; consumer build waits for CRM integration to be a real project.

**Rationale.** The shape of a CRM event consumer is an architectural decision (what schema, what cadence, what output). The *whether* to build it is a product decision. Defining the shape without committing to build is the right separation of concerns.

**Consequences.**
- **Engineering:** None at launch. When CRM integration becomes a project, the consumer build follows the documented shape.
- **Operations:** CRM rules sit in version control as documentation of intended future capability.
- **Finance:** None at launch.
- **Legal:** None.

---

### D-078: Engine-owns-numbers principle

**Decision:** Pricing and timeline values live in the Pricing & Timeline Engine, not in the RAG corpus. The RAG corpus owns names, descriptions, processes, and capabilities; the Engine owns numbers. RAG corpus ingestion includes a verification pass that rejects any document containing numeric pricing or timeline patterns (e.g., `$\d`, `\d+\s*weeks`, `\d+\s*per\s+(?:word|page|hour)`). This is added to AR §2.2 as a system principle and to §7.1 as a hard rule.

**Context.** The marketing-content decoupling work (`bookcraft_knowledge.zip`, decoupling_report.docx) revealed that v1.4.0 of the marketing content had specific prices baked in (`$150/PFH`, `$0.005/word`, `4-8 weeks`). When prices change in the Pricing & Timeline Engine, the RAG corpus continues to retrieve old numbers — and the chatbot retrieves both, producing contradictory output. The architecture mentioned RAG corpus contents in §7.1 but never made the principle explicit, allowing this kind of duplication to creep in over time.

**Alternatives considered.**
- **Document the principle without enforcement.** Soft: the same drift will recur with the next content update.
- **Enforce at runtime by stripping numbers from RAG retrievals.** Brittle: legitimate uses (page counts in word-count thresholds) get stripped too.
- **Enforce at ingest time with a verifier** (chosen). Catches the issue at the source. Legitimate numbers (like word counts) pass; pricing-shaped numbers fail with clear error message.

**Rationale.** This is a single source of truth principle applied to a place the architecture overlooked. The decoupling effort already produced the v1.5.0 corpus correctly; this decision ensures it stays correct under future updates.

**Consequences.**
- **Engineering:** RAG corpus ingestion (IG §2.5) gains a numeric-pattern verifier as part of D-075's CD-gate framework. The verifier rejects documents containing pricing-shaped patterns. Maintainers get a clear error message and can route the affected number to the Engine.
- **Operations:** RAG corpus updates that violate the principle fail in CD with a clear diagnosis. Marketing-content authors learn the boundary quickly.
- **Finance:** None directly. Indirectly avoids customer-confidence damage from contradictory price quotes.
- **Legal:** None.

---

### D-079: Preprocessor sidecar contract

**Decision:** Component 13 (Shared Preprocessing) has a versioned, typed contract for three sidecar inputs: `_negation_cues.json`, `_typography_normalization.json`, and `_compound_word_variants.json`. Each sidecar has a documented schema, a documented effect on `ProcessedMessage`, and a verifier (per D-075) that asserts the sidecar's invariants hold post-load. Sidecars are not editable by downstream consumers; only the preprocessor reads them.

**Context.** The rule pack ships three sidecars that the preprocessor (per their `meta.purpose` fields) is expected to consume. AR §6.13 didn't specify their contracts. This left questions: *who* reads each file, *when* (startup or per-turn), *what* the transformation produces on `ProcessedMessage`, and *how* the consuming code handles a malformed sidecar.

**Alternatives considered.**
- **Treat sidecars as undocumented implementation detail.** Causes confusion when downstream components want to reason about negation or typography. Allows drift between rule-pack assumptions and preprocessor behavior.
- **Inline the sidecar contents into the preprocessor code.** Tempting but loses the version-controlled separation between rule-pack data and engine code; rule authors can't iterate on negation cues without a code change.
- **Formal sidecar contract with verifier** (chosen). Sidecars are versioned data files; preprocessor is the sole consumer; verifier ensures sidecars remain well-formed.

**Rationale.** The sidecar pattern (data outside code, versioned independently) is the right shape for content rule authors edit frequently. The contract makes the consumer explicit and prevents downstream components from reaching for the same files independently — eliminating the drift risk D-074 also addresses.

**Consequences.**
- **Engineering:** Component 13 gains three load-time hooks (one per sidecar) and three corresponding `ProcessedMessage` fields. Verifiers per D-075 assert sidecar well-formedness.
- **Operations:** New environment variables `PREPROCESSOR_SIDECAR_DIR` (default `/etc/bookcraft/preprocessor`). Sidecar updates trigger preprocessor reload via standard hot-reload.
- **Finance:** None.
- **Legal:** None.

---

### D-080: Genre-rule lookahead pattern (Tri-Match cross-service disambiguation)

**Decision:** Genre-only rules (e.g., "fantasy + novel/book") are wrapped with an anchored positive lookahead requiring a service-intent verb to also be present in the message. The pattern is `\A(?=[\s\S]*(?:<service-verb-alternatives>))[\s\S]*?\b(<genre>)\b.*\b(novel|book)\b`. Priority is auto-demoted one tier (Critical/High → Moderate, Moderate → Low) since the rule is now corroborating evidence rather than standalone evidence. This pattern becomes part of Tri-Match's documented techniques.

**Context.** Genre rules without service-verb requirements over-fire across services. "I need a cover for my fantasy novel" matches both the cover-design intent and (without a service-verb requirement) the ghostwriting fantasy-genre rule. The user wants cover design; ghostwriting fires falsely. This is a real Tri-Match technique that wasn't documented as a pattern.

**Alternatives considered.**
- **Remove genre-only rules.** Loses the genre signal entirely.
- **Run all genre rules at lower priority unconditionally.** Doesn't fix the false-fire — just makes it weaker. Genre + cover-design verb still matters for genre-specific cover design ("dark fantasy cover").
- **Anchored lookahead with service-verb requirement** (chosen). Genre signal is preserved as corroborating evidence when paired with service intent; suppressed when standalone.

**Rationale.** The `\A` anchor in the lookahead is critical — without it, the lookahead would only find verbs appearing *after* the current match position, missing the common case where the verb appears earlier in the message. The pattern is reusable across services and should be documented as a Tri-Match technique alongside negation handling and evidence aggregation.

**Consequences.**
- **Engineering:** Documented as a Tri-Match pattern in AR §6.4.1. Existing rule pack already applies this pattern (Pass 3.6); the architecture amendment makes it official.
- **Operations:** None.
- **Finance:** None.
- **Legal:** None.

---

## 3. Diff Catalog

This section enumerates the specific edits required in the canonical Architecture Reference and Implementation Guide once these ADRs are adopted. Edits are ordered by section number for ease of patching.

### Architecture Reference v2.0 → v2.1

| Section | Change | ADR ref |
|---|---|---|
| §2.2 (Architectural Principles) | Add principle #8: "Single source of truth for facts. Numbers (prices, timelines) live in the Pricing & Timeline Engine; descriptions live in the RAG corpus; identity lives in the customers table; conversation state lives in `ThreadState`." | D-078 |
| §3.5 (Deployment Topology) | Update worker tier diagram to include "Funnel Signal Engine" alongside Tri-Match, sharing infrastructure. | D-070 |
| §4.2 (Component SLOs) | Add row: "Funnel Signal Engine — p50 10ms, p95 30ms, p99 60ms, error budget 0.5%". | D-070 |
| §5 (Per-Turn Flow) | In Phase 2, add parallel Funnel Signal Engine classification alongside Tri-Match. | D-070 |
| §6.2 (TRG) | Clarify that TRG produces relations and compliance scores; **does not** produce funnel-stage signals. Funnel signals come from the LLM ensemble and the new Funnel Signal Engine. | D-070 |
| §6.4.1 (Tri-Match) | Add subsection "Cross-service disambiguation patterns" covering the genre-rule lookahead pattern. | D-080 |
| §6.4.1 (Tri-Match) | Superseded by D-081: Tri-Match emits funnel-stage votes in shadow mode with Decision Layer weight 0. | D-081 |
| §6.5 (Extraction) | Update D-036 reference to point to D-074's negation/hedge/counterfactual contract. | D-074 |
| §6.11 (Decision Layer) | Add Funnel Signal Engine as a fourth source. Initial weight 0. Update source weights table. | D-070 |
| §6.13 (Shared Preprocessing) | Replace input contract with the formal sidecar contract: `_negation_cues.json`, `_typography_normalization.json`, `_compound_word_variants.json`. Specify `ProcessedMessage` gains `hedge_spans` and `counterfactual_spans`. | D-074, D-079 |
| Add **§6.14: Funnel Signal Engine** | New component reference. Mirrors §6.4 in shape but operates on funnel-stage rule corpus. | D-070 |
| Add **§6.15: CRM Event Consumer (deferred)** | Documents the shape of the consumer; explicitly marks build as deferred to product decision. | D-077 |
| §7.1 (RAG) | Add hard rule: "RAG corpus must not contain numeric pricing or timeline patterns. Ingestion verifies this and rejects documents that violate." Reference D-078. | D-078 |
| §8.1 (Testing Strategy) | Eval harness gains three named subsets — Negation, Hedge, Counterfactual — tracked separately. | D-074 |
| §8.2 (CI/CD) | Add subsection "Pipeline output verification" requiring every data-shipping pipeline to have a final verifier pass that gates CD promotion. | D-075 |
| §10.2 (Cost Projections) | Update mature-state numbers: per-turn $0.0270, monthly $1,944. Add "optimistic with semantic corroboration" scenario at $0.0258 / $1,857. | D-073 |
| §11 (Risk Register) | Add R-021: "Tri-Match recall plateau below shortcut hit rate target — mitigation: D-072 recall floors gate promotion; D-073 honest cost projections preserve budget headroom." | D-072, D-073 |
| §12 (Decision Ledger) | Append D-070 through D-080 as one-liners. Mark D-022 as "Modified by D-070" and D-021/D-023 as "Refined by D-072". | All |

### Implementation Guide v1.0 → v1.1

| Section | Change | ADR ref |
|---|---|---|
| §1.9 (Preprocessor) | Add task: load three sidecars at startup; compute hedge and counterfactual spans alongside negation. | D-074, D-079 |
| §1.9 validation | Add tests: `_negation_cues.json` produces expected spans; hedge cues produce hedge spans; counterfactual cues produce counterfactual spans. | D-074 |
| §2.5 (Elasticsearch RAG) | Add ingest-time verifier: reject documents containing pricing-shaped patterns. | D-078 |
| §2.8 (Eval Harness) | Add Negation, Hedge, Counterfactual labeled subsets. Track each separately in eval reports. | D-074 |
| Phase 4 | Add §4.15: Funnel Signal Engine implementation (mirrors §4.5 through §4.13 in shape). | D-070 |
| Phase 4 | Add §4.16: Funnel rule partitioning at ingest time. | D-071 |
| Cross-Phase CP.7 (CI/CD) | Add: every data pipeline ends with verifier; verifier failure blocks CD. | D-075 |

---

## 4. Out of Scope for This Amendment

These items appeared during the review but are not architectural decisions:

- **Rule-pack mechanical fixes** (smart-quote regex repair, bounded `.*` in cross-service exclusions, scoped `(?-i:ABBR)`, top-level `|` alternation bugs, "general" rule recall broadening). These are Document 1's scope and belong to the rule-pack workstream, not the architecture document.
- **Specific recall floor values** beyond D-072's initial calibration. These will be tuned against real eval data after the rule-pack workstream lands its broadening pass.
- **CRM vendor selection.** Out of architectural scope; D-077 documents the consumer shape so the architecture is ready when the product decision is made.
- **Marketing brand voice prompt customization.** Owned by marketing per D-042.
- **CSR admin UI for rule approval** (D-029, D-056). Separate frontend track per the architecture's stated scope.

---

*End of architecture amendments. Once the diffs in §3 are applied, the Architecture Reference is at v2.1 and this amendment becomes a historical record. New ADRs continue from D-081.*
