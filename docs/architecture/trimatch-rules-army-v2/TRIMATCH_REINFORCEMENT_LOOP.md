# Tri-Match Reinforcement Loop

Production traffic should not auto-edit rules. LLMs/NLPs propose candidates, humans approve them, and rule packs are versioned.

```
Production turn / diagnostic failure / LLM disagreement
→ candidate proposal
→ human review
→ staged rule
→ eval
→ shadow
→ calibration
→ active
→ shortcut only after promotion gates
```

## Core metrics

- service_intent_precision
- service_intent_recall
- query_intent_precision
- query_intent_recall
- funnel_stage_precision
- funnel_stage_recall
- negation_accuracy
- counterfactual_accuracy
- multi_service_accuracy
- human_override_rate
- LLM_disagreement_rate
- shortcut_error_rate
