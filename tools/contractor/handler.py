"""Contractor tool — fills the CDD master prompt and delegates to contract_writer."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from core.task import TaskResult
from tools.base import ToolHandler

AgentFactory = Callable[[str, str], Coroutine[Any, Any, TaskResult]]

_factory: Optional[AgentFactory] = None


def set_factory(factory: AgentFactory) -> None:
    global _factory
    _factory = factory


# ── CDD Master Prompt Template ────────────────────────────────────────────────

_CDD_MASTER_PROMPT = """\
=============================================================================
MASTER PROMPT: CONTRACT-DRIVEN DEVELOPMENT (CDD) — API CONTRACT TEMPLATE
=============================================================================

ROLE
────
You are a senior API architect and Contract-Driven Development (CDD) expert
with deep knowledge of OpenAPI 3.x, AsyncAPI 2.x, Specmatic, Pact, backward
compatibility analysis, microservices integration patterns, and CI/CD pipeline
design. You will generate a complete, production-grade CDD Contract Template
document for the project described in the PROJECT CONTEXT section below.

This is not a skeleton or a sample — it is a fully filled, immediately usable
contract document. Every section must be complete. Where the user has marked
a placeholder as N/A, acknowledge it and skip that section cleanly.

=============================================================================
STARTING POINT CONTEXT  (critical — read before writing a single line)
=============================================================================

{starting_point_block}

=============================================================================
PROJECT CONTEXT
=============================================================================

PROJECT IDENTITY
  Project Name          : {project_name}
  Project Description   : {project_description}
  Business Domain       : {business_domain}
  Contract Version      : {contract_version}
  Contract Date         : {contract_date}
  Contract Owner Team   : {contract_owner_team}
  Contract Owner Email  : {contract_owner_email}
  Contract Status       : {contract_status}

ARCHITECTURE
  Full Architecture
  Description           : {architecture_description}
  All Services Involved : {services}
  Primary Protocol      : {primary_protocol}
  Async Broker          : {async_broker}
  Deployment Platform   : {deployment_platform}

CONSUMER
  Consumer Service Name : {consumer_service_name}
  Consumer Team         : {consumer_team}
  Consumer Tech Stack   : {consumer_tech_stack}
  Consumer Repo URL     : {consumer_repo_url}
  Consumer Local URL    : {consumer_local_url}

PROVIDER
  Provider Service Name : {provider_service_name}
  Provider Team         : {provider_team}
  Provider Tech Stack   : {provider_tech_stack}
  Provider Repo URL     : {provider_repo_url}
  Provider Local URL    : {provider_local_url}
  Provider CI URL       : {provider_ci_url}
  Health Check Path     : {provider_health_endpoint}
  Route Listing Path    : {actuator_mappings_path}

HTTP ENDPOINTS TO CONTRACT
{endpoints}

CORE DATA MODELS
{core_data_models}

ASYNC / EVENT TOPICS
{kafka_topics}

ERROR HANDLING POLICY
{error_policy}

CONTRACT REPOSITORY
  Central Repo URL      : {contract_repo_url}
  Folder Structure      : {contract_folder_structure}
  Linting Tool          : {linting_tool}
  CI/CD Platform        : {ci_cd_platform}

TEST DATA (Named Examples)
{test_data_examples}

TOOLING
  Contract Testing Tool : {contract_testing_tool}
  Specmatic Version     : {specmatic_version}
  Test Framework        : {test_framework}
  Build Tool            : {build_tool}

ADDITIONAL REQUIREMENTS
  Authentication        : {authentication}
  Rate Limiting         : {rate_limiting}
  Pagination Style      : {pagination_style}
  Custom Headers        : {custom_headers}
  Additional Notes      : {additional_notes}

=============================================================================
OUTPUT INSTRUCTIONS
=============================================================================

Using all of the project context above, generate the following ten
deliverables in order. Each deliverable must be complete and immediately
usable. Do not truncate any section. Do not say "add more as needed."
Do not leave any placeholder unfilled.

CRITICAL — your contract must reflect the STARTING POINT CONTEXT above:
  - If strategy is clone_existing: every deliverable describes DELTA changes
    to the cloned repo. Deliverable 3 (OpenAPI) covers ONLY the endpoints that
    differ from the cloned repo. Deliverable 8 covers what the cloned repo
    already satisfies vs. what must change.
  - If strategy is use_boilerplate: every deliverable describes what the
    boilerplate provides out-of-the-box vs. what must be added/changed.
    Name the boilerplate in Deliverable 1 and reference it throughout.
  - If strategy is from_scratch: the standard full-codebase contract applies.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 1 — CONTRACT HEADER & EXECUTIVE SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce a formal contract header block containing:
  - Project name, contract version, date, status, and owner
  - A two-to-three sentence executive summary explaining exactly what
    integration problem this contract solves and which teams it binds
  - A participants table with columns: Service Name | Role (Consumer/
    Provider) | Team | Tech Stack | Repository URL | Base URL
  - A section explaining the CDD principle as it applies to THIS project:
    why using a hand-rolled mock is dangerous for this specific system,
    what breaks if the consumer assumes the wrong field type, and how
    this contract prevents those silent failures

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 2 — ARCHITECTURE & SERVICE DEPENDENCY MAP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce:
  - A written architecture narrative describing the full request/response
    flow from the consumer through every service to the data layer and
    back, including any async events emitted along the way
  - A service dependency map table with columns: Service | Depends On
    (Consumes) | Depended On By | Protocol | Contract File Name
  - An integration risk table: for each consumer-provider pair, describe
    the specific failure mode that would occur WITHOUT this contract
  - A shift-left benefit statement: describe concretely what stage
    issues will now be caught (developer laptop) versus where they
    were caught before (integration environment)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 3 — COMPLETE OpenAPI 3.x SPECIFICATION (YAML)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce a complete, valid, ready-to-commit OpenAPI 3.x YAML file.

The file must include:

3a. OPENAPI HEADER
  openapi: 3.0.3
  info block with title, version, description, contact (team name and email)
  servers block with at least local dev URL and CI URL
  Full tags block listing every logical group of endpoints

3b. FOR EVERY ENDPOINT listed in PROJECT CONTEXT:
  Under paths, produce a complete path item containing:
  OPERATION OBJECT: operationId, summary, description, tags, security block
  PARAMETERS: name, in, required, schema with all constraints, description,
    examples block with at least one positive example (using TEST DATA)
    and one negative example. Naming convention: [HTTP_STATUS]_[scenario_name]
  REQUEST BODY (POST/PUT/PATCH): full schema with required array, all
    properties with types and constraints, examples block
  RESPONSES: every possible response code with description, schema $ref,
    and named examples that EXACTLY MATCH the parameter/requestBody example
    names (Specmatic requires this name-matching to stitch tests together)

3c. COMPONENTS SECTION:
  Define every reusable schema. Include StandardError and every domain
  entity from CORE_DATA_MODELS with full constraints and at least one example.

3d. SPECMATIC PATTERNS:
  For dynamic values use: (string), (number), (integer), (boolean),
  (datetime), (uuid) with a comment explaining why it is dynamic.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 4 — ASYNC / EVENT SPECIFICATION (AsyncAPI YAML)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If ASYNC_BROKER is N/A, write "Not applicable for this project." and skip.
Otherwise produce a complete AsyncAPI 2.x YAML with servers, channels,
message schemas, and a contract assertions table per topic.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 5 — NAMED EXAMPLES CATALOGUE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce a human-readable catalogue of EVERY named example in the OpenAPI
spec, organised by endpoint. For each example include:
  Example Name | Endpoint | Request Input | Expected Response |
  Test Category (POSITIVE/NEGATIVE) | Why This Example | Specmatic Behaviour
Positive examples: cover every TEST_DATA entity, every enum value,
  optional-field-present and optional-field-absent cases.
Negative examples: null for every mandatory field, wrong type for every
  typed field, invalid enum, 404 scenario, missing body (400), 503 timeout.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 6 — SHARED ERROR CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce:
6a. StandardError YAML schema (embedded in components/schemas)
6b. Error code catalogue table: HTTP Status | Scenario | Who Is At Fault |
    Provider Must Do | Provider Must NOT Do | Example Request That Triggers It
    Cover: 400, 401, 403, 404, 409, 422, 429, 500, 503.
6c. Hard rules bullet list (e.g. null input → 422 never 500, no stack traces,
    no 200 for failed operations, no empty 200 instead of 404).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 7 — GENERATIVE TESTING CONFIGURATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

7a. What Specmatic will auto-generate table per endpoint:
    Endpoint | Test Category | What Is Mutated | Expected HTTP Response
    Categories: null injection, wrong type, invalid enum, missing fields,
    boundary values, empty strings, extra unknown fields.
7b. Provider must-pass checklist before generative tests will pass.
7c. Expected test count: baseline (named) + generative + total with math.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 8 — BACKWARD COMPATIBILITY RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

8a. Semantic versioning policy for this contract (MAJOR / MINOR / PATCH)
    with concrete examples drawn from the actual fields in CORE_DATA_MODELS.
8b. Backward compatibility truth table (13 change scenarios, definitive YES/NO).
8c. specmatic compare command and what pipeline must do on breaking change.
8d. Migration guide template for MAJOR version bumps.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 9 — CI/CD PIPELINE INTEGRATION GUIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce a complete CI/CD integration guide for {ci_cd_platform}.
Include actual pipeline YAML snippets — not pseudocode.
9a. Central contract repo pipeline (lint → compat check → review → tag).
9b. Consumer CI pipeline (unit → pull contract → start stub → component tests).
9c. Provider CI pipeline (unit → pull contract → start app → contract tests
    FIRST → component tests → coverage check).
9d. Full ready-to-commit pipeline YAML for the PROVIDER CI.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERABLE 10 — SPECMATIC CONFIGURATION FILES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

10a. specmatic.json for the CONSUMER (stub mode).
10b. specmatic.json for the PROVIDER (test mode).
10c. Provider contract test class in {provider_tech_stack} with build
     dependency entry for {build_tool}.
10d. Stub interaction example in {consumer_tech_stack} showing how to call
     the /specmatic/expectations API and why the assertion matters.
10e. Fault injection stub JSON for the 503 timeout scenario.

=============================================================================
FORMATTING REQUIREMENTS
=============================================================================

1. Use clear section headers matching the Deliverable names.
2. All YAML in fenced yaml code blocks.
3. All other code in fenced code blocks labelled with the language.
4. All tables in Markdown table format.
5. Numbered lists for sequential steps; bullet lists for non-sequential items.
6. At the start of every Deliverable write one sentence explaining what it
   is and how it is used in the CDD workflow.
7. After all ten Deliverables produce a QUICK START CHECKLIST with the exact
   commands both teams must run to go from zero to both sides running against
   the contract.

=============================================================================
QUALITY RULES — FOLLOW WITHOUT EXCEPTION
=============================================================================

- Every example name in parameters/requestBody MUST exactly match the name
  in the responses block. Specmatic uses name-matching to stitch test cases.
- Every field marked required must appear in both required:[] and properties.
- Every nullable field must include nullable: true.
- StandardError MUST be the $ref for every 4xx and 5xx response.
- Backward compatibility table must give YES or NO — never "it depends."
- CI pipeline YAML must use real syntax for {ci_cd_platform}.
- Document must be self-contained — a developer new to CDD must be able to
  follow the Quick Start Checklist without reading any external documentation.

=== END PROMPT ===
"""


def _build_starting_point_block(inputs: dict[str, Any]) -> str:
    strategy = inputs.get("starting_point_strategy", "from_scratch")

    if strategy == "clone_existing":
        return (
            f"Strategy          : CLONE EXISTING REPO\n"
            f"Source Repo URL   : {inputs.get('cloned_repo_url', 'N/A')}\n"
            f"Local Cloned Path : {inputs.get('cloned_repo_path', 'N/A')}\n\n"
            "This contract is a DELTA CONTRACT. It specifies only what must change\n"
            "in the cloned repository to match the user's requirements. Engineers\n"
            "MUST NOT rewrite files that already satisfy the requirements as-is.\n"
            "Each deliverable must clearly distinguish:\n"
            "  [KEEP]   — already exists and is correct in the cloned repo\n"
            "  [MODIFY] — exists but must be changed\n"
            "  [ADD]    — does not exist and must be created\n"
            "  [DELETE] — exists but must be removed"
        )

    if strategy == "use_boilerplate":
        return (
            f"Strategy           : USE BOILERPLATE TEMPLATE\n"
            f"Boilerplate ID     : {inputs.get('boilerplate_id', 'N/A')}\n"
            f"Local Boilerplate  : {inputs.get('boilerplate_path', 'N/A')}\n\n"
            "This contract is a BOILERPLATE ADAPTATION CONTRACT. The named boilerplate\n"
            "provides the project skeleton. Engineers MUST call template_clone with\n"
            "the boilerplate_id before writing code. Each deliverable must clearly\n"
            "distinguish:\n"
            "  [BOILERPLATE] — provided out-of-the-box by the template (do not touch)\n"
            "  [CONFIGURE]   — boilerplate param substitution required\n"
            "  [ADD]         — new code that must be written on top of the boilerplate\n"
            "  [OVERRIDE]    — boilerplate file that must be replaced entirely"
        )

    # from_scratch
    return (
        "Strategy : FROM SCRATCH\n\n"
        "No close match was found on GitHub or in the boilerplate library.\n"
        "This is a full new-codebase contract. Engineers write all code from zero.\n"
        "No [KEEP], [MODIFY], or [BOILERPLATE] annotations are needed."
    )


def _fill_prompt(inputs: dict[str, Any]) -> str:
    today = date.today().isoformat()
    starting_point_block = _build_starting_point_block(inputs)
    return _CDD_MASTER_PROMPT.format(
        starting_point_block=starting_point_block,
        project_name=inputs["project_name"],
        project_description=inputs["project_description"],
        business_domain=inputs.get("business_domain", "Software"),
        contract_version=inputs.get("contract_version", "1.0.0"),
        contract_date=inputs.get("contract_date") or today,
        contract_owner_team=inputs.get("contract_owner_team", "Platform Team"),
        contract_owner_email=inputs.get("contract_owner_email", "platform@company.com"),
        contract_status=inputs.get("contract_status", "DRAFT"),
        architecture_description=inputs["architecture_description"],
        services=inputs.get("services", ""),
        primary_protocol=inputs.get("primary_protocol", "HTTP_REST"),
        async_broker=inputs.get("async_broker", "N/A"),
        deployment_platform=inputs.get("deployment_platform", "DOCKER_COMPOSE"),
        consumer_service_name=inputs["consumer_service_name"],
        consumer_team=inputs.get("consumer_team", "Frontend Team"),
        consumer_tech_stack=inputs.get("consumer_tech_stack", "TypeScript / React"),
        consumer_repo_url=inputs.get("consumer_repo_url", "N/A"),
        consumer_local_url=inputs.get("consumer_local_url", "http://localhost:3000"),
        provider_service_name=inputs["provider_service_name"],
        provider_team=inputs.get("provider_team", "Backend Team"),
        provider_tech_stack=inputs.get("provider_tech_stack", "Python / FastAPI"),
        provider_repo_url=inputs.get("provider_repo_url", "N/A"),
        provider_local_url=inputs.get("provider_local_url", "http://localhost:8000"),
        provider_ci_url=inputs.get("provider_ci_url", "http://localhost:8000"),
        provider_health_endpoint=inputs.get("provider_health_endpoint", "/health"),
        actuator_mappings_path=inputs.get("actuator_mappings_path", "N/A"),
        endpoints=inputs["endpoints"],
        core_data_models=inputs["core_data_models"],
        kafka_topics=inputs.get("kafka_topics", "N/A"),
        error_policy=inputs.get(
            "error_policy",
            "Standard error body: fields error(string), code(string), details(object|null). "
            "422 for validation failures (never 500 for bad input), "
            "404 for not found, 500 for provider fault. No stack traces in responses.",
        ),
        contract_repo_url=inputs.get("contract_repo_url", "N/A"),
        contract_folder_structure=inputs.get(
            "contract_folder_structure", "contracts/<service_name>/<version>/"
        ),
        linting_tool=inputs.get("linting_tool", "STOPLIGHT_SPECTRAL"),
        ci_cd_platform=inputs.get("ci_cd_platform", "GITHUB_ACTIONS"),
        test_data_examples=inputs.get("test_data_examples", "N/A"),
        contract_testing_tool=inputs.get("contract_testing_tool", "SPECMATIC"),
        specmatic_version=inputs.get("specmatic_version", "2.0.7"),
        test_framework=inputs.get("test_framework", "PYTEST"),
        build_tool=inputs.get("build_tool", "PIP"),
        authentication=inputs.get("authentication", "NONE"),
        rate_limiting=inputs.get("rate_limiting", "N/A"),
        pagination_style=inputs.get("pagination_style", "N/A"),
        custom_headers=inputs.get("custom_headers", "N/A"),
        additional_notes=inputs.get("additional_notes", "N/A"),
    )


def _extract_yaml_block(document: str, marker: str) -> str:
    """Extract the first fenced yaml block that follows a given header marker."""
    idx = document.find(marker)
    if idx == -1:
        return ""
    fence_start = document.find("```yaml", idx)
    if fence_start == -1:
        return ""
    content_start = document.find("\n", fence_start) + 1
    fence_end = document.find("```", content_start)
    if fence_end == -1:
        return ""
    return document[content_start:fence_end].strip()


class ContractorHandler(ToolHandler):
    """Generate a CDD contract document and persist it as a CDDContract artifact."""

    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if _factory is None:
            return {
                "artifact_id": "",
                "artifact_type": "CDDContract",
                "status": "error",
                "error": (
                    "Agent factory not configured. "
                    "The contractor tool requires the swarm runtime to inject "
                    "the agent factory via set_factory()."
                ),
            }

        filled_prompt = _fill_prompt(inputs)

        result = await _factory("contract_writer", filled_prompt)

        if not result.success or not result.output:
            return {
                "artifact_id": "",
                "artifact_type": "CDDContract",
                "status": "error",
                "error": result.error or "contract_writer agent produced no output",
            }

        document: str = result.output

        # Extract the OpenAPI YAML (Deliverable 3) and optional AsyncAPI / CI pipeline
        openapi_yaml = _extract_yaml_block(document, "DELIVERABLE 3")
        asyncapi_yaml = (
            _extract_yaml_block(document, "DELIVERABLE 4")
            if inputs.get("async_broker", "N/A").upper() != "N/A"
            else ""
        )
        ci_pipeline_yaml = _extract_yaml_block(document, "DELIVERABLE 9")

        # Persist as a CDDContract artifact
        from memory.artifacts import get_artifact_registry
        from memory.artifact_schemas.base import CDDContract, ArtifactType

        reg = get_artifact_registry()
        artifact = CDDContract(
            artifact_type=ArtifactType.cdd_contract,
            project_name=inputs["project_name"],
            contract_version=inputs.get("contract_version", "1.0.0"),
            contract_status=inputs.get("contract_status", "DRAFT"),
            consumer_service=inputs["consumer_service_name"],
            provider_service=inputs["provider_service_name"],
            primary_protocol=inputs.get("primary_protocol", "HTTP_REST"),
            async_broker=inputs.get("async_broker", "N/A"),
            full_document=document,
            openapi_yaml=openapi_yaml,
            asyncapi_yaml=asyncapi_yaml,
            ci_pipeline_yaml=ci_pipeline_yaml,
            stage_id=inputs.get("stage_id", "contracting"),
            lineage=inputs.get("source_artifact_ids", []),
        )

        artifact = await reg.create(artifact)
        artifact = await reg.approve(artifact.id)

        return {
            "artifact_id": artifact.id,
            "artifact_type": "CDDContract",
            "status": artifact.status if isinstance(artifact.status, str) else artifact.status.value,
            "contract_version": inputs.get("contract_version", "1.0.0"),
        }

    async def self_test(self) -> bool:
        return _factory is not None


handler = ContractorHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec
    handler.spec = load_tool_spec(_spec_path)
