"""Text-only OpenAI accounting. No fallback prices, word counts or invented receipts."""
from dataclasses import dataclass
from hashlib import sha256
import os

from app.usage.processor import ProviderReceipt, ConfirmedTechnicalFailure
from app.usage.service import UsageError


@dataclass(frozen=True)
class Settings:
    model: str
    pricing_revision: str
    input_rate: int  # reviewed micro-USD per million tokens
    output_rate: int
    input_limit: int = 16384
    output_limit: int = 1024
    cached_input_rate: int | None = None

    def __post_init__(self):
        if (not self.model or not self.pricing_revision
                or any(type(v) is not int or not 0 < v <= 10**9
                       for v in (self.input_rate, self.output_rate))
                or not 1024 <= self.input_limit <= 100000
                or not 1 <= self.output_limit <= 16000):
            raise UsageError(503, "provider_accounting_not_configured")
        if self.cached_input_rate is not None and (type(self.cached_input_rate) is not int
                or not 0 <= self.cached_input_rate <= self.input_rate):
            raise UsageError(503, "provider_accounting_not_configured")

    @classmethod
    def from_environment(cls):
        # Gemini and other providers need reviewed usage/receipt adapters first.
        if os.getenv("LLM_PROVIDER") != "openai":
            raise UsageError(503, "metered_provider_not_supported")
        try:
            return cls(os.environ["LLM_MODEL"], os.environ["LLM_PRICING_REVISION"],
                int(os.environ["LLM_INPUT_MICRO_USD_PER_MILLION"]),
                int(os.environ["LLM_OUTPUT_MICRO_USD_PER_MILLION"]),
                cached_input_rate=int(os.environ["LLM_CACHED_INPUT_MICRO_USD_PER_MILLION"])
                    if os.getenv("LLM_CACHED_INPUT_MICRO_USD_PER_MILLION") else None)
        except (KeyError, ValueError):
            raise UsageError(503, "provider_accounting_not_configured") from None

    def cost(self, inputs, outputs, cached=0):
        if any(type(v) is not int or v < 0 for v in (inputs, outputs, cached)) or cached > inputs:
            raise UsageError(503, "provider_usage_unverified")
        if cached and self.cached_input_rate is None:
            raise UsageError(503, "cached_usage_price_unconfigured")
        return ((inputs-cached) * self.input_rate + cached * (self.cached_input_rate or 0)
                + outputs * self.output_rate + 999999) // 1000000

    @property
    def envelope(self):
        return self.cost(self.input_limit, self.output_limit)

    @property
    def context_revision(self):
        return "stateless-v1:" + sha256(repr(self).encode()).hexdigest()


def prompt_for(query, context):
    return ("أجب بالعربية من المصادر فقط. لا تخمّن. اذكر التعارض دون ترجيح.\n"
            f"المصادر:\n{context}\nالسؤال:\n{query}")


def generate(settings, query, context, attempt_id, *, client=None):
    prompt = prompt_for(query, context)
    # UTF-8 byte count upper-bounds text BPE tokens; reserve message overhead too.
    if len(prompt.encode("utf-8")) + 512 > settings.input_limit:
        raise ConfirmedTechnicalFailure()  # demonstrably before provider dispatch
    if client is None:
        from openai import OpenAI
        client = OpenAI(max_retries=0)  # no hidden paid retries outside U1
    response = client.chat.completions.create(model=settings.model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=settings.output_limit,
        extra_headers={"Idempotency-Key": attempt_id})
    usage = response.usage
    identity = response.id
    if not isinstance(identity, str) or not identity or usage is None:
        raise UsageError(503, "provider_usage_unverified")
    # SDK response is server-side evidence, not a request/model-supplied cost.
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    cost = settings.cost(usage.prompt_tokens, usage.completion_tokens, cached)
    answer = response.choices[0].message.content
    if not isinstance(answer, str) or not answer.strip():
        raise UsageError(503, "provider_response_unverified")
    receipt = ProviderReceipt("openai:" + sha256(identity.encode()).hexdigest(), cost,
        usage.prompt_tokens, usage.completion_tokens)
    return answer, receipt
