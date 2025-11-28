from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import os
import datetime
import pytz
import json
import bcrypt
import jwt
from functools import wraps

import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

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

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

@app.get("/login")
async def read_login():
    return FileResponse(os.path.join(FRONTEND_PATH, "login.html"))


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

def require_auth(func):
    """Decorator to require authentication"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # This is a simplified version - in production, extract token from headers
        return await func(*args, **kwargs)
    return wrapper


# Authentication Endpoints
@app.post("/register")
def register(user: UserRegister):
    """Register a new user"""
    try:
        users_sheet = get_users_sheet()
        users = users_sheet.get_all_records()
        
        # Check if username already exists
        for existing_user in users:
            if existing_user.get('Username') == user.username:
                return {"status": "error", "message": "Username already exists"}
            
            # Check if mobile number already exists (one mobile = one account)
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
        
        # Add user to sheet
        users_sheet.append_row([
            user.username,
            password_hash,
            user.name,
            user.mobile,
            user.email,
            user.branch,
            created_at,
            ""  # Last login (empty for now)
        ])
        
        return {"status": "success", "message": "Registration successful"}
    
    except Exception as e:
        print(f"Registration error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/login")
def login(credentials: UserLogin):
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


