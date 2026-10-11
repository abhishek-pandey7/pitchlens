"""Content safety (§10A, T-76): fan input and generated output are screened before anything acts on them
or shows them.

Two layers:

- **Azure AI Content Safety** (when `CONTENT_SAFETY_ENDPOINT` is set): text analysis for hate, sexual,
  violence and self-harm content (four severity levels), and **Prompt Shields** for jailbreak attempts in
  fan input. Authenticated with `CONTENT_SAFETY_KEY` or, without one, managed identity / Entra ID.
- **A local screen**, always on: deterministic patterns for prompt injection, self-harm and violent
  threats against people. It is the offline fallback (tests, CI, keyless runs) and a cheap first pass.
  It is tuned for football talk, so "shoot", "attack the left side" and "kill the game off" pass.

Generated output is held to a stricter threshold (low severity and up) than fan input (medium and up):
fans may use strong language about a match, but we should not. If Azure is unreachable, the local
screen's verdict stands and the result says so. Blocked fan input gets a friendly message in the fan's
language.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_VERSION = "2024-09-01"
CATEGORIES = ["Hate", "SelfHarm", "Sexual", "Violence"]
SCOPE = "https://cognitiveservices.azure.com/.default"

BLOCKED_MESSAGE = {
    "en": "Let's keep it about the football: I can't help with that one.",
    "es": "Hablemos de fútbol: con eso no puedo ayudarte.",
    "fr": "Restons sur le football : je ne peux pas vous aider avec ça.",
    "pt": "Vamos falar de futebol: com isso eu não posso ajudar.",
    "ar": "لنبقَ في حديث كرة القدم: لا يمكنني المساعدة في ذلك.",
    "hi": "चलिए फ़ुटबॉल की बात करें: इसमें मैं मदद नहीं कर सकता।",
}

_PEOPLE = (
    r"(referee|ref|linesman|him|her|them|you|fans?|supporters?|players?|manager|coach|people|someone|"
    r"keeper|goalkeeper|striker|defender|everyone|anybody|anyone)"
)
LOCAL_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "PromptInjection",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.?!]{0,40}\b(instructions|rules|prompts?|guidelines|"
            r"system message)\b|\bsystem prompt\b|\b(you are now|pretend (to be|you are)|developer mode|"
            r"jailbreak)\b",
            re.IGNORECASE,
        ),
    ),
    ("SelfHarm", re.compile(r"\b(kill|hurt|harm|cut)\s+(myself|ourselves)\b|\bsuicid|\bself[- ]harm", re.I)),
    (
        "Violence",
        re.compile(
            rf"\b(kill|stab|murder|poison|beat up|assault|punch|hurt|harm)\s+(the\s+|that\s+|this\s+)?"
            rf"{_PEOPLE}\b",
            re.IGNORECASE,
        ),
    ),
]


@dataclass
class SafetyVerdict:
    allowed: bool
    categories: list[str] = field(default_factory=list)
    by: str = "local"  # local | azure | local (azure unavailable)
    ms: float = 0.0
    detail: str = ""

    def trace(self, stage: str) -> dict[str, Any]:
        return {
            "agent": "safety", "stage": stage, "allowed": self.allowed, "categories": self.categories,
            "by": self.by, "ms": round(self.ms), "error": self.detail or None,
        }  # fmt: skip


def local_screen(text: str) -> list[str]:
    """Categories the local patterns find in the text (empty if none)."""
    return [name for name, pattern in LOCAL_RULES if pattern.search(text or "")]


class ContentSafety:
    """Screens text with the local patterns, then Azure AI Content Safety when it is configured."""

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        key: str | None = None,
        input_threshold: int = 4,
        output_threshold: int = 2,
        timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/") or None
        self.key = key
        self.input_threshold, self.output_threshold = input_threshold, output_threshold
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)
        self._credential: Any = None

    @property
    def azure(self) -> bool:
        return self.endpoint is not None

    async def screen_input(self, text: str) -> SafetyVerdict:
        """Fan-supplied text: questions, nicknames. Also checked for jailbreak attempts."""
        return await self._screen(text, self.input_threshold, shield=True)

    async def screen_output(self, text: str) -> SafetyVerdict:
        """Text we generated, before a fan sees it (stricter threshold)."""
        return await self._screen(text, self.output_threshold, shield=False)

    async def _screen(self, text: str, threshold: int, *, shield: bool) -> SafetyVerdict:
        start = time.perf_counter()
        found = local_screen(text)
        if not shield:  # our own output can quote "instructions" harmlessly
            found = [c for c in found if c != "PromptInjection"]
        if found or not self.azure:
            return SafetyVerdict(not found, found, "local", (time.perf_counter() - start) * 1000)
        try:
            flagged = await self._analyze(text, threshold)
            if shield and await self._shield(text):
                flagged.append("PromptInjection")
        except (httpx.HTTPError, ValueError, KeyError) as exc:  # unreachable or unexpected: local stands
            log.warning("content safety unavailable: %s", exc)
            ms = (time.perf_counter() - start) * 1000
            return SafetyVerdict(True, [], "local (azure unavailable)", ms, str(exc)[:160])
        return SafetyVerdict(not flagged, flagged, "azure", (time.perf_counter() - start) * 1000)

    async def _headers(self) -> dict[str, str]:
        if self.key:
            return {"Ocp-Apim-Subscription-Key": self.key}
        if self._credential is None:
            from azure.identity.aio import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        token = await self._credential.get_token(SCOPE)
        return {"Authorization": f"Bearer {token.token}"}

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.endpoint}/contentsafety/{path}?api-version={API_VERSION}"
        response = await self._client.post(url, json=body, headers=await self._headers())
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    async def _analyze(self, text: str, threshold: int) -> list[str]:
        data = await self._post(
            "text:analyze",
            {"text": text[:10_000], "categories": CATEGORIES, "outputType": "FourSeverityLevels"},
        )
        return [c["category"] for c in data["categoriesAnalysis"] if int(c.get("severity") or 0) >= threshold]

    async def _shield(self, text: str) -> bool:
        data = await self._post("text:shieldPrompt", {"userPrompt": text[:10_000], "documents": []})
        return bool(data["userPromptAnalysis"]["attackDetected"])


@functools.cache
def default_safety() -> ContentSafety:
    """The process-wide checker from the environment (local only without an endpoint)."""
    return ContentSafety(
        os.environ.get("CONTENT_SAFETY_ENDPOINT") or None,
        key=os.environ.get("CONTENT_SAFETY_KEY") or None,
        input_threshold=int(os.environ.get("CONTENT_SAFETY_INPUT_THRESHOLD", "4")),
        output_threshold=int(os.environ.get("CONTENT_SAFETY_OUTPUT_THRESHOLD", "2")),
    )


def blocked_message(lang: str) -> str:
    return BLOCKED_MESSAGE.get(lang, BLOCKED_MESSAGE["en"])
