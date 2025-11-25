import json
from loguru import logger
from app.config import Settings
from app.models.dto import RouterResult
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.gigachat_client import gigachat_chat


def extract_json(text: str) -> str:
    """
    Extract JSON object from text (may contain markdown or extra text).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")
    return text[start:end+1]


class LLMRouter:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def classify_text(self, text: str) -> RouterResult:
        """
        Classify user text using GigaChat API.
        Returns RouterResult with intent and extracted information.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

        try:
            resp = await gigachat_chat(messages, self.settings)
            raw = resp["choices"][0]["message"]["content"]
        except Exception:
            logger.exception("Error calling GigaChat")
            return RouterResult(intent="other", confidence="low")

        try:
            json_text = extract_json(raw)
            data = json.loads(json_text)
            return RouterResult(**data)
        except Exception:
            logger.exception(f"Error parsing router JSON: {raw!r}")
            return RouterResult(intent="other", confidence="low")
