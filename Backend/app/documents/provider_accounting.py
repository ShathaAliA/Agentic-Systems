"""Text-only OpenAI-compatible accounting. No fallback prices, word counts or invented receipts."""

from dataclasses import dataclass
from hashlib import sha256
import os

from app.documents import guardrails
from app.usage.processor import ProviderReceipt, ConfirmedTechnicalFailure
from app.usage.service import UsageError


SUPPORTED_PROVIDERS = {"openai", "groq"}


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    pricing_revision: str
    input_rate: int
    output_rate: int
    input_limit: int = 16384
    output_limit: int = 1024
    cached_input_rate: int | None = None

    def __post_init__(self):
        if self.provider not in SUPPORTED_PROVIDERS:
            raise UsageError(503, "metered_provider_not_supported")

        if (
            not self.model
            or not self.pricing_revision
            or any(
                type(value) is not int or not 0 < value <= 10**9
                for value in (self.input_rate, self.output_rate)
            )
            or not 1024 <= self.input_limit <= 100000
            or not 1 <= self.output_limit <= 16000
        ):
            raise UsageError(503, "provider_accounting_not_configured")

        if self.cached_input_rate is not None and (
            type(self.cached_input_rate) is not int
            or not 0 <= self.cached_input_rate <= self.input_rate
        ):
            raise UsageError(503, "provider_accounting_not_configured")

    @classmethod
    def from_environment(cls):
        provider = (os.getenv("LLM_PROVIDER") or "").lower()

        if provider not in SUPPORTED_PROVIDERS:
            raise UsageError(503, "metered_provider_not_supported")

        try:
            return cls(
                provider=provider,
                model=os.environ["LLM_MODEL"],
                pricing_revision=os.environ["LLM_PRICING_REVISION"],
                input_rate=int(os.environ["LLM_INPUT_MICRO_USD_PER_MILLION"]),
                output_rate=int(os.environ["LLM_OUTPUT_MICRO_USD_PER_MILLION"]),
                cached_input_rate=(
                    int(os.environ["LLM_CACHED_INPUT_MICRO_USD_PER_MILLION"])
                    if os.getenv("LLM_CACHED_INPUT_MICRO_USD_PER_MILLION")
                    else None
                ),
            )
        except (KeyError, ValueError):
            raise UsageError(503, "provider_accounting_not_configured") from None

    def cost(self, inputs, outputs, cached=0):
        if any(
            type(value) is not int or value < 0
            for value in (inputs, outputs, cached)
        ) or cached > inputs:
            raise UsageError(503, "provider_usage_unverified")

        if cached and self.cached_input_rate is None:
            raise UsageError(503, "cached_usage_price_unconfigured")

        return (
            (inputs - cached) * self.input_rate
            + cached * (self.cached_input_rate or 0)
            + outputs * self.output_rate
            + 999999
        ) // 1000000

    @property
    def envelope(self):
        return self.cost(self.input_limit, self.output_limit)

    @property
    def context_revision(self):
        return "stateless-v1:" + sha256(repr(self).encode()).hexdigest()


def build_client(provider):
    """OpenAI-compatible client per provider."""
    from openai import OpenAI

    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise UsageError(503, "provider_credentials_missing")
        return OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
            max_retries=0,
        )

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise UsageError(503, "provider_credentials_missing")
        return OpenAI(api_key=api_key, max_retries=0)

    raise UsageError(503, "metered_provider_not_supported")


def generate(settings, query, context, attempt_id, *, client=None):
    prompt = guardrails.build_prompt(query, context)
    # UTF-8 byte count (system rules included) upper-bounds text BPE tokens; reserve message overhead too.
    if prompt.size + 512 > settings.input_limit:
        raise ConfirmedTechnicalFailure()  # demonstrably before provider dispatch

    if client is None:
        client = build_client(settings.provider)

    response = client.chat.completions.create(
        model=settings.model,
        messages=prompt.messages,
        max_completion_tokens=settings.output_limit,
        extra_headers={"Idempotency-Key": attempt_id},
    )

    usage = response.usage
    identity = response.id

    if not isinstance(identity, str) or not identity or usage is None:
        raise UsageError(503, "provider_usage_unverified")

    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0

    cost = settings.cost(
        usage.prompt_tokens,
        usage.completion_tokens,
        cached,
    )

    answer = response.choices[0].message.content

    if not isinstance(answer, str) or not answer.strip():
        raise UsageError(503, "provider_response_unverified")

    receipt = ProviderReceipt(
        key=f"{settings.provider}:{sha256(identity.encode()).hexdigest()}",
        cost_micro_usd=cost,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
    )

    # Leaks, markup and stray URLs never reach the user. The receipt is built first so the attempt's cost is
    # settled even when the text is blocked (a blocked answer is a counted question, never a silent refund).
    return guardrails.guard_output(answer, prompt), receipt