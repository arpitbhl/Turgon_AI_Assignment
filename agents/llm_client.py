import logging
import os
import time
from openai import OpenAI

logger = logging.getLogger("LLMClient")


class LLMClient:
    """Connects to LM Studio's local OpenAI-compatible server running Qwen models."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model_name: str = "qwen/qwen3.5-9b",
    ):
        self.base_url = os.getenv("LM_STUDIO_BASE_URL", base_url)
        self.model_name = os.getenv("LM_STUDIO_MODEL", model_name)
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"),
        )
        self.total_tokens_consumed = 0
        self.total_calls = 0

    def chat_completion(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.0
    ) -> str:
        """Sends a request to local LM Studio server running qwen/qwen3.5-9b."""
        self.total_calls += 1
        start_time = time.time()

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )

            if response.usage:
                self.total_tokens_consumed += (
                    response.usage.total_tokens or 0
                )

            duration = round(time.time() - start_time, 2)
            logger.info(
                f"LM Studio [{self.model_name}] completed in {duration}s"
            )
            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(
                f"Failed to communicate with LM Studio at {self.base_url}: {e}"
            )
            logger.warning(
                "Ensure LM Studio Local Server is STARTED and 'qwen/qwen3.5-9b' is loaded."
            )
            raise e