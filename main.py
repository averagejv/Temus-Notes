import os
import time
from urllib.parse import urlparse
from flask import Flask, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from firecrawl import FirecrawlApp
from langchain_community.llms import OpenAI
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
import openai
from openai.error import RateLimitError, AuthenticationError
#from openai import OpenAI
from openai import AuthenticationError
# Load environment variables
load_dotenv()

# Get Firecrawl API key from .env
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
if not FIRECRAWL_API_KEY:
    raise ValueError("Missing FIRECRAWL_API_KEY in environment variables")

# Initialize Firecrawl (OpenAI key will be user-provided at runtime)
firecrawl = FirecrawlApp(api_key=FIRECRAWL_API_KEY)

# Initialize Flask app
app = Flask(__name__)
limiter = Limiter(get_remote_address,
                  app=app,
                  default_limits=["10 per minute"])
cache = {}  # In-memory cache


# Validate job URL format
def is_valid_url(url):
    parsed = urlparse(url)
    return bool(parsed.scheme in ["http", "https"] and parsed.netloc)


# Limit to known job listing sites
def is_job_site_url(url):
    job_sites = [
        "linkedin.com", "indeed.com", "glassdoor.com", "monster.com",
        "mycareersfuture.gov.sg", "myskillsfuture.com", "weworkremotely.com",
        "remoteok.com"
    ]
    return any(site in url for site in job_sites)


# Validate OpenAI API key with a test call
#import openai
def is_valid_openai_key(api_key):
    openai.api_key = api_key
    try:
        openai.Model.list()
        return True
    except openai.error.AuthenticationError:
        return False


# Retry logic for rate limits
def try_summarize_with_retries(chain, job_text, retries=3, base_delay=5):
    for i in range(retries):
        try:
            return chain.run(job_content=job_text)
        except RateLimitError:
            delay = base_delay * (2**i)
            print(f"⚠️ Rate limit hit. Retrying in {delay} seconds...")
            time.sleep(delay)
    raise Exception("OpenAI rate limit exceeded after retries.")


# Main route
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        job_url = request.form.get("job_url", "").strip()
        user_api_key = request.form.get("user_api_key", "").strip()

        if not is_valid_url(job_url):
            return render_template("index.html", error="Invalid URL format.")
        if not is_job_site_url(job_url):
            return render_template("index.html",
                                   error="Only job listing sites are allowed.")
        if not user_api_key or not user_api_key.startswith("sk-"):
            return render_template(
                "index.html", error="Please provide a valid OpenAI API key.")
        if not is_valid_openai_key(user_api_key):
            return render_template("index.html",
                                   error="Invalid OpenAI API key.")

        if job_url in cache:
            summary = cache[job_url]
        else:
            try:
                content = firecrawl.scrape_url(url=job_url,
                                               formats=["markdown"])
                job_text = content.markdown

                if not job_text or len(job_text.strip()) < 50:
                    return render_template(
                        "index.html",
                        error=
                        "Could not extract meaningful content — this site may block bots. Try a different URL."
                    )

                prompt = PromptTemplate(input_variables=["job_content"],
                                        template="""
                        You are a helpful assistant. Summarize this job listing page clearly with key positions, companies, locations, and requirements, and help the user choose the best one:
                        {job_content}
                    """)

                llm = OpenAI(temperature=0.7, openai_api_key=user_api_key)
                chain = LLMChain(llm=llm, prompt=prompt)

                summary = try_summarize_with_retries(chain, job_text)
                cache[job_url] = summary

            except Exception as e:
                return render_template("index.html", error=f"Error: {str(e)}")

        return render_template("index.html", summary=summary)

    return render_template("index.html")


# Run the app
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

