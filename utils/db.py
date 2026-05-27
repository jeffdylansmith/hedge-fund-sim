from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

def get_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    
    if not url or not key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in .env")
    
    return create_client(url, key)

supabase = get_client()