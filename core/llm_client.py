import os, json, time, logging, re
from typing import Any

logger = logging.getLogger(__name__)

class LLMClient:
    """
    Unified LLM interface with automatic failover.
    If the primary provider fails (rate limit, credits, etc.), it rotates to the next available one.
    """

    PROVIDER_DEFAULTS = {
        "groq":         {"model": "llama-3.3-70b-versatile",
                         "base_url": "https://api.groq.com/openai/v1",
                         "key_env": "GROQ_API_KEY"},
        "huggingface":  {"model": "Qwen/Qwen2.5-Coder-32B-Instruct",
                         "base_url": "https://router.huggingface.co/v1",
                         "key_env": "HF_API_KEY"},
        "openai":       {"model": "gpt-4o",
                         "base_url": "https://api.openai.com/v1",
                         "key_env": "OPENAI_API_KEY"},
        "gemini":       {"model": "gemini-1.5-pro",
                         "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
                         "key_env": "GEMINI_API_KEY"},
        "ollama":       {"model": "deepseek-coder-v2:16b",
                         "base_url": "http://127.0.0.1:11434/v1",
                         "key_env": None},
        "lmstudio":     {"model": "google/gemma-4-26b-a4b",
                         "base_url": "http://127.0.0.1:1234/v1",
                         "key_env": None},
    }

    def __init__(self, model: str = None, temperature: float = 0.2,
                 max_tokens: int = 4096, retry_attempts: int = 3):
        self.temperature = temperature
        self.max_tokens  = max_tokens
        self.retries     = retry_attempts
        self.override_model = model

        # Build a list of available providers based on environment keys
        self.providers = self._build_provider_pool()
        if not self.providers:
            raise EnvironmentError(
                "No LLM providers configured. "
                "For cloud: set GROQ_API_KEY, HF_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY. "
                "For local: set LLM_PROVIDER=lmstudio or LLM_PROVIDER=ollama."
            )
        
        self.current_provider_idx = 0
        self._setup_current_client()

    def _build_provider_pool(self):
        pool = []
        # Priority: Groq -> HF -> OpenAI -> Gemini -> Ollama -> LM Studio
        order = ["groq", "huggingface", "openai", "gemini", "ollama", "lmstudio"]
        
        # If LLM_PROVIDER is set, use ONLY that provider (don't mix local+cloud)
        preferred = os.getenv("LLM_PROVIDER", "").lower()
        if preferred in order:
            # Local providers: use exclusively, no fallback to cloud
            local_providers = {"ollama", "lmstudio"}
            if preferred in local_providers:
                order = [preferred]  # Only the selected local provider
            else:
                order.remove(preferred)
                order.insert(0, preferred)

        for p_name in order:
            config = self.PROVIDER_DEFAULTS[p_name]
            key_env = config["key_env"]
            
            if key_env is None or os.getenv(key_env):
                pool.append({
                    "name":     p_name,
                    "model":    self.override_model or os.getenv("LLM_MODEL") or config["model"],
                    "base_url": config["base_url"],
                    "api_key":  os.getenv(key_env) if key_env else "local"
                })
        return pool

    def _setup_current_client(self):
        from openai import OpenAI
        p = self.providers[self.current_provider_idx]
        self.model    = p["model"]
        self.base_url = p["base_url"]
        
        # Gemini and local providers handle their own logic in complete()
        if p["name"] not in ("gemini", "lmstudio"):
            self._client = OpenAI(api_key=p["api_key"], base_url=self.base_url)
        else:
            self._client = None
            
        logger.info(f"Using LLM Provider: {p['name'].upper()} (Model: {self.model})")

    def _rotate_provider(self):
        if len(self.providers) > 1:
            old_p = self.providers[self.current_provider_idx]["name"]
            self.current_provider_idx = (self.current_provider_idx + 1) % len(self.providers)
            new_p = self.providers[self.current_provider_idx]["name"]
            logger.warning(f"Failover: Switching provider from {old_p.upper()} to {new_p.upper()}")
            self._setup_current_client()
            return True
        return False

    # ── Public API ───────────────────────────────────────────────────────────

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        for attempt in range(self.retries * len(self.providers)):
            p = self.providers[self.current_provider_idx]
            try:
                # ── Custom LM Studio API v1 ──
                if p["name"] == "lmstudio":
                    import urllib.request
                    import json
                    
                    data = {
                        "model": self.model,
                        "input": user_prompt,
                        "system_prompt": system_prompt,
                        "temperature": self.temperature,
                        "max_output_tokens": self.max_tokens,
                        "stream": False
                    }
                    
                    req_url = "http://127.0.0.1:1234/api/v1/chat"
                    headers = {'Content-Type': 'application/json'}
                    lms_key = os.getenv("LMSTUDIO_API_KEY")
                    if lms_key:
                        headers['Authorization'] = f"Bearer {lms_key}"
                        
                    request = urllib.request.Request(
                        req_url, 
                        data=json.dumps(data).encode(), 
                        headers=headers
                    )
                    
                    with urllib.request.urlopen(request, timeout=300) as response:
                        resp_data = json.loads(response.read().decode())
                        output = resp_data.get("output", [])
                        message_content = "".join([item.get("content", "") for item in output if item.get("type") == "message"])
                        return message_content.strip()

                # ── Google Gemini API ──
                elif p["name"] == "gemini":
                    import urllib.request
                    import json
                    
                    api_key = os.getenv("GEMINI_API_KEY")
                    if not api_key:
                        raise EnvironmentError("GEMINI_API_KEY not set")
                        
                    data = {
                        "contents": [{
                            "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]
                        }],
                        "generationConfig": {
                            "temperature": self.temperature,
                            "maxOutputTokens": self.max_tokens,
                        }
                    }
                    
                    # Ensure model name doesn't have double prefix
                    model_path = self.model if self.model.startswith("models/") else f"{self.model}"
                    req_url = f"{p['base_url']}/{model_path}:generateContent?key={api_key}"
                    
                    request = urllib.request.Request(
                        req_url, 
                        data=json.dumps(data).encode(), 
                        headers={'Content-Type': 'application/json'}
                    )
                    
                    with urllib.request.urlopen(request, timeout=120) as response:
                        resp_data = json.loads(response.read().decode())
                        try:
                            return resp_data['candidates'][0]['content']['parts'][0]['text'].strip()
                        except (KeyError, IndexError):
                            if 'error' in resp_data:
                                raise RuntimeError(f"Gemini API Error: {resp_data['error'].get('message')}")
                            raise RuntimeError(f"Unexpected Gemini response format: {resp_data}")

                # ── Standard OpenAI Compatible Providers ──
                else:
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
                err_msg = str(e).lower()
                is_fatal = any(x in err_msg for x in ["402", "429", "rate limit", "credits", "decommissioned", "unauthorized", "api_key"])
                
                if is_fatal:
                    if self._rotate_provider():
                        continue
                
                if attempt == (self.retries * len(self.providers)) - 1:
                    raise
                
                wait = 2 ** (attempt % self.retries)
                logger.warning(f"LLM call failed (attempt {attempt+1}): {e}. Retrying in {wait}s...")
                time.sleep(wait)

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        for attempt in range(self.retries):
            raw = self.complete(system_prompt, user_prompt)
            clean = self._strip_fences(raw)
            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                try:
                    def escape_match(match):
                        s = match.group(0)
                        return s[0] + s[1:-1].replace("\n", "\\n").replace("\r", "\\r") + s[-1]
                    fixed = re.sub(r'"(?:[^"\\]|\\.)*"', escape_match, clean, flags=re.DOTALL)
                    return json.loads(fixed)
                except Exception as e:
                    if attempt == self.retries - 1:
                        raise RuntimeError(f"Invalid JSON after {self.retries} attempts.\nLast response:\n{raw}") from e
                    logger.warning(f"JSON parse failed (attempt {attempt+1}): {e}. Retrying...")
                    time.sleep(2 ** attempt)

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return text.strip()
