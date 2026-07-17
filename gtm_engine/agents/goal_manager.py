import datetime
import logging
from typing import List, Optional
from pydantic import BaseModel, Field

log = logging.getLogger("gtm.agents.goal_manager")


class Goal(BaseModel):
    objective: str = Field(..., description="The main business objective (e.g. Schedule demo with VP Sales)")
    success_criteria: str = Field(..., description="Condition for considering the goal accomplished")
    constraints: List[str] = Field(default_factory=list, description="Rules or restrictions for outreach (e.g. No weekends)")
    budget: float = Field(0.50, description="LLM spend limit for the campaign run (USD)")
    deadline: Optional[str] = Field(None, description="ISO format deadline timestamp")
    priority: int = Field(1, description="Priority rating (1 to 5, where 1 is highest)")


class BudgetManager:
    """Tracks and regulates LLM token usage and spend limit policies."""
    def __init__(self, limit: float = 0.50):
        self.limit = limit
        self.spend = 0.0

    def record_spend(self, amount: float) -> None:
        self.spend += amount
        log.info("BudgetManager: recorded spend of $%f. Total campaign spend: $%f (limit: $%f)", amount, self.spend, self.limit)

    def is_exhausted(self) -> bool:
        return self.spend >= self.limit

    def select_model(self, task: str) -> str:
        """
        Cost-aware model routing policy.
        - Research: Cheap Model (e.g. gemini-2.5-flash)
        - Evaluation: Medium Model (e.g. gemini-2.5-flash or similar)
        - Strategy/Planning: Best Model (e.g. gemini-2.5-pro)
        """
        if self.is_exhausted():
            # Force cheapest model as a budget safety mechanism
            log.warning("BudgetManager: campaign budget exhausted! Routing task %r to cheapest fallback model.", task)
            return "gemini-2.5-flash"

        # Adaptive routing based on task criticality
        if task in ("research", "intelligence"):
            return "gemini-2.5-flash"
        elif task in ("evaluation", "spam_check"):
            return "gemini-2.5-flash"
        else: # planning, strategy copy generation
            return "gemini-2.5-pro"


# Global budget manager instance
budget_manager = BudgetManager()


# ── Goal Manager Graph Node ────────────────────────────────────────────────

def goal_manager_node(state: dict) -> dict:
    """
    Goal Manager LangGraph node.
    Initializes a structured Goal from the objective string, evaluates
    constraints, and initializes budget policies.
    """
    objective_str = state.get("objective", "Generate outreach campaign")
    
    # Simple parsing/construction of default structured goal
    # In a full production loop, this would run an LLM prompt to extract these fields.
    deadline_val = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)).isoformat()
    
    goal = Goal(
        objective=objective_str,
        success_criteria="meeting_booked event captured",
        constraints=["No weekends", "Max 3 follow-up attempts", "Gmail delivery only"],
        budget=0.50,
        deadline=deadline_val,
        priority=2
    )
    
    # Initialize the budget limit
    global budget_manager
    budget_manager.limit = goal.budget
    
    log.info("GoalManager: structured Goal initialized. Objective: %r, Budget: $%f", goal.objective, goal.budget)
    
    return {
        "plan": {
            "goal": goal.model_dump() if hasattr(goal, "model_dump") else goal.dict(),
            "status": "active",
            "initialized_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    }
