import os

sa_json = os.environ.get("GOOGLEAPI")

sheet_id = os.environ.get("SHEET_ID")
sheet = client.open_by_key(sheet_id).worksheet("Jupiter")
