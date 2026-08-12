from google import genai

from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("GCP_API_KEY")

client = genai.Client(api_key=API_KEY)

response = client.models.generate_content(
    model='gemini-3.6-flash',
    contents='안녕하세요!',
)
print(response.text)