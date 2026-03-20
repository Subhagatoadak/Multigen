import json
import logging
from typing import Any, Dict

from openai import AsyncOpenAI

import orchestrator.services.config as config

logger = logging.getLogger(__name__)

# Prompt template for DSL generation
DSL_PROMPT = (
    "You are a workflow DSL generator.\n"
    "Given the following problem statement, output ONLY valid JSON with the "
    "top-level key \"steps\", where each step has \"name\", \"agent\", and \"params\".\n"
    "Do not include any explanatory text.\n"
    "Problem statement:\n{problem}"
)


async def text_to_dsl(problem: str) -> Dict[str, Any]:
    """
    Convert a free-form problem statement into a workflow DSL dictionary
    by invoking the configured LLM via the OpenAI AsyncClient.

    Raises RuntimeError on LLM or JSON-parse failure with the original
    exception chained for traceback inspection.
    """
    if not config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured — cannot generate DSL from text"
        )

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    try:
        response = await client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": "You generate workflow DSL."},
                {"role": "user", "content": DSL_PROMPT.format(problem=problem)},
            ],
            temperature=0.2,
        )
        dsl_text = response.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("LLM API call failed")
        raise RuntimeError(f"LLM API call failed: {exc}") from exc

    try:
        return json.loads(dsl_text)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned non-JSON: %s", dsl_text[:500])
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc
