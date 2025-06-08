import json
import os
import logging
from typing import Dict, Any

import openai
import orchestrator.services.config as config

# Configure OpenAI
api_key = config.OPENAI_API_KEY  # set in config
logger = logging.getLogger(__name__)

# Prompt template for DSL generation
DSL_PROMPT = '''You are a workflow DSL generator.
Given the following problem statement, output ONLY valid JSON with the top-level key "steps",
where each step has "name", "agent", and "params".
Do not include any explanatory text.
Problem statement:
{problem}
'''  # noqa: E501

async def text_to_dsl(problem: str) -> Dict[str, Any]:
    """
    Convert a free-form problem statement into a workflow DSL dictionary
    by invoking the LLM.

    Args:
        problem: The natural-language problem description.

    Returns:
        A dict representing the workflow DSL.
    """
    try:
        client = openai.OpenAI(api_key=api_key)
        response = await client.chat.completions.acreate(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": "You generate workflow DSL."},
                {"role": "user", "content": DSL_PROMPT.format(problem=problem)}
            ],
            temperature=0.2,
        )
        dsl_text = response.choices[0].message.content
        dsl = json.loads(dsl_text)
        return dsl
    except Exception as exc:
        logger.exception("Failed to generate DSL from text")
        raise RuntimeError("DSL generation failed") from exc
