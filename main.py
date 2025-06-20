# Top of your file
import os

# Replace:
# sa_json = userdata.get("GoogleAPI")
# With:
sa_json = os.environ.get("GOOGLEAPI")

# Replace hardcoded sheet ID:
# client.open_by_key("1narNUimhteKfefjNHUjC3U6ObqXusFHe_aOg4x7xlHw")
# With:
sheet_id = os.environ.get("SHEET_ID")
sheet = client.open_by_key(sheet_id).worksheet("Jupiter")
