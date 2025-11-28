from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import os
import datetime
import pytz
import json

import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# Serve Frontend
FRONTEND_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_PATH), name="static")

class ScanData(BaseModel):
    bin_id: str
    bag_id: str
    scan_type: str  # "FWD" or "RTO"

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

def get_sheet():
    """Helper function to get authenticated sheet"""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds_path = os.path.join(os.path.dirname(__file__), "credentials.json")
        if not os.path.exists(creds_path):
            raise FileNotFoundError("credentials.json not found")
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    
    client = gspread.authorize(creds)
    return client.open("Bag Tracker Data").sheet1

@app.post("/record_scan")
def record_scan(data: ScanData):
    ist_timezone = pytz.timezone('Asia/Kolkata')
    timestamp = datetime.datetime.now(ist_timezone).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Received scan: Type={data.scan_type}, Bin={data.bin_id}, Bag={data.bag_id}")
    
    try:
        sheet = get_sheet()
        sheet.append_row([timestamp, data.scan_type, data.bin_id, data.bag_id, "Scanned"])
        return {"status": "success", "data": data}
    except Exception as e:
        print(f"Error saving to sheet: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/delete_scan")
def delete_scan(data: ScanData):
    print(f"Request to delete: Type={data.scan_type}, Bin={data.bin_id}, Bag={data.bag_id}")
    
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        
        # Debug: print first record to see column names
        if records:
            print(f"Available columns: {list(records[0].keys())}")
            print(f"First record: {records[0]}")
        
        # Find matching row (search backwards for most recent)
        row_to_delete = None
        for i in range(len(records) - 1, -1, -1):
            record = records[i]
            
            # Use exact column names from your sheet: 'Bin Name', 'Bag ID', 'Type'
            bin_name_value = str(record.get('Bin Name', ''))
            bag_id_value = str(record.get('Bag ID', ''))
            type_value = str(record.get('Type', ''))
            
            print(f"Checking row {i+2}: Bin={bin_name_value}, Bag={bag_id_value}, Type={type_value}")
            
            if (bin_name_value == str(data.bin_id) and 
                bag_id_value == str(data.bag_id) and 
                type_value == str(data.scan_type)):
                row_to_delete = i + 2  # +2 because header is row 1, first data is row 2
                print(f"Match found at row {row_to_delete}")
                break
        
        if row_to_delete:
            sheet.delete_rows(row_to_delete)
            print(f"Successfully deleted row {row_to_delete}")
            return {"status": "success", "message": "Scan deleted"}
        else:
            print(f"No matching record found for Bin={data.bin_id}, Bag={data.bag_id}, Type={data.scan_type}")
            return {"status": "error", "message": "Record not found"}
    
    except Exception as e:
        print(f"Error deleting from sheet: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


