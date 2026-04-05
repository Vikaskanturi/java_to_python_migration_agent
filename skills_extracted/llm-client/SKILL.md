---
name: llm-client
description: >
  Use this skill whenever any agent in the Java→Python migration pipeline needs
  to call an LLM. This skill defines the ONLY way LLMs are called in the
  project — through core/llm_client.py. Triggers whenever implementing or
  modifying LLM calls, adding a new provider (Groq, HuggingFace, Ollama,
  OpenAI), handling retries, or parsing JSON responses from an LLM. No agent
  code should ever import an LLM SDK directly — always route through LLMClient.
---

# LLM Client Skill

Single unified interface for all LLM calls in the pipeline.
**Rule: Every LLM call in every agent goes through this class. No exceptions.**

## Supported Providers
| Provider | `LLM_PROVIDER` value | Free tier | Best model for code |
|----------|----------------------|-----------|---------------------|
| Groq | `groq` | Yes — fast | `llama3-70b-8192` |
| HuggingFace | `huggingface` | Yes | `Qwen/Qwen2.5-Coder-32B-Instruct` |
| Ollama | `ollama` | Local/offline | `deepseek-coder-v2:16b` |
| OpenAI-compat | `openai` | Paid / local | `gpt-4o-mini` or any local |

---

## Implementation: `core/llm_client.py`

```python
import os, json, time, logging
from typing import Any

logger = logging.getLogger(__name__)

class LLMClient:
    """
    Unified LLM interface. Reads LLM_PROVIDER from environment.
    All agents import and use ONLY this class for LLM calls.
    """

    PROVIDER_DEFAULTS = {
        "groq":         {"model": "llama3-70b-8192",
                         "base_url": "https://api.groq.com/openai/v1"},
        "huggingface":  {"model": "Qwen/Qwen2.5-Coder-32B-Instruct",
                         "base_url": "https://api-inference.huggingface.co/v1"},
        "ollama":       {"model": "deepseek-coder-v2:16b",
                         "base_url": "http://localhost:11434/v1"},
        "openai":       {"model": "gpt-4o-mini",
                         "base_url": "https://api.openai.com/v1"},
    }

    def __init__(self, model: str = None, temperature: float = 0.2,
                 max_tokens: int = 4096, retry_attempts: int = 3):
        self.provider    = os.getenv("LLM_PROVIDER", "groq").lower()
        self.temperature = temperature
        self.max_tokens  = max_tokens
        self.retries     = retry_attempts

        defaults = self.PROVIDER_DEFAULTS.get(self.provider, {})
        self.model    = model or os.getenv("LLM_MODEL") or defaults.get("model")
        self.base_url = os.getenv("LLM_BASE_URL") or defaults.get("base_url")

        self._client = self._build_client()

    def _build_client(self):
        """Return an openai-compatible client for the selected provider."""
        from openai import OpenAI
        api_key = self._get_api_key()
        return OpenAI(api_key=api_key, base_url=self.base_url)

    def _get_api_key(self) -> str:
        KEY_MAP = {
            "groq":        "GROQ_API_KEY",
            "huggingface": "HF_API_KEY",
            "openai":      "OPENAI_API_KEY",
            "ollama":      None,   # no key needed
        }
        env_var = KEY_MAP.get(self.provider)
        if env_var is None:
            return "ollama"   # Ollama accepts any non-empty string
        key = os.getenv(env_var)
        if not key:
            raise EnvironmentError(
                f"Provider '{self.provider}' requires {env_var} env variable.\n"
                f"Set it with: export {env_var}=your_key_here"
            )
        return key

    # ── Public API ───────────────────────────────────────────────────────────

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a chat completion request. Returns the response as a plain string.
        Retries up to self.retries times with exponential backoff.
        """
        for attempt in range(self.retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ]
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                if attempt == self.retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(f"LLM call failed (attempt {attempt+1}): {e}. Retrying in {wait}s...")
                time.sleep(wait)

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """
        Like complete(), but parses the response as JSON.
        Strips markdown fences if the model wraps the JSON in ```json ... ```.
        Retries up to self.retries times if response is not valid JSON.
        """
        for attempt in range(self.retries):
            try:
                raw = self.complete(system_prompt, user_prompt)
                clean = self._strip_fences(raw)
                return json.loads(clean)
            except json.JSONDecodeError as e:
                if attempt == self.retries - 1:
                    raise RuntimeError(
                        f"LLM returned invalid JSON after {self.retries} attempts.\n"
                        f"Last response:\n{raw}"
                    ) from e
                logger.warning(f"JSON parse failed (attempt {attempt+1}): {e}. Retrying...")
                time.sleep(2 ** attempt)

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove ```json ... ``` or ``` ... ``` wrappers from LLM output."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]   # remove first line (```json)
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]  # remove trailing fence
        return text.strip()
```

---

## How Agents Use LLMClient

```python
# In any agent — this is the ONLY import pattern allowed
from core.llm_client import LLMClient

class MigrationAgent:
    def __init__(self, manifest: dict, llm: LLMClient, output_dir: str):
        self.llm = llm   # injected by orchestrator

    def _convert_file(self, java_code: str, context: str) -> str:
        system = self.prompt_builder.build("migration", {...})
        return self.llm.complete(system, java_code)   # ← only call needed

    def _validate_file(self, java: str, python: str) -> dict:
        system = self.prompt_builder.build("validation", {...})
        return self.llm.complete_json(system, java + "\n\n" + python)
```

---

## Environment Setup

### Groq (fastest, recommended)
```bash
export LLM_PROVIDER=groq
export GROQ_API_KEY=gsk_xxxx   # from console.groq.com
```

### HuggingFace
```bash
export LLM_PROVIDER=huggingface
export HF_API_KEY=hf_xxxx      # from huggingface.co/settings/tokens
```

### Ollama (local, no key)
```bash
ollama pull deepseek-coder-v2:16b
export LLM_PROVIDER=ollama
# OLLAMA_BASE_URL defaults to http://localhost:11434/v1
```

### Override model at runtime
```bash
export LLM_MODEL=mixtral-8x7b-32768   # overrides provider default
# or via CLI:
python run.py --repo <url> --model mixtral-8x7b-32768
```

---

## config.yaml Mapping
```yaml
llm:
  provider: groq             # reads LLM_PROVIDER env if not set
  model: llama3-70b-8192    # reads LLM_MODEL env if not set
  temperature: 0.2
  max_tokens: 4096
  retry_attempts: 3
  retry_backoff_seconds: 2
```

## Instantiation in Orchestrator
```python
from core.llm_client import LLMClient
import yaml

cfg = yaml.safe_load(open("config.yaml"))["llm"]
llm = LLMClient(
    model=args.model or cfg.get("model"),
    temperature=cfg["temperature"],
    max_tokens=cfg["max_tokens"],
    retry_attempts=cfg["retry_attempts"],
)
# Pass `llm` into every agent constructor — never create a second instance
```

---

## Error Reference
| Error | Cause | Fix |
|-------|-------|-----|
| `EnvironmentError` | Missing API key env var | Set the correct `*_API_KEY` env var |
| `RuntimeError: invalid JSON` | LLM returned prose instead of JSON | Prompt already instructs JSON-only; increase `retry_attempts` |
| `openai.APIConnectionError` | Ollama not running | Run `ollama serve` first |
| `openai.RateLimitError` | Groq/HF rate limit hit | Exponential backoff handles this automatically |
