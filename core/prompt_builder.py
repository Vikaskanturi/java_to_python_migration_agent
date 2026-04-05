from pathlib import Path

class PromptBuilder:
    def __init__(self, prompts_dir: str = "prompts"):
        self.dir = Path(prompts_dir)

    def build(self, template_name: str, variables: dict) -> str:
        """
        Load prompts/{template_name}.txt and fill {variable} placeholders.
        Raises KeyError if a required variable is missing from `variables`.
        """
        template = (self.dir / f"{template_name}.txt").read_text()
        # Find all {placeholders}
        import re
        required = set(re.findall(r'\{(\w+)\}', template))
        missing = required - set(variables.keys())
        if missing:
            raise KeyError(f"Missing prompt variables for '{template_name}': {missing}")
        return template.format(**variables)
