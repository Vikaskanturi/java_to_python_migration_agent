#!/usr/bin/env python3
"""
Java → Python AI Migration Suite
Usage: python run.py --repo https://github.com/user/repo [options]
"""
import click
import yaml
import os
from pathlib import Path
from dotenv import load_dotenv
from orchestrator import Orchestrator

# Load environment variables from .env if it exists
load_dotenv()

@click.command()
@click.option("--repo",       required=True,  help="GitHub repo URL to migrate")
@click.option("--output-dir", default="projects", show_default=True, help="Output directory")
@click.option("--agent",      default="all",
              type=click.Choice(["all","migration","validation","tests","docs"]),
              help="Run only one agent (default: all)")
@click.option("--model",      default=None,   help="Override LLM model name")
@click.option("--branch",     default=None,   help="Git branch to clone")
@click.option("--verbose",    is_flag=True,   help="Print LLM prompts and responses")
@click.option("--config",     default="config.yaml", help="Path to config file")
@click.option("--groq-key",   help="GROQ API Key")
@click.option("--hf-key",     help="HuggingFace API Key")
@click.option("--openai-key", help="OpenAI API Key")
def main(repo, output_dir, agent, model, branch, verbose, config, groq_key, hf_key, openai_key):
    # Inject keys into environment for LLMClient to pick up
    if groq_key:   os.environ["GROQ_API_KEY"] = groq_key
    if hf_key:     os.environ["HF_API_KEY"]   = hf_key
    if openai_key: os.environ["OPENAI_API_KEY"] = openai_key

    cfg = _load_config(config)
    if model:
        cfg["llm"]["model"] = model

    agents_to_run = (
        ["migration","validation","tests","docs"] if agent == "all" else [agent]
    )

    orchestrator = Orchestrator(
        repo_url     = repo,
        output_dir   = output_dir,
        agents       = agents_to_run,
        llm_config   = cfg["llm"],
        branch       = branch,
        verbose      = verbose,
    )
    orchestrator.run()

def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {"llm": {"provider": "groq", "temperature": 0.2, "max_tokens": 4096, "retry_attempts": 3}}
    return yaml.safe_load(path.read_text())

if __name__ == "__main__":
    main()
