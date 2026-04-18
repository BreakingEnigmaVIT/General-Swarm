"""
Canonical Pydantic schemas for every workforce artifact type.

All artifact types inherit ArtifactBase which carries identity + lineage metadata.
Free-text fields are minimised; structured sub-fields are preferred per §19.3.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Type

from pydantic import BaseModel, Field


# ── Status & type enums ───────────────────────────────────────────────────────

class ArtifactStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"


class ArtifactType(str, Enum):
    product_brief = "ProductBrief"
    market_research = "MarketResearch"
    prd = "PRD"
    architecture_doc = "ArchitectureDoc"
    api_spec = "APISpec"
    db_schema = "DBSchema"
    code_change_set = "CodeChangeSet"
    test_report = "TestReport"
    security_report = "SecurityReport"
    code_review_report = "CodeReviewReport"
    deployment_plan = "DeploymentPlan"
    deployment_record = "DeploymentRecord"
    monitoring_config = "MonitoringConfig"
    incident_record = "IncidentRecord"
    feedback_digest = "FeedbackDigest"
    release_decision = "ReleaseDecision"


# ── Base model ────────────────────────────────────────────────────────────────

class ArtifactBase(BaseModel):
    """Metadata fields carried by every artifact."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artifact_type: ArtifactType
    version: int = 1
    stage_id: str = ""
    author_agent_id: str = ""
    project_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ArtifactStatus = ArtifactStatus.draft
    lineage: list[str] = Field(default_factory=list)  # parent artifact ids

    class Config:
        use_enum_values = True


# ── Discovery & Planning ──────────────────────────────────────────────────────

class ProductBrief(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.product_brief
    goal_statement: str = ""
    target_market: str = ""
    key_constraints: list[str] = Field(default_factory=list)
    raw_goal: str = ""


class CompetitorEntry(BaseModel):
    name: str
    pricing: str = ""
    positioning: str = ""
    gaps: list[str] = Field(default_factory=list)
    source_url: str = ""


class PainPoint(BaseModel):
    description: str
    evidence_url: str = ""
    severity: str = "medium"  # low | medium | high


class MarketResearch(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.market_research
    trend_summary: str = ""
    competitors: list[CompetitorEntry] = Field(default_factory=list)
    pain_points: list[PainPoint] = Field(default_factory=list)
    recommended_positioning: str = ""
    sources: list[str] = Field(default_factory=list)


class FunctionalRequirement(BaseModel):
    id: str
    title: str
    description: str
    priority: str = "should"  # must | should | could | wont  (MoSCoW)
    acceptance_criteria: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(BaseModel):
    category: str  # performance | security | scalability | ux | ...
    description: str


class PRD(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.prd
    problem_statement: str = ""
    target_user: str = ""
    success_metrics: list[str] = Field(default_factory=list)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    prioritization_framework: str = "MoSCoW"


# ── Architecture ──────────────────────────────────────────────────────────────

class ComponentSpec(BaseModel):
    name: str
    responsibility: str
    technology: str = ""


class TechChoice(BaseModel):
    area: str  # frontend | backend | database | infra | ...
    choice: str
    rationale: str


class APIEndpoint(BaseModel):
    method: str   # GET | POST | PUT | DELETE | PATCH
    path: str
    purpose: str
    auth_required: bool = True


class ArchitectureDoc(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.architecture_doc
    system_context: str = ""
    components: list[ComponentSpec] = Field(default_factory=list)
    tech_stack: list[TechChoice] = Field(default_factory=list)
    api_endpoints: list[APIEndpoint] = Field(default_factory=list)
    data_model_summary: str = ""
    non_functional_notes: str = ""
    # References to Mermaid diagram artifact ids
    diagram_refs: list[str] = Field(default_factory=list)


class APISpec(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.api_spec
    service_name: str = ""
    base_path: str = "/"
    endpoints: list[APIEndpoint] = Field(default_factory=list)
    openapi_source: str = ""   # path to generated OpenAPI YAML in workspace


class EntityField(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    description: str = ""


class Entity(BaseModel):
    name: str
    fields: list[EntityField] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class DBSchema(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.db_schema
    database_engine: str = "postgres"
    entities: list[Entity] = Field(default_factory=list)
    migration_tool: str = "alembic"


# ── Build ─────────────────────────────────────────────────────────────────────

class FileChange(BaseModel):
    path: str
    operation: str  # create | modify | delete
    description: str = ""


class ReversalPlan(BaseModel):
    description: str
    steps: list[str] = Field(default_factory=list)


class CodeChangeSet(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.code_change_set
    layer: str = ""  # frontend | backend | database | integration | devops
    files_changed: list[FileChange] = Field(default_factory=list)
    summary: str = ""
    reversal_plan: ReversalPlan = Field(default_factory=lambda: ReversalPlan(description="revert via git"))


# ── Quality ───────────────────────────────────────────────────────────────────

class TestSuite(BaseModel):
    name: str
    kind: str  # unit | integration | e2e
    pass_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    coverage_pct: float = 0.0


class UncoveredCriterion(BaseModel):
    requirement_id: str
    reason: str = ""


class TestReport(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.test_report
    suites: list[TestSuite] = Field(default_factory=list)
    overall_pass: bool = False
    overall_coverage_pct: float = 0.0
    uncovered_acceptance_criteria: list[UncoveredCriterion] = Field(default_factory=list)


class SecurityFinding(BaseModel):
    tool: str
    severity: str  # critical | high | medium | low | info
    title: str
    location: str = ""
    description: str = ""
    cve: str = ""


class SecurityReport(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.security_report
    findings: list[SecurityFinding] = Field(default_factory=list)
    owasp_checklist: dict[str, bool] = Field(default_factory=dict)
    blocks_progression: bool = False  # True if any Critical/High


class ReviewComment(BaseModel):
    file_path: str = ""
    line: Optional[int] = None
    severity: str  # error | warning | info
    message: str
    tool: str = "llm"  # linter | ast | llm


class ReviewVerdict(str, Enum):
    approve = "approve"
    request_changes = "request_changes"
    block = "block"


class CodeReviewReport(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.code_review_report
    comments: list[ReviewComment] = Field(default_factory=list)
    verdict: ReviewVerdict = ReviewVerdict.request_changes
    architectural_concerns: list[str] = Field(default_factory=list)

    class Config:
        use_enum_values = True


# ── Deployment ────────────────────────────────────────────────────────────────

class PipelineStage(BaseModel):
    name: str
    description: str = ""
    rollback_step: str = ""


class DeploymentPlan(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.deployment_plan
    target_environment: str = "staging"  # staging | production | local
    strategy: str = "rolling"  # rolling | blue_green | canary
    pipeline_stages: list[PipelineStage] = Field(default_factory=list)
    ci_tool: str = "github_actions"


class DeploymentRecord(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.deployment_record
    environment: str = "staging"
    strategy_used: str = "rolling"
    image_digest: str = ""
    rollout_started_at: Optional[datetime] = None
    rollout_completed_at: Optional[datetime] = None
    rollback_handle: str = ""   # command or procedure reference
    reversal_plan: ReversalPlan = Field(default_factory=lambda: ReversalPlan(description="revert deployment"))


class SLODefinition(BaseModel):
    name: str
    query: str
    threshold: float
    window_minutes: int = 60


class MonitoringConfig(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.monitoring_config
    slos: list[SLODefinition] = Field(default_factory=list)
    alert_channels: list[str] = Field(default_factory=list)
    dashboard_refs: list[str] = Field(default_factory=list)


# ── Post-Launch ───────────────────────────────────────────────────────────────

class IncidentRecord(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.incident_record
    triggered_by: str  # slo_breach | alert | user_report
    slo_name: str = ""
    severity: str = "high"
    description: str = ""
    resolved: bool = False
    rollback_invoked: bool = False


class FeedbackTheme(BaseModel):
    theme: str
    sentiment: str  # positive | negative | neutral
    count: int = 1
    evidence_quotes: list[str] = Field(default_factory=list)
    prd_delta_suggestion: str = ""


class FeedbackDigest(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.feedback_digest
    source_count: int = 0
    themes: list[FeedbackTheme] = Field(default_factory=list)
    overall_sentiment: str = "neutral"
    recommended_prd_deltas: list[str] = Field(default_factory=list)


class ReleaseVerdict(str, Enum):
    hotfix = "hotfix"
    minor_update = "minor_update"
    major_respec = "major_respec"


class ReleaseDecision(ArtifactBase):
    artifact_type: ArtifactType = ArtifactType.release_decision
    verdict: ReleaseVerdict = ReleaseVerdict.minor_update
    routing_phase: str = "planning"  # phase id to re-enter: discovery | planning | build | ...
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    changelog_draft: str = ""

    class Config:
        use_enum_values = True


# ── Type registry (name → class) ──────────────────────────────────────────────

ARTIFACT_REGISTRY: dict[str, Type[ArtifactBase]] = {
    ArtifactType.product_brief.value: ProductBrief,
    ArtifactType.market_research.value: MarketResearch,
    ArtifactType.prd.value: PRD,
    ArtifactType.architecture_doc.value: ArchitectureDoc,
    ArtifactType.api_spec.value: APISpec,
    ArtifactType.db_schema.value: DBSchema,
    ArtifactType.code_change_set.value: CodeChangeSet,
    ArtifactType.test_report.value: TestReport,
    ArtifactType.security_report.value: SecurityReport,
    ArtifactType.code_review_report.value: CodeReviewReport,
    ArtifactType.deployment_plan.value: DeploymentPlan,
    ArtifactType.deployment_record.value: DeploymentRecord,
    ArtifactType.monitoring_config.value: MonitoringConfig,
    ArtifactType.incident_record.value: IncidentRecord,
    ArtifactType.feedback_digest.value: FeedbackDigest,
    ArtifactType.release_decision.value: ReleaseDecision,
}
