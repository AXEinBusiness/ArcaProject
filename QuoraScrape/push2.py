import json
import os
import requests

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ibakdwmgqivmdrbminsc.supabase.co")

SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImliYWtkd21ncWl2bWRyYm1pbnNjIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzkzMjE1OSwiZXhwIjoyMDkzNTA4MTU5fQ.sE9nIL7lVPtjM4JUDpG1hKOmlnzPXKVRDZ_Jp2AkscY")

# ── Point this at your Quora scrape output ────────────────────────────────────
DEFAULT_FILE = "/Users/akshathlakshminarayana/Desktop/scrapereddit/quora_pain_points_20260505_150324.json"
def push_leads(file_path=DEFAULT_FILE):
    """Push leads from a Quora pain-points JSON file to Supabase."""
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        leads = json.load(f)

    if not leads:
        print("No leads to push.")
        return

    import uuid

    for lead in leads:
        username = lead.get("user_id", "Unknown")

        # Make the readable username visible in the 'source' column
        lead["source"] = f"quora (u/{username})"

        # Supabase requires a UUID for the 'user_id' column.
        # Generate a deterministic UUID from the username so it's never null.
        if "user_id" in lead:
            lead["user_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, username))

    print(f"Pushing {len(leads)} leads to Supabase...")

    url = f"{SUPABASE_URL}/rest/v1/leads"

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        response = requests.post(url, headers=headers, json=leads)
        if response.status_code in [200, 201]:
            print("✅ Successfully pushed leads to Supabase.")
        else:
            print(f"❌ Failed to push leads: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"❌ Error during push: {e}")

if __name__ == "__main__":
    if SUPABASE_KEY == "your-anon-key":
        print("⚠️ Please set SUPABASE_KEY environment variable first.")
        print("  export SUPABASE_KEY='your-anon-public-key'")
    else:
        push_leads()