"""Artifact schema package — typed Pydantic models for every workforce artifact type."""

from memory.artifact_schemas.base import (
    ArtifactBase,
    ArtifactStatus,
    ArtifactType,
    # Discovery & Planning
    ProductBrief,
    MarketResearch,
    PRD,
    # Architecture
    ArchitectureDoc,
    APISpec,
    DBSchema,
    # Build
    CodeChangeSet,
    # Quality
    TestReport,
    SecurityReport,
    CodeReviewReport,
    # Deployment
    DeploymentPlan,
    DeploymentRecord,
    MonitoringConfig,
    # Post-Launch
    IncidentRecord,
    FeedbackDigest,
    ReleaseDecision,
    ARTIFACT_REGISTRY,
)

__all__ = [
    "ArtifactBase",
    "ArtifactStatus",
    "ArtifactType",
    "ProductBrief",
    "MarketResearch",
    "PRD",
    "ArchitectureDoc",
    "APISpec",
    "DBSchema",
    "CodeChangeSet",
    "TestReport",
    "SecurityReport",
    "CodeReviewReport",
    "DeploymentPlan",
    "DeploymentRecord",
    "MonitoringConfig",
    "IncidentRecord",
    "FeedbackDigest",
    "ReleaseDecision",
    "ARTIFACT_REGISTRY",
]
