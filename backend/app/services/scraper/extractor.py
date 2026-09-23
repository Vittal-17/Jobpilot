from bs4 import BeautifulSoup
import re

class ExtractionError(Exception):
    pass

def extract_job_description(html_content: str) -> str:
    if not html_content:
        raise ExtractionError("Empty HTML content")

    soup = BeautifulSoup(html_content, 'html.parser')

    # Remove script, style, meta, noscript
    for element in soup(["script", "style", "meta", "noscript", "header", "footer", "nav"]):
        element.decompose()

    text = soup.get_text(separator='\n')

    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)

    if len(text) < 200:
        raise ExtractionError("Extracted text too short, possibly SPA, CAPTCHA, or secondary snippet")

    # Optional: Basic anti-bot checks (Cloudflare, etc)
    lower_text = text.lower()
    if "please enable cookies" in lower_text or "checking your browser" in lower_text or "enable javascript" in lower_text or "just a moment..." in lower_text:
        raise ExtractionError("Anti-bot page detected")

    return text
