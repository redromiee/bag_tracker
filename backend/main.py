from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import os
import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# Serve Frontend
# We assume the frontend folder is in the same directory as the parent of backend, or we adjust path
# Current structure: bag_tracker/backend/main.py, bag_tracker/frontend/
# So frontend is at ../frontend
FRONTEND_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

app.mount("/static", StaticFiles(directory=FRONTEND_PATH), name="static")

class ScanData(BaseModel):
    bin_id: str
    bag_id: str
    scan_type: str  # "FWD" or "RTO"

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

@app.post("/record_scan")
def record_scan(data: ScanData):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Received scan: Type={data.scan_type}, Bin={data.bin_id}, Bag={data.bag_id}")
    
    try:
        # Define the scope
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # Load credentials - try environment variable first, then local file
        import json
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        
        if creds_json:
            # Production: use environment variable
            creds_dict = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            # Local development: use credentials.json file
            creds_path = os.path.join(os.path.dirname(__file__), "credentials.json")
            if not os.path.exists(creds_path):
                raise FileNotFoundError("credentials.json not found in backend folder and GOOGLE_CREDENTIALS_JSON env var not set")
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            
        client = gspread.authorize(creds)
        
        # Open the sheet - CHANGE THIS TO YOUR SHEET NAME
        sheet = client.open("Bag Tracker Data").sheet1
        
        # Append the row
        sheet.append_row([timestamp, data.scan_type, data.bin_id, data.bag_id, "Scanned"])
        
        return {"status": "success", "data": data}
    except Exception as e:
        print(f"Error saving to Sheets: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
