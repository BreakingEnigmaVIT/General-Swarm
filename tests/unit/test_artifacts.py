"""
Unit tests for the Artifact Registry and schema validation (§19, Phase 12).

Tests run fully in-memory (no ChromaDB required) using the fallback dict backend.
"""

from __future__ import annotations

import pytest
import tempfile
import asyncio
from pathlib import Path


# ── Schema validation tests ───────────────────────────────────────────────────

class TestArtifactSchemas:
    def test_product_brief_valid(self):
        from memory.artifact_schemas.base import ProductBrief, ArtifactType, ArtifactStatus
        b = ProductBrief(goal_statement="Build X", target_market="Developers")
        assert b.artifact_type == ArtifactType.product_brief.value or b.artifact_type == ArtifactType.product_brief
        assert b.status == ArtifactStatus.draft.value or b.status == ArtifactStatus.draft
        assert b.id  # auto-generated

    def test_product_brief_missing_required(self):
        from memory.artifact_schemas.base import ProductBrief
        import pydantic
        with pytest.raises((pydantic.ValidationError, TypeError)):
            ProductBrief()  # goal_statement is required

    def test_prd_structured_fields(self):
        from memory.artifact_schemas.base import PRD, FunctionalRequirement
        req = FunctionalRequirement(
            id="FR-1",
            title="User login",
            description="Users can log in with email/password",
            priority="must",
            acceptance_criteria=["Given valid credentials, when login, then authenticated"],
        )
        prd = PRD(
            problem_statement="No login system",
            target_user="End users",
            functional_requirements=[req],
        )
        assert len(prd.functional_requirements) == 1
        assert prd.functional_requirements[0].priority == "must"

    def test_code_change_set_requires_reversal_plan(self):
        from memory.artifact_schemas.base import CodeChangeSet, ReversalPlan
        import pydantic
        # Empty reversal plan should raise
        with pytest.raises((ValueError, pydantic.ValidationError)):
            cs = CodeChangeSet(
                layer="backend",
                reversal_plan=ReversalPlan(description=""),  # empty — invalid
            )
            # model_post_init raises ValueError, but Pydantic may wrap it
            # So validate manually
            cs.model_post_init(None)

    def test_code_change_set_valid_reversal(self):
        from memory.artifact_schemas.base import CodeChangeSet, ReversalPlan, FileChange
        cs = CodeChangeSet(
            layer="backend",
            files_changed=[FileChange(path="app/main.py", operation="create")],
            reversal_plan=ReversalPlan(
                description="Delete app/main.py",
                steps=["git revert HEAD"],
            ),
        )
        assert cs.layer == "backend"

    def test_deployment_record_requires_reversal(self):
        from memory.artifact_schemas.base import DeploymentRecord, ReversalPlan
        with pytest.raises((ValueError, Exception)):
            dr = DeploymentRecord(
                environment="production",
                strategy_used="rolling",
                reversal_plan=ReversalPlan(description=""),
            )
            dr.model_post_init(None)

    def test_release_decision_verdict_enum(self):
        from memory.artifact_schemas.base import ReleaseDecision, ReleaseVerdict
        rd = ReleaseDecision(
            verdict=ReleaseVerdict.hotfix,
            routing_phase="build",
            changelog_draft="- Fixed critical bug",
        )
        assert "hotfix" in (rd.verdict if isinstance(rd.verdict, str) else rd.verdict.value)

    def test_security_report_blocks_progression(self):
        from memory.artifact_schemas.base import SecurityReport, SecurityFinding
        finding = SecurityFinding(
            tool="semgrep",
            severity="critical",
            title="SQL injection",
            location="app/db.py:42",
        )
        sr = SecurityReport(findings=[finding], blocks_progression=True)
        assert sr.blocks_progression is True

    def test_artifact_registry_contains_all_types(self):
        from memory.artifact_schemas.base import ARTIFACT_REGISTRY, ArtifactType
        for art_type in ArtifactType:
            assert art_type.value in ARTIFACT_REGISTRY, \
                f"ArtifactType.{art_type.name} not in ARTIFACT_REGISTRY"

    def test_lineage_field(self):
        from memory.artifact_schemas.base import PRD, ProductBrief
        brief = ProductBrief(goal_statement="x")
        prd = PRD(
            problem_statement="y",
            target_user="z",
            lineage=[brief.id],
        )
        assert brief.id in prd.lineage


# ── ArtifactRegistry tests ────────────────────────────────────────────────────

class TestArtifactRegistry:
    @pytest.fixture
    def registry(self, tmp_path):
        from memory.artifacts import ArtifactRegistry
        return ArtifactRegistry(persist_dir=str(tmp_path))

    def test_create_and_get(self, registry):
        from memory.artifact_schemas.base import ProductBrief

        async def _run():
            brief = ProductBrief(goal_statement="Build a SaaS", stage_id="discovery")
            created = await registry.create(brief)
            assert created.id == brief.id
            fetched = await registry.get_by_id(brief.id)
            assert fetched is not None
            assert fetched.id == brief.id

        asyncio.run(_run())

    def test_approve(self, registry):
        from memory.artifact_schemas.base import ProductBrief, ArtifactStatus

        async def _run():
            brief = ProductBrief(goal_statement="Approve me")
            await registry.create(brief)
            approved = await registry.approve(brief.id)
            assert approved is not None
            status = approved.status if isinstance(approved.status, str) else approved.status.value
            assert status == ArtifactStatus.approved.value

        asyncio.run(_run())

    def test_supersede(self, registry):
        from memory.artifact_schemas.base import ProductBrief, ArtifactStatus

        async def _run():
            v1 = ProductBrief(goal_statement="v1")
            v2 = ProductBrief(goal_statement="v2", lineage=[v1.id])
            await registry.create(v1)
            await registry.approve(v1.id)
            await registry.create(v2)
            await registry.supersede(v1.id, v2.id)
            fetched = await registry.get_by_id(v1.id)
            assert fetched is not None
            status = fetched.status if isinstance(fetched.status, str) else fetched.status.value
            assert status == ArtifactStatus.superseded.value

        asyncio.run(_run())

    def test_get_latest_by_type(self, registry):
        from memory.artifact_schemas.base import ProductBrief

        async def _run():
            b1 = ProductBrief(goal_statement="v1", project_id="proj1")
            b2 = ProductBrief(goal_statement="v2", project_id="proj1")
            await registry.create(b1)
            await registry.approve(b1.id)
            await registry.create(b2)
            await registry.approve(b2.id)

            latest = await registry.get_latest_by_type("ProductBrief", project_id="proj1")
            assert latest is not None
            # Should be b2 (created later)
            assert latest.goal_statement == "v2"

        asyncio.run(_run())

    def test_list_by_stage(self, registry):
        from memory.artifact_schemas.base import ProductBrief, MarketResearch

        async def _run():
            b = ProductBrief(goal_statement="x", stage_id="discovery")
            m = MarketResearch(trend_summary="y", stage_id="discovery")
            other = ProductBrief(goal_statement="z", stage_id="planning")
            for art in [b, m, other]:
                await registry.create(art)

            items = await registry.list_by_stage("discovery")
            ids = {a.id for a in items}
            assert b.id in ids
            assert m.id in ids
            assert other.id not in ids

        asyncio.run(_run())

    def test_lineage_walk(self, registry):
        from memory.artifact_schemas.base import ProductBrief, MarketResearch, PRD

        async def _run():
            brief = ProductBrief(goal_statement="root")
            research = MarketResearch(trend_summary="trends", lineage=[brief.id])
            prd = PRD(
                problem_statement="prob",
                target_user="users",
                lineage=[research.id],
            )
            for art in [brief, research, prd]:
                await registry.create(art)

            chain = await registry.get_lineage(prd.id)
            ids = [a.id for a in chain]
            assert research.id in ids

        asyncio.run(_run())

    def test_audit_log_written(self, tmp_path):
        from memory.artifacts import ArtifactRegistry
        from memory.artifact_schemas.base import ProductBrief
        import json

        async def _run():
            reg = ArtifactRegistry(persist_dir=str(tmp_path))
            brief = ProductBrief(goal_statement="audit test")
            await reg.create(brief)
            await reg.approve(brief.id)
            return reg

        asyncio.run(_run())

        audit_file = tmp_path / "artifact_audit.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) >= 2  # create + approve
        entries = [json.loads(l) for l in lines if l]
        to_values = {e["to"] for e in entries}
        assert "draft" in to_values
        assert "approved" in to_values

    def test_unknown_artifact_id_returns_none(self, registry):
        async def _run():
            return await registry.get_by_id("nonexistent-id-000")

        result = asyncio.run(_run())
        assert result is None


# ── Tool handler tests ────────────────────────────────────────────────────────

class TestArtifactTools:
    @pytest.fixture
    def registry(self, tmp_path):
        from memory.artifacts import ArtifactRegistry
        return ArtifactRegistry(persist_dir=str(tmp_path))

    def test_artifact_write_tool(self, registry):
        import tools.artifact_write.handler as aw
        aw.set_registry(registry)

        async def _run():
            result = await aw.handler._run({
                "artifact_type": "ProductBrief",
                "payload": {
                    "goal_statement": "Build a tool",
                    "target_market": "Engineers",
                },
                "stage_id": "discovery",
                "author_agent_id": "market_research",
            })
            return result

        result = asyncio.run(_run())
        assert "artifact_id" in result
        assert "error" not in result
        assert result["artifact_type"] == "ProductBrief"

    def test_artifact_write_invalid_type(self, registry):
        import tools.artifact_write.handler as aw
        aw.set_registry(registry)

        async def _run():
            return await aw.handler._run({
                "artifact_type": "NonExistentType",
                "payload": {},
            })

        result = asyncio.run(_run())
        assert "error" in result

    def test_artifact_read_by_id(self, registry):
        import tools.artifact_write.handler as aw
        import tools.artifact_read.handler as ar
        aw.set_registry(registry)
        ar.set_registry(registry)

        async def _run():
            write_result = await aw.handler._run({
                "artifact_type": "ProductBrief",
                "payload": {"goal_statement": "Read me back"},
            })
            artifact_id = write_result["artifact_id"]
            read_result = await ar.handler._run({
                "query_type": "by_id",
                "artifact_id": artifact_id,
            })
            return read_result

        result = asyncio.run(_run())
        assert result["count"] == 1
        assert result["artifacts"][0]["goal_statement"] == "Read me back"

    def test_artifact_read_latest_by_type(self, registry):
        import tools.artifact_write.handler as aw
        import tools.artifact_read.handler as ar
        aw.set_registry(registry)
        ar.set_registry(registry)

        async def _run():
            await aw.handler._run({
                "artifact_type": "MarketResearch",
                "payload": {"trend_summary": "AI is growing"},
                "auto_approve": True,
            })
            result = await ar.handler._run({
                "query_type": "latest_by_type",
                "artifact_type": "MarketResearch",
                "status_filter": "approved",
            })
            return result

        result = asyncio.run(_run())
        assert result["count"] == 1
        assert result["artifacts"][0]["trend_summary"] == "AI is growing"


# ── Lifecycle YAML loading ────────────────────────────────────────────────────

class TestLifecycleLoading:
    def test_software_delivery_lifecycle_loads(self):
        from coordination.orchestrator import _load_lifecycle
        lifecycle = _load_lifecycle("software_delivery")
        assert "phases" in lifecycle
        phase_ids = [p["id"] for p in lifecycle["phases"]]
        assert "discovery" in phase_ids
        assert "planning" in phase_ids
        assert "architecture" in phase_ids
        assert "build" in phase_ids
        assert "quality" in phase_ids
        assert "deployment" in phase_ids
        assert "post_launch" in phase_ids
        assert "iteration" in phase_ids

    def test_lifecycle_phases_have_required_fields(self):
        from coordination.orchestrator import _load_lifecycle
        lifecycle = _load_lifecycle("software_delivery")
        for phase in lifecycle["phases"]:
            assert "id" in phase
            assert "name" in phase
            assert "default_agents" in phase
            assert "required_input_artifact_types" in phase
            assert "required_output_artifact_types" in phase

    def test_budget_allocation_present(self):
        from coordination.orchestrator import _load_lifecycle
        lifecycle = _load_lifecycle("software_delivery")
        alloc = lifecycle.get("budget_allocation", {})
        assert alloc, "budget_allocation must be non-empty"
        total = sum(alloc.values())
        assert total <= 100, f"Budget allocation {total}% exceeds 100%"
