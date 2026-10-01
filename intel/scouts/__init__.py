"""
Scouts: deterministic watchers (no LLM, no cost) that turn raw market/news/filing data into
structured FLAGS with a rule-based importance score. Only flags that clear the escalation
threshold ever wake the (costly) reasoning agent.

Design rule shared by every scout: if a source fails, report it as FAILED and emit no flags
from it. Never substitute another vendor, never invent a neutral value.
"""
