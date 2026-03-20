"""Prompt Framework integration with versioned prompt management and switchable templates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class Prompt:  # type: ignore
        def __init__(self, name: str, template: str, version: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
            self.name = name
            self.template = template
            self.version = version or "default"
            self.metadata = metadata or {}

class PromptRegistry:  # type: ignore
        def __init__(self) -> None:
            self._store: Dict[str, List[Prompt]] = {}

        def register(self, prompt: Prompt) -> None:
            self._store.setdefault(prompt.name, [])
            self._store[prompt.name] = [p for p in self._store[prompt.name] if p.version != prompt.version]
            self._store[prompt.name].append(prompt)

        def get(self, name: str, version: Optional[str] = None) -> Prompt | None:
            prompts = self._store.get(name, [])
            if version:
                for p in prompts:
                    if p.version == version:
                        return p
                return None
            return prompts[-1] if prompts else None

        def list(self, name: str) -> List[Prompt]:
            return list(self._store.get(name, []))


@dataclass
class PromptManager:
    """Versioned prompt manager backed by Prompt Framework (with in-memory shim fallback)."""

    registry: Any = None

    def __post_init__(self) -> None:
        self.registry = PromptRegistry()

    def add(self, name: str, template: str, *, version: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        prompt = Prompt(name=name, template=template, version=version, metadata=metadata or {})
        self.registry.register(prompt)

    def get(self, name: str, *, version: Optional[str] = None) -> str:
        prompt = self.registry.get(name=name, version=version)
        return prompt.template if prompt else ""

    def list_versions(self, name: str) -> Dict[str, Any]:
        return {p.version: p.template for p in self.registry.list(name)}


class Prompt_Framework:
    """Switchable prompt engineering frameworks with shared context/style."""

    def __init__(self, context: str, output_type: str | None = None, style: str | None = None, role: str | None = None, *args, **kwargs) -> None:
        self.context = context
        self.output_type = output_type
        self.style = style
        self.role = role
        self.args = args
        self.kwargs = kwargs
        self.framework = None

    def switch_framework(self, framework: str) -> None:
        frameworks = {
            "costar": self.costar_framework,
            "care": self.care_framework,
            "race": self.race_framework,
            "ape": self.ape_framework,
            "create": self.create_framework,
            "tag": self.tag_framework,
            "creo": self.creo_framework,
            "rise": self.rise_framework,
            "pain": self.pain_framework,
            "coast": self.coast_framework,
            "roses": self.roses_framework,
            "react": self.react_framework,
        }
        key = framework.lower()
        if key not in frameworks:
            raise ValueError(f"Invalid framework: {framework}. Choose from {list(frameworks)}")
        self.framework = frameworks[key]

    def generate_prompt(self, *args, **kwargs) -> str:
        if self.framework is None:
            raise ValueError("No framework selected. Call switch_framework first.")
        return self.framework(*args, **kwargs)

    def _hdr(self) -> str:
        prompt = f"Context: {self.context}\n"
        if self.output_type:
            prompt += f"Output Type: {self.output_type}\n"
        if self.style:
            prompt += f"Style: {self.style}\n"
        if self.role:
            prompt += f"Role: {self.role}\n"
        return prompt

    def costar_framework(self, reasoning: str, *args, **kwargs) -> str:
        prompt = self._hdr()
        prompt += f"Reasoning: {reasoning}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def care_framework(self, action: str, result: str, example: str, *args, **kwargs) -> str:
        prompt = self._hdr()
        prompt += f"Action: {action}\nExpected Result: {result}\nExample: {example}\n"
        prompt += "Task: Generate a response based on the above information."
        return prompt

    def race_framework(self, role: str, action: str, explanation: str, *args, **kwargs) -> str:
        prompt = f"Role: {role}\nAction: {action}\nContext: {self.context}\nExplanation: {explanation}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def ape_framework(self, action: str, purpose: str, execution: str, *args, **kwargs) -> str:
        prompt = f"Action: {action}\nPurpose: {purpose}\nExecution: {execution}\n"
        prompt += "Task: Generate a response based on the above information."
        return prompt

    def create_framework(self, character: str, request: str, examples: str, adjustment: str, output_type: str, *args, **kwargs) -> str:
        prompt = (
            f"Character: {character}\n"
            f"Request: {request}\n"
            f"Examples: {examples}\n"
            f"Adjustment: {adjustment}\n"
            f"Type of Output: {output_type}\n"
        )
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def tag_framework(self, task: str, action: str, goal: str, *args, **kwargs) -> str:
        prompt = f"Task: {task}\nAction: {action}\nGoal: {goal}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def creo_framework(self, context: str, request: str, explanation: str, outcome: str, *args, **kwargs) -> str:
        prompt = f"Context: {self.context}\nRequest: {request}\nExplanation: {explanation}\nOutcome: {outcome}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def rise_framework(self, role: str, input_: str, steps: str, execution: str, *args, **kwargs) -> str:
        prompt = f"Role: {role}\nInput: {input_}\nSteps: {steps}\nExecution: {execution}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def pain_framework(self, problem: str, action: str, information: str, next_steps: str, *args, **kwargs) -> str:
        prompt = f"Problem: {problem}\nAction: {action}\nInformation: {information}\nNext Steps: {next_steps}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def coast_framework(self, context: str, objective: str, actions: str, scenario: str, task: str, *args, **kwargs) -> str:
        prompt = (
            f"Context: {context}\n"
            f"Objective: {objective}\n"
            f"Actions: {actions}\n"
            f"Scenario: {scenario}\n"
            f"Task: {task}\n"
        )
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def roses_framework(self, role: str, objective: str, scenario: str, expected_solution: str, steps: str, *args, **kwargs) -> str:
        prompt = (
            f"Role: {role}\n"
            f"Objective: {objective}\n"
            f"Scenario: {scenario}\n"
            f"Expected Solution: {expected_solution}\n"
            f"Steps: {steps}\n"
        )
        prompt += "Task: Generate a response based on the above parameters."
        return prompt

    def react_framework(self, context: str, task: str, explanation: str, *args, **kwargs) -> str:
        prompt = f"Context: {context}\nTask: {task}\nExplanation: {explanation}\n"
        prompt += "Task: Generate a response based on the above parameters."
        return prompt


__all__ = ["PromptManager", "Prompt_Framework"]
