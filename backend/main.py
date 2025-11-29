from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
import os
import datetime
from datetime import timedelta
import pytz
import json
import bcrypt
import jwt
from functools import wraps

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from openpyxl import Workbook
from io import BytesIO

app = FastAPI()

# [SECTION: CONFIG]
# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRATION_DAYS = 7

# Serve Frontend
FRONTEND_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_PATH), name="static")

class ScanData(BaseModel):
    bin_id: str
    bag_id: str
    scan_type: str  # "FWD" or "RTO"

class UserRegister(BaseModel):
    username: str
    password: str
    name: str
    mobile: str
    email: str = ""  # Optional
    branch: str

class UserLogin(BaseModel):
    username: str
    password: str

class TokenData(BaseModel):
    token: str

class DownloadRequest(BaseModel):
    start_date: str  # Format: YYYY-MM-DD
    end_date: str    # Format: YYYY-MM-DD
    branch: str

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Server is running"}

@app.get("/login")
async def read_login():
    return FileResponse(os.path.join(FRONTEND_PATH, "login.html"))


# [SECTION: DATA]
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

def get_users_sheet():
    """Get the users sheet"""
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
    return client.open("Bag Tracker Users").sheet1

# Authentication Helper Functions
def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(username: str, name: str, branch: str) -> str:
    """Create a JWT token"""
    expiration = datetime.datetime.utcnow() + datetime.timedelta(days=TOKEN_EXPIRATION_DAYS)
    payload = {
        "username": username,
        "name": name,
        "branch": branch,
        "exp": expiration
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    """Verify and decode a JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

# --- Data Retention & Cleanup Functions ---

def cleanup_old_scan_data(dry_run: bool = True) -> dict:
    """
    Delete scan data older than 7 days from 'Bag Tracker Data' sheet
    
    Args:
        dry_run: If True, only report what would be deleted without actually deleting
    
    Returns:
        dict with status, count of deletions, and list of deleted records (if dry_run)
    """
    try:
        sheet = get_sheet()
        all_records = sheet.get_all_records()
        
        ist_timezone = pytz.timezone('Asia/Kolkata')
        now = datetime.datetime.now(ist_timezone)
        seven_days_ago = now - timedelta(days=7)
        
        rows_to_delete = []
        deleted_records = []
        
        for i, record in enumerate(all_records):
            timestamp_str = record.get('Timestamp', '')
            if not timestamp_str:
                continue
            
            try:
                # Parse timestamp (format: "2024-11-29 14:30:45")
                timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                timestamp = ist_timezone.localize(timestamp)
                
                if timestamp < seven_days_ago:
                    row_index = i + 2  # +2 because header is row 1, data starts at row 2
                    rows_to_delete.append(row_index)
                    deleted_records.append({
                        'row': row_index,
                        'timestamp': timestamp_str,
                        'bin_id': record.get('Bin ID'),
                        'bag_id': record.get('Bag ID'),
                        'scan_type': record.get('Scan Type')
                    })
            except ValueError:
                # Skip records with invalid timestamp format
                continue
        
        if not dry_run and rows_to_delete:
            # Delete rows in reverse order to maintain correct indices
            for row_index in reversed(rows_to_delete):
                sheet.delete_rows(row_index)
        
        return {
            "status": "success",
            "dry_run": dry_run,
            "deleted_count": len(rows_to_delete),
            "records": deleted_records if dry_run else []
        }
    
    except Exception as e:
        print(f"Cleanup scan data error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

def cleanup_inactive_users(dry_run: bool = True) -> dict:
    """
    Delete users who haven't logged in for more than 10 days
    Only deletes users with a Last Login date (keeps never-logged-in users)
    
    Args:
        dry_run: If True, only report what would be deleted without actually deleting
    
    Returns:
        dict with status, count of deletions, and list of deleted users (if dry_run)
    """
    try:
        users_sheet = get_users_sheet()
        all_users = users_sheet.get_all_records()
        
        ist_timezone = pytz.timezone('Asia/Kolkata')
        now = datetime.datetime.now(ist_timezone)
        ten_days_ago = now - timedelta(days=10)
        
        rows_to_delete = []
        deleted_users = []
        
        for i, user in enumerate(all_users):
            last_login_str = user.get('Last Login', '').strip()
            
            # Skip users who have never logged in (empty Last Login)
            if not last_login_str:
                continue
            
            try:
                # Parse last login (format: "2024-11-29 14:30:45")
                last_login = datetime.datetime.strptime(last_login_str, "%Y-%m-%d %H:%M:%S")
                last_login = ist_timezone.localize(last_login)
                
                if last_login < ten_days_ago:
                    row_index = i + 2  # +2 because header is row 1, data starts at row 2
                    rows_to_delete.append(row_index)
                    deleted_users.append({
                        'row': row_index,
                        'username': user.get('Username'),
                        'name': user.get('Name'),
                        'last_login': last_login_str,
                        'branch': user.get('Branch')
                    })
            except ValueError:
                # Skip users with invalid last login format
                continue
        
        if not dry_run and rows_to_delete:
            # Delete rows in reverse order to maintain correct indices
            for row_index in reversed(rows_to_delete):
                users_sheet.delete_rows(row_index)
        
        return {
            "status": "success",
            "dry_run": dry_run,
            "deleted_count": len(rows_to_delete),
            "users": deleted_users if dry_run else []
        }
    
    except Exception as e:
        print(f"Cleanup inactive users error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

def require_auth(func):
    """Decorator to require authentication"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # This is a simplified version - in production, extract token from headers
        dry_run: If True, only reports what would be deleted without deleting
    
    Returns:
        Summary of cleanup operations
    """
    try:
        # Verify secret key
        expected_key = os.getenv("CLEANUP_SECRET_KEY", "bagtracker2024")
        if request.secret_key != expected_key:
            return {"status": "error", "message": "Invalid secret key"}
        
        # Run cleanup functions
        scan_result = cleanup_old_scan_data(dry_run=request.dry_run)
        user_result = cleanup_inactive_users(dry_run=request.dry_run)
        
        return {
            "status": "success",
            "dry_run": request.dry_run,
            "scan_data_cleanup": scan_result,
            "inactive_users_cleanup": user_result,
            "summary": {
                "scans_deleted": scan_result.get("deleted_count", 0),
                "users_deleted": user_result.get("deleted_count", 0)
            }
        }
    
    except Exception as e:
        print(f"Cleanup endpoint error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

