"""
P2P Subswarm — a bounded group of peer agents collaborating via a shared blackboard.

Three consensus protocols:
  - majority   : each agent votes; majority wins
  - weighted   : votes weighted by self-reported confidence
  - debate     : agents debate across rounds; referee declares resolution

After the subswarm dissolves, the blackboard snapshot is written to the trace.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal, Optional

from coordination.bus import MessageBus
from core.agent import Agent
from core.task import Task, TaskConstraints, TaskResult, TokenUsage
from memory.blackboard import Blackboard
from observability.logutil import get_logger

log = get_logger("coordination.subswarm")

ConsensusProtocol = Literal["majority", "weighted", "debate"]


class SubswarmCoordinator:
    def __init__(
        self,
        agents: list[Agent],
        bus: MessageBus,
        protocol: ConsensusProtocol = "majority",
        max_rounds: int = 3,
        swarm_id: Optional[str] = None,
    ) -> None:
        self.agents = agents
        self.bus = bus
        self.protocol = protocol
        self.max_rounds = max_rounds
        self.swarm_id = swarm_id or str(uuid.uuid4())[:8]
        self.blackboard = Blackboard(swarm_id=self.swarm_id)

    async def run(self, task: Task) -> TaskResult:
        log.info("subswarm_start", swarm=self.swarm_id, agents=len(self.agents),
                 protocol=self.protocol)

        # Wire write_blackboard tool for each agent
        try:
            import tools.write_blackboard.handler as wbb
            wbb.set_blackboard(self.blackboard)
        except ImportError:
            pass

        if self.protocol == "majority":
            result = await self._majority_vote(task)
        elif self.protocol == "weighted":
            result = await self._weighted_vote(task)
        else:
            result = await self._debate(task)

        # Persist blackboard snapshot to trace
        snapshot = self.blackboard.snapshot()
        log.info("subswarm_dissolved", swarm=self.swarm_id, entries=len(snapshot))

        return result

    async def _majority_vote(self, task: Task) -> TaskResult:
        """Each agent independently answers; most common answer wins."""
        agent_results = await self._run_all(task)
        votes: dict[str, list[str]] = {}
        for agent_id, res in agent_results:
            answer = str(res.output).strip()[:200]
            votes.setdefault(answer, []).append(agent_id)

        winner = max(votes, key=lambda k: len(votes[k]))
        total_cost = sum(r.cost for _, r in agent_results)
        total_usage = TokenUsage()
        for _, r in agent_results:
            total_usage = total_usage + r.token_usage

        log.info("majority_vote", winner_votes=len(votes[winner]), total_agents=len(self.agents))
        return TaskResult(
            output=winner,
            success=True,
            token_usage=total_usage,
            cost=total_cost,
            iterations=len(self.agents),
            metadata={"protocol": "majority", "vote_counts": {k: len(v) for k, v in votes.items()}},
        )

    async def _weighted_vote(self, task: Task) -> TaskResult:
        """Agents report confidence alongside answer; weighted sum decides."""
        weighted_task = task.fork(
            goal=task.goal + "\n\nAlso rate your confidence in the answer from 0.0 to 1.0."
                 "\nRespond in JSON: {\"answer\": \"...\", \"confidence\": 0.9}",
        )
        agent_results = await self._run_all(weighted_task)
        weighted: dict[str, float] = {}
        for _, res in agent_results:
            try:
                data = json.loads(str(res.output))
                ans = str(data.get("answer", "")).strip()[:200]
                conf = float(data.get("confidence", 0.5))
                weighted[ans] = weighted.get(ans, 0.0) + conf
            except Exception:
                ans = str(res.output).strip()[:200]
                weighted[ans] = weighted.get(ans, 0.0) + 0.5

        winner = max(weighted, key=lambda k: weighted[k])
        total_cost = sum(r.cost for _, r in agent_results)
        total_usage = TokenUsage()
        for _, r in agent_results:
            total_usage = total_usage + r.token_usage

        return TaskResult(
            output=winner,
            success=True,
            token_usage=total_usage,
            cost=total_cost,
            metadata={"protocol": "weighted", "scores": weighted},
        )

    async def _debate(self, task: Task) -> TaskResult:
        """Agents take turns contributing to a shared blackboard; referee resolves."""
        total_usage = TokenUsage()
        total_cost = 0.0

        for round_num in range(1, self.max_rounds + 1):
            log.debug("debate_round", round=round_num, swarm=self.swarm_id)
            for agent in self.agents[:-1]:  # last agent is referee
                bb_state = json.dumps(self.blackboard.latest(), indent=2)
                round_task = task.fork(
                    goal=(
                        f"Round {round_num} of a collaborative debate.\n"
                        f"Original goal: {task.goal}\n\n"
                        f"Blackboard so far:\n{bb_state}\n\n"
                        "Contribute your best argument or answer. Be concise."
                    ),
                )
                res = await agent.run_task(round_task)
                total_usage = total_usage + res.token_usage
                total_cost += res.cost
                await self.blackboard.write(
                    f"round{round_num}_{agent.spec.role}",
                    str(res.output),
                    metadata={"author_id": agent.id},
                )

        # Referee (last agent) declares resolution
        referee = self.agents[-1]
        bb_final = json.dumps(self.blackboard.latest(), indent=2)
        referee_task = task.fork(
            goal=(
                f"You are the referee. Here is the debate log:\n{bb_final}\n\n"
                f"Original goal: {task.goal}\n\n"
                "Synthesize the best answer from the debate and declare the final resolution."
            ),
        )
        final_res = await referee.run_task(referee_task)
        total_usage = total_usage + final_res.token_usage
        total_cost += final_res.cost

        return TaskResult(
            output=final_res.output,
            success=final_res.success,
            token_usage=total_usage,
            cost=total_cost,
            metadata={"protocol": "debate", "rounds": self.max_rounds},
        )

    async def _run_all(self, task: Task) -> list[tuple[str, TaskResult]]:
        import asyncio
        tasks_coro = [agent.run_task(task.fork(goal=task.goal)) for agent in self.agents]
        results = await asyncio.gather(*tasks_coro, return_exceptions=True)
        out = []
        for agent, res in zip(self.agents, results):
            if isinstance(res, Exception):
                out.append((agent.id, TaskResult(output=str(res), success=False)))
            else:
                out.append((agent.id, res))
        return out
