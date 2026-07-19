"""
fraud_guardrail.py
==================
Agentic Engineering Masterclass — Autonomous Fraud Guardrails with Python
Author: Chrisogonas (Agentic Engineering Patterns Series)

Core implementation of the FraudGuardrail feedback control loop.
Companion to the lecture slide deck. Full walkthrough in the IDE session.

Architecture:
    TransactionEvent
        └── AgentReasoningLayer   (LLM candidate decision)
              └── Comparator        (immutable rule enforcement)
                    ├── PASS  ──► ActuatorBus (approve / downstream)
                    └── BREACH ──► CorrectionLoop
                                        ├── Re-evaluation (once)
                                        └── HITL escalation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("fraud_guardrail")


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ComparatorVerdict(Enum):
    PASS = auto()
    BREACH = auto()


@dataclass
class TransactionEvent:
    event_id: str
    entity_id: str
    amount: float
    currency: str
    merchant_category: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        payload = f"{self.event_id}{self.entity_id}{self.amount}{self.timestamp}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class AgentDecision:
    event_id: str
    entity_id: str
    risk_level: RiskLevel
    rationale: str
    suggested_action: str
    agent_confidence: float
    evaluation_cycle: int = 1


@dataclass
class ComparatorResult:
    verdict: ComparatorVerdict
    rule_violated: Optional[str]
    delta: float
    state_snapshot: Dict[str, Any]
    agent_decision: AgentDecision

    def is_breach(self) -> bool:
        return self.verdict == ComparatorVerdict.BREACH


@dataclass
class HITLSignal:
    signal_id: str
    event_id: str
    entity_id: str
    rule_violated: str
    delta: float
    state_snapshot: Dict[str, Any]
    agent_decision: AgentDecision
    escalated_at: float
    audit_hash: str


# ---------------------------------------------------------------------------
# Immutable Financial Rules — NOT accessible to the agent reasoning layer
# ---------------------------------------------------------------------------

RULES: Dict[str, Any] = {
    "MAX_SINGLE_TRANSACTION":              50_000.00,
    "MAX_24H_AGGREGATE":                  100_000.00,
    "MAX_VELOCITY_PER_HOUR":              10,
    "BLOCKED_MERCHANT_CATEGORIES":        {"7995", "6050", "9406"},
    "MAX_AGENT_CONFIDENCE_FOR_HIGH_RISK": 0.70,
    "MAX_EVALUATION_CYCLES":              2,
}


class EntityStateStore:
    def __init__(self) -> None:
        self._aggregates: Dict[str, List[Dict]] = defaultdict(list)

    def record_transaction(self, event: TransactionEvent) -> None:
        self._aggregates[event.entity_id].append(
            {"amount": event.amount, "timestamp": event.timestamp}
        )

    def get_24h_aggregate(self, entity_id: str, as_of: float) -> float:
        cutoff = as_of - 86_400
        return sum(
            tx["amount"]
            for tx in self._aggregates[entity_id]
            if tx["timestamp"] >= cutoff
        )

    def get_hourly_velocity(self, entity_id: str, as_of: float) -> int:
        cutoff = as_of - 3_600
        return sum(
            1 for tx in self._aggregates[entity_id]
            if tx["timestamp"] >= cutoff
        )

    def snapshot(self, entity_id: str) -> Dict[str, Any]:
        return {
            "entity_id": entity_id,
            "total_events_on_record": len(self._aggregates[entity_id]),
        }


def comparator(
    agent_decision: AgentDecision,
    event: TransactionEvent,
    state: EntityStateStore,
) -> ComparatorResult:
    now = event.timestamp
    snapshot = state.snapshot(event.entity_id)

    if event.amount > RULES["MAX_SINGLE_TRANSACTION"]:
        return ComparatorResult(
            verdict=ComparatorVerdict.BREACH,
            rule_violated="MAX_SINGLE_TRANSACTION",
            delta=event.amount - RULES["MAX_SINGLE_TRANSACTION"],
            state_snapshot=snapshot,
            agent_decision=agent_decision,
        )

    aggregate_24h = state.get_24h_aggregate(event.entity_id, as_of=now)
    if aggregate_24h > RULES["MAX_24H_AGGREGATE"]:
        return ComparatorResult(
            verdict=ComparatorVerdict.BREACH,
            rule_violated="MAX_24H_AGGREGATE",
            delta=aggregate_24h - RULES["MAX_24H_AGGREGATE"],
            state_snapshot=snapshot,
            agent_decision=agent_decision,
        )

    velocity = state.get_hourly_velocity(event.entity_id, as_of=now)
    if velocity > RULES["MAX_VELOCITY_PER_HOUR"]:
        return ComparatorResult(
            verdict=ComparatorVerdict.BREACH,
            rule_violated="MAX_VELOCITY_PER_HOUR",
            delta=velocity - RULES["MAX_VELOCITY_PER_HOUR"],
            state_snapshot=snapshot,
            agent_decision=agent_decision,
        )

    if event.merchant_category in RULES["BLOCKED_MERCHANT_CATEGORIES"]:
        return ComparatorResult(
            verdict=ComparatorVerdict.BREACH,
            rule_violated="BLOCKED_MERCHANT_CATEGORY",
            delta=0.0,
            state_snapshot=snapshot,
            agent_decision=agent_decision,
        )

    if (
        agent_decision.risk_level == RiskLevel.HIGH
        and agent_decision.suggested_action == "APPROVE"
        and agent_decision.agent_confidence > RULES["MAX_AGENT_CONFIDENCE_FOR_HIGH_RISK"]
    ):
        return ComparatorResult(
            verdict=ComparatorVerdict.BREACH,
            rule_violated="OVERCONFIDENT_HIGH_RISK_APPROVE",
            delta=agent_decision.agent_confidence - RULES["MAX_AGENT_CONFIDENCE_FOR_HIGH_RISK"],
            state_snapshot=snapshot,
            agent_decision=agent_decision,
        )

    return ComparatorResult(
        verdict=ComparatorVerdict.PASS,
        rule_violated=None,
        delta=0.0,
        state_snapshot=snapshot,
        agent_decision=agent_decision,
    )


def agent_reasoning_layer(
    event: TransactionEvent,
    breach_context: Optional[str] = None,
    evaluation_cycle: int = 1,
) -> AgentDecision:
    """
    Stub — replace with your LLM API call.
    On breach_context: include violated rule + state in the prompt.
    Parse the LLM structured JSON response into AgentDecision.
    """
    risk = RiskLevel.LOW
    action = "APPROVE"
    confidence = 0.85

    if event.amount > 40_000:
        risk = RiskLevel.HIGH
        action = "HOLD"
        confidence = 0.60

    if breach_context:
        log.info("[Agent] Re-evaluating with breach context: %s", breach_context)
        risk = RiskLevel.CRITICAL
        action = "DECLINE"
        confidence = 0.95

    return AgentDecision(
        event_id=event.event_id,
        entity_id=event.entity_id,
        risk_level=risk,
        rationale=f"Evaluated event {event.event_id}. Cycle {evaluation_cycle}.",
        suggested_action=action,
        agent_confidence=confidence,
        evaluation_cycle=evaluation_cycle,
    )


def emit_hitl_signal(result: ComparatorResult) -> HITLSignal:
    decision = result.agent_decision
    audit_hash = hashlib.sha256(
        json.dumps(asdict(decision), sort_keys=True).encode()
    ).hexdigest()

    signal = HITLSignal(
        signal_id=str(uuid.uuid4()),
        event_id=decision.event_id,
        entity_id=decision.entity_id,
        rule_violated=result.rule_violated or "UNKNOWN",
        delta=result.delta,
        state_snapshot=result.state_snapshot,
        agent_decision=decision,
        escalated_at=time.time(),
        audit_hash=audit_hash,
    )
    log.warning(
        "[HITL] Escalation emitted | signal_id=%s | rule=%s | delta=%.2f | entity=%s",
        signal.signal_id, signal.rule_violated, signal.delta, signal.entity_id,
    )
    return signal


class FraudGuardrail:
    """
    Autonomous Fraud Guardrail — Feedback Control Loop.

    Lifecycle per event:
        1. Record event in state store.
        2. Request candidate decision from agent reasoning layer.
        3. Pass through Comparator.
        4. BREACH: augment prompt with breach context, re-evaluate once.
        5. Second BREACH: emit HITL signal, freeze transaction.
        6. PASS: forward to actuator bus.
    """

    def __init__(
        self,
        state_store: Optional[EntityStateStore] = None,
        actuator: Optional[Callable[[AgentDecision], None]] = None,
        hitl_emitter: Optional[Callable[[ComparatorResult], HITLSignal]] = None,
    ) -> None:
        self.state = state_store or EntityStateStore()
        self.actuator = actuator or self._default_actuator
        self.hitl_emitter = hitl_emitter or emit_hitl_signal
        self._audit_trail: List[Dict[str, Any]] = []

    async def process(self, event: TransactionEvent) -> Dict[str, Any]:
        log.info("[Guardrail] Processing %s | entity=%s | amount=%.2f",
                 event.event_id, event.entity_id, event.amount)
        self.state.record_transaction(event)
        return await self._run_control_loop(event)

    async def _run_control_loop(self, event: TransactionEvent) -> Dict[str, Any]:
        cycle = 1
        breach_context: Optional[str] = None

        while cycle <= RULES["MAX_EVALUATION_CYCLES"]:
            decision = agent_reasoning_layer(event, breach_context, cycle)
            log.info("[Agent] Cycle %d | action=%s | risk=%s | confidence=%.2f",
                     cycle, decision.suggested_action,
                     decision.risk_level.value, decision.agent_confidence)

            result = comparator(decision, event, self.state)
            self._log_audit(event, decision, result, cycle)

            if not result.is_breach():
                log.info("[Comparator] PASS — forwarding to actuator")
                self.actuator(decision)
                return {
                    "outcome": "APPROVED",
                    "event_id": event.event_id,
                    "cycle": cycle,
                    "decision": asdict(decision),
                }

            log.warning("[Comparator] BREACH | rule=%s | delta=%.4f | cycle=%d",
                        result.rule_violated, result.delta, cycle)

            if cycle < RULES["MAX_EVALUATION_CYCLES"]:
                breach_context = (
                    f"Rule violated: {result.rule_violated}. "
                    f"Delta above threshold: {result.delta:.2f}. "
                    f"Entity state: {json.dumps(result.state_snapshot)}."
                )
                cycle += 1
            else:
                signal = self.hitl_emitter(result)
                return {
                    "outcome": "ESCALATED_TO_HITL",
                    "event_id": event.event_id,
                    "cycles_attempted": cycle,
                    "hitl_signal_id": signal.signal_id,
                    "rule_violated": result.rule_violated,
                }

        return {"outcome": "ERROR", "event_id": event.event_id}

    @staticmethod
    def _default_actuator(decision: AgentDecision) -> None:
        log.info("[Actuator] Forwarded | action=%s | event=%s",
                 decision.suggested_action, decision.event_id)

    def _log_audit(self, event, decision, result, cycle) -> None:
        self._audit_trail.append({
            "fingerprint": event.fingerprint(),
            "event_id": event.event_id,
            "entity_id": event.entity_id,
            "cycle": cycle,
            "verdict": result.verdict.name,
            "rule_violated": result.rule_violated,
            "agent_action": decision.suggested_action,
            "timestamp": time.time(),
        })

    def audit_trail(self) -> List[Dict[str, Any]]:
        return list(self._audit_trail)


async def consume_event_stream(
    guardrail: FraudGuardrail,
    event_stream: List[TransactionEvent],
) -> None:
    """
    Async Kafka consumer stub.
    Replace with aiokafka AIOKafkaConsumer in production.
    Commit offsets only after successful guardrail processing.
    """
    results = await asyncio.gather(*[guardrail.process(e) for e in event_stream])
    for r in results:
        log.info("[Consumer] Result: %s", json.dumps(r, indent=2))


if __name__ == "__main__":
    now = time.time()
    entity = "entity_ACME_001"

    # Layered Transaction Masking scenario:
    # 7 transactions of $15,000 each = $105,000 aggregate — exceeds 24h rule.
    # Each individual transaction passes the single-transaction check.
    events = [
        TransactionEvent(
            event_id=f"evt_{i:03d}",
            entity_id=entity,
            amount=15_000.00,
            currency="USD",
            merchant_category="5411",
            timestamp=now - (7 - i) * 3600,
        )
        for i in range(7)
    ]
    events.append(TransactionEvent(
        event_id="evt_final",
        entity_id=entity,
        amount=18_000.00,
        currency="USD",
        merchant_category="5411",
        timestamp=now,
    ))

    guardrail = FraudGuardrail()
    asyncio.run(consume_event_stream(guardrail, events))

    print("\n=== Audit Trail ===")
    for entry in guardrail.audit_trail():
        print(json.dumps(entry, indent=2))
