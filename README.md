# agentic-ai-fraud-guardrails

Agentic AI Engineering Patterns — Fraud guardrails example application in Fintech.

## Overview

This repository contains a Python prototype for an autonomous fraud guardrail control loop.
It shows how an agent reasoning layer can be combined with immutable comparator rules to approve transactions, re-evaluate breaches, and escalate high-risk decisions to human review.

## What it does

The core implementation is in `fraud_guardrail.py`.
It models the following flow:

1. `TransactionEvent` is recorded in an entity state store.
2. `agent_reasoning_layer()` produces an `AgentDecision`.
3. `comparator()` applies immutable financial rules.
4. If the decision passes, the transaction is forwarded to the actuator.
5. If a breach occurs, the guardrail performs one re-evaluation with breach context.
6. If the second cycle still breaches, a HITL signal is emitted.

## Key components

- `TransactionEvent`: transaction payload with event id, entity id, amount, currency, merchant category, timestamp, and metadata.
- `AgentDecision`: agent candidate decision including risk level, rationale, suggested action, confidence, and evaluation cycle.
- `ComparatorResult`: comparator verdict that can be `PASS` or `BREACH`, with rule violated and breach delta.
- `HITLSignal`: escalation signal generated when the guardrail cannot resolve a breach after re-evaluation.
- `FraudGuardrail`: orchestrates the control loop, records transactions, audits each decision cycle, and either approves or escalates.

## Immutable rules enforced by the comparator

The prototype uses a fixed `RULES` set that is intentionally not exposed to the agent reasoning layer:

- `MAX_SINGLE_TRANSACTION`: 50,000.00
- `MAX_24H_AGGREGATE`: 100,000.00
- `MAX_VELOCITY_PER_HOUR`: 10
- `BLOCKED_MERCHANT_CATEGORIES`: `7995`, `6050`, `9406`
- `MAX_AGENT_CONFIDENCE_FOR_HIGH_RISK`: 0.70
- `MAX_EVALUATION_CYCLES`: 2

Additionally, the comparator rejects cases where the agent approves a `HIGH` risk decision with overconfidence.

## Running the sample

From the repository root, run:

```bash
python fraud_guardrail.py
```

The script generates a sample transaction stream with 8 events and logs the outcome of each guardrail processing step.

## Notes

- `agent_reasoning_layer()` is a stub implementation and should be replaced with a real LLM or decision service integration in production.
- `consume_event_stream()` is an asynchronous consumer stub for a Kafka-style event stream.
- The sample currently logs audit trail entries and HITL signal emissions to the console.

