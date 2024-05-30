import os
import re
import json
import logging
import time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from groq import Groq
from groq import APIError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

with open("config-groq.json", "r") as config_file:
    config = json.load(config_file)

GROQ_API_KEYS = [
    config["groq_api_key_1"],
    config["groq_api_key_2"],
    config["groq_api_key_3"]
]
REVISER_MODEL = config["reviser_model"]
WRITER_MODEL = config["writer_model"]
REVISER_SYSTEM_PROMPT = config["reviser_system_prompt"]

clients = [Groq(api_key=api_key) for api_key in GROQ_API_KEYS]

console = Console()


def get_client():
    client_index = get_client.counter % len(clients)
    get_client.counter += 1
    return clients[client_index]


get_client.counter = 0


def should_retry_rate_limit(exception):
    if isinstance(exception, RateLimitError):
        retry_after = exception.response.headers.get("Retry-After")
        if retry_after:
            logger.warning(
                f"Rate limit exceeded for the current API key. Retrying after {retry_after} seconds...")
            return True
    return False


@retry(stop=stop_after_attempt(len(GROQ_API_KEYS)), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(RateLimitError))
def llego_revise(article, article_logger, model):
    article_logger.info(
        f"Calling Llego to revise and expand the article using model: {model}")

    messages = [
        {
            "role": "system",
            "content": REVISER_SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": f"Article:\n{article}\n\n Your prompt here."
        }
    ]

    while True:
        try:
            llego_response = get_client().chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=8192,
                temperature=0.7,
            )

            response_text = llego_response.choices[0].message.content.strip()
            input_tokens = llego_response.usage.prompt_tokens
            output_tokens = llego_response.usage.completion_tokens

            article_logger.info(
                f"Reviser - Input Tokens: {input_tokens}, Output Tokens: {output_tokens}")
            article_logger.info(f"Revised article: {response_text}")

            console.print(Panel(
                response_text, title="[bold green]Revised and Expanded Article[/bold green]", title_align="left", border_style="green"))
            return response_text
        except RateLimitError as e:
            if "llama3-70b-8192" in str(e):
                article_logger.warning(
                    f"Rate limit exceeded for model {REVISER_MODEL}. Switching to model {WRITER_MODEL}.")
                return llego_revise(article, article_logger, WRITER_MODEL)
            else:
                retry_after = float(e.response.headers.get("Retry-After", 0))
                article_logger.warning(
                    f"Rate limit exceeded. Retrying after {retry_after} seconds...")
                time.sleep(retry_after)
        except APIError as e:
            article_logger.error(f"Error in Llego Reviser: {str(e)}")
            console.print(Panel(
                f"Error in Llego Reviser: {str(e)}", title="[bold red]Llego Reviser Error[/bold red]", title_align="left", border_style="red"))
            raise


def process_markdown_file(file_path):
    logger.info(f"Processing markdown file: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as file:
        article = file.read()

    sanitized_filename = re.sub(r'\W+', '_', os.path.basename(file_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    os.makedirs("log-groq", exist_ok=True)
    log_filename = f"log-groq/{timestamp}_{sanitized_filename}.txt"

    article_logger = logging.getLogger(f"article_{sanitized_filename}")
    article_logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_filename)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'))
    article_logger.addHandler(handler)

    try:
        revised_article = llego_revise(article, article_logger, REVISER_MODEL)
    except APIError as e:
        article_logger.error(
            f"Skipping file: {file_path} due to Reviser error after retries.")
        console.print(Panel(
            f"Skipping file: {file_path} due to Reviser error after retries.",
            title="[bold yellow]File Skipped[/bold yellow]",
            title_align="left",
            border_style="yellow"
        ))
        return

    if revised_article is None:
        article_logger.warning(
            f"Skipping file: {file_path} due to rate limit exceeded for all API keys.")
        console.print(Panel(
            f"Skipping file: {
                file_path} due to rate limit exceeded for all API keys.",
            title="[bold yellow]File Skipped[/bold yellow]",
            title_align="left",
            border_style="yellow"
        ))
        return

    os.makedirs("groq-reviser", exist_ok=True)
    output_filename = f"groq-reviser/{timestamp}_{sanitized_filename}.md"

    article_logger.info(f"Revised and Expanded Article:\n{revised_article}")

    try:
        with open(output_filename, 'w', encoding='utf-8') as file:
            file.write("## Old Article\n\n")
            file.write(article)
            file.write("\n\n")
            file.write("---\n")
            file.write("***\n")
            file.write("___\n")
            file.write("\n")
            file.write("## Revised Article\n\n")
            file.write(revised_article)
        article_logger.info(f"Revised article saved to {output_filename}")
    except IOError as e:
        article_logger.error(f"Error saving revised article: {str(e)}")
        console.print(Panel(
            f"Error saving revised article: {str(e)}",
            title="[bold red]Article Save Error[/bold red]",
            title_align="left",
            border_style="red"
        ))


def main():
    article_folder = "groq-article"

    markdown_files = [file for file in os.listdir(
        article_folder) if file.endswith(".md") or file.endswith(".markdown")]

    for markdown_file in markdown_files:
        file_path = os.path.join(article_folder, markdown_file)
        process_markdown_file(file_path)


if __name__ == "__main__":
    main()
