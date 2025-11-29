from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
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
import time
from collections import defaultdict

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

# Rate Limiting Configuration
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 5  # requests per window
rate_limit_store = defaultdict(list)

# Serve Frontend
FRONTEND_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_PATH), name="static")

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

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
# Global cache for GSpread client
cached_client = None
cached_client_time = 0
CACHE_DURATION = 3000  # Refresh token every 50 minutes (tokens last 60 mins)

def get_gspread_client():
    """Get or refresh cached GSpread client"""
    global cached_client, cached_client_time
    
    current_time = time.time()
    if cached_client and (current_time - cached_client_time < CACHE_DURATION):
        return cached_client

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
    cached_client = client
    cached_client_time = current_time
    print("Refreshed GSpread client connection")
    return client

def get_sheet():
    """Helper function to get authenticated sheet using cached client"""
    client = get_gspread_client()
    return client.open("Bag Tracker Data").sheet1

def get_users_sheet():
    """Get the users sheet using cached client"""
    client = get_gspread_client()
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

# Rate Limiter Decorator
def rate_limit(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        client_ip = request.client.host
        current_time = time.time()
        
        # Clean up old requests
        rate_limit_store[client_ip] = [t for t in rate_limit_store[client_ip] if current_time - t < RATE_LIMIT_WINDOW]
        
        if len(rate_limit_store[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
            return {"status": "error", "message": "Too many attempts. Please try again later."}
        
        rate_limit_store[client_ip].append(current_time)
        return await func(request, *args, **kwargs) # Pass request to function
    return wrapper

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
        return await func(*args, **kwargs)
    return wrapper


# [SECTION: AUTH]
@app.post("/register")
@rate_limit
async def register(request: Request, user: UserRegister):
    """Register a new user"""
    try:
        users_sheet = get_users_sheet()
        users = users_sheet.get_all_records()
        
        # Check if username already exists
        for existing_user in users:
            if existing_user.get('Username') == user.username:
                return {"status": "error", "message": "Username already exists"}
        
        # Check if mobile number already exists (one mobile = one account)
        for existing_user in users:
            if existing_user.get('Mobile') == user.mobile:
                return {"status": "error", "message": "Mobile number already registered"}
        
        # Validate required fields
        if not user.username or not user.password or not user.name or not user.mobile or not user.branch:
            return {"status": "error", "message": "All fields except email are required"}
        
        if len(user.password) < 6:
            return {"status": "error", "message": "Password must be at least 6 characters"}
        
        # Hash password
        password_hash = hash_password(user.password)
        
        # Get current timestamp
        ist_timezone = pytz.timezone('Asia/Kolkata')
        created_at = datetime.datetime.now(ist_timezone).strftime("%Y-%m-%d %H:%M:%S")
        
        # Add user to sheet (with empty Approval status - admin needs to approve)
        users_sheet.append_row([
            user.username,
            password_hash,
            user.name,
            user.mobile,
            user.email,
            user.branch,
            created_at,
            "",  # Last login (empty for now)
            ""   # Approval status (empty - needs admin approval)
        ])
        
        return {"status": "success", "message": "Registration successful"}
    
    except Exception as e:
        print(f"Registration error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/login")
@rate_limit
async def login(request: Request, credentials: UserLogin):
    """Login user and return JWT token"""
    try:
        users_sheet = get_users_sheet()
        users = users_sheet.get_all_records()
        
        # Find user
        user_row = None
        row_index = None
        for i, user in enumerate(users):
            if user.get('Username') == credentials.username:
                user_row = user
                row_index = i + 2  # +2 because header is row 1, first data is row 2
                break
        
        if not user_row:
            return {"status": "error", "message": "Invalid username or password"}
        
        # Verify password
        if not verify_password(credentials.password, user_row.get('Password Hash', '')):
            return {"status": "error", "message": "Invalid username or password"}
        
        # Check approval status
        approval_status = user_row.get('Approval', '').strip()
        if approval_status != 'Approved':
            return {
                "status": "error",
                "message": "Admin approval needed. Please contact administrator.",
                "error_code": "APPROVAL_REQUIRED"
            }
        
        # Update last login
        ist_timezone = pytz.timezone('Asia/Kolkata')
        last_login = datetime.datetime.now(ist_timezone).strftime("%Y-%m-%d %H:%M:%S")
        users_sheet.update_cell(row_index, 8, last_login)  # Column 8 is Last Login
        
        # Create token
        token = create_token(
            user_row.get('Username'),
            user_row.get('Name'),
            user_row.get('Branch')
        )
        
        return {
            "status": "success",
            "token": token,
            "user": {
                "username": user_row.get('Username'),
                "name": user_row.get('Name'),
                "branch": user_row.get('Branch'),
                "mobile": user_row.get('Mobile'),
                "email": user_row.get('Email')
            }
        }
    
    except Exception as e:
        print(f"Login error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/verify_token")
def verify_user_token(token_data: TokenData):
    """Verify JWT token and return user info"""
    payload = verify_token(token_data.token)
    if payload:
        return {"status": "success", "user": payload}
    else:
        return {"status": "error", "message": "Invalid or expired token"}

@app.post("/check_approval")
def check_approval(token_data: TokenData):
    """Check if user's approval status is still valid"""
    try:
        # Verify token first
        payload = verify_token(token_data.token)
        if not payload:
            return {"status": "error", "message": "Invalid or expired token", "approved": False}
        
        username = payload.get('username')
        if not username:
            return {"status": "error", "message": "Invalid token payload", "approved": False}
        
        # Get user from sheet
        users_sheet = get_users_sheet()
        users = users_sheet.get_all_records()
        
        # Find user
        user_row = None
        for user in users:
            if user.get('Username') == username:
                user_row = user
                break
        
        if not user_row:
            return {"status": "error", "message": "User not found", "approved": False}
        
        # Check approval status
        approval_status = user_row.get('Approval', '').strip()
        is_approved = approval_status == 'Approved'
        
        return {
            "status": "success",
            "approved": is_approved,
            "approval_status": approval_status if approval_status else "Pending"
        }

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

@app.post("/download_data")
def download_data(request: DownloadRequest):
    """Download scan data for a date range as Excel file"""
    try:
        # Parse dates
        start_date = datetime.datetime.strptime(request.start_date, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(request.end_date, "%Y-%m-%d").date()
        
        # Validate date range (max 7 days)
        date_diff = (end_date - start_date).days
        if date_diff < 0:
            return {"status": "error", "message": "Start date must be before or equal to end date"}
        if date_diff > 7:
            return {"status": "error", "message": "Date range cannot exceed 7 days"}
        
        # Fetch all records from Google Sheet
        sheet = get_sheet()
        all_records = sheet.get_all_records()
        
        # Filter by date range
        filtered_records = []
        for record in all_records:
            try:
                # Assuming Date column exists (format: YYYY-MM-DD)
                record_date_str = str(record.get('Date', ''))
                if not record_date_str:
                    continue
                    
                record_date = datetime.datetime.strptime(record_date_str, "%Y-%m-%d").date()
                
                if start_date <= record_date <= end_date:
                    filtered_records.append(record)
            except (ValueError, KeyError):
                # Skip records with invalid dates
                continue
        
        # Check if any data found
        if not filtered_records:
            return {"status": "error", "message": "No data found in the mentioned date range"}
        
        # Create Excel file
        wb = Workbook()
        ws = wb.active
        ws.title = "Scan Data"
        
        # Write headers
        if filtered_records:
            headers = list(filtered_records[0].keys())
            ws.append(headers)
            
            # Write data rows
            for record in filtered_records:
                row = [record.get(header, '') for header in headers]
                ws.append(row)
        
        # Format filename: DD-MM-YYYY-DD-MM-YYYY_BranchName.xlsx
        start_formatted = start_date.strftime("%d-%m-%Y")
        end_formatted = end_date.strftime("%d-%m-%Y")
        filename = f"{start_formatted}-{end_formatted}_{request.branch}.xlsx"
        
        # Save to BytesIO
        excel_file = BytesIO()
        wb.save(excel_file)
        excel_file.seek(0)
        
        # Return file as download
        return StreamingResponse(
            excel_file,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        print(f"Error generating download: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

# Data Retention & Cleanup Endpoint
class CleanupRequest(BaseModel):
    secret_key: str
    dry_run: bool = True  # Default to dry-run for safety

# [SECTION: ADMIN]
@app.post("/cleanup")
def cleanup_data(request: CleanupRequest):
    """
    Cleanup old data (7+ day scans and 10+ day inactive users)
    Requires secret key for authentication
    
    Args:
        secret_key: Must match CLEANUP_SECRET_KEY environment variable
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
