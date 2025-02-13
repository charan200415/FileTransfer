from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from typing import List, Dict
import os
import shutil
import uuid
from collections import defaultdict
import secrets
from config import DOCS_USERNAME, DOCS_PASSWORD
# Initialize security for docs only
security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials for docs only"""
    is_username_correct = secrets.compare_digest(credentials.username, DOCS_USERNAME)
    is_password_correct = secrets.compare_digest(credentials.password, DOCS_PASSWORD)
    
    if not (is_username_correct and is_password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

# Initialize FastAPI with auth for docs only
app = FastAPI(
    title="File Sharing API",
    description="API for file sharing service",
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url=None  # Disable default redoc
)

# Create protected docs routes
@app.get("/docs", include_in_schema=False)
async def get_documentation(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json", title="API Documentation")

@app.get("/redoc", include_in_schema=False)
async def get_redoc(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    from fastapi.openapi.docs import get_redoc_html
    return get_redoc_html(openapi_url="/openapi.json", title="API Documentation")

@app.get("/openapi.json", include_in_schema=False)
async def get_openapi(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title="File Sharing API",
        version="1.0.0",
        description="API for file sharing service",
        routes=app.routes
    )
    return openapi_schema

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# Store file mappings with user info: {unique_code: {"filename": filename, "user_id": user_id}}
file_codes: Dict[str, dict] = {}
# Reverse mapping: {filename: {"code": code, "user_id": user_id}}
filename_codes: Dict[str, dict] = {}

# Add this class after other class definitions
class UserStats:
    def __init__(self):
        self.upload_counts = defaultdict(int)
        self.download_counts = defaultdict(int)
        self.total_bytes_uploaded = defaultdict(int)
        self.total_bytes_downloaded = defaultdict(int)
        self.last_activity = defaultdict(str)

    def log_upload(self, user_id: str, file_size: int, filename: str):
        self.upload_counts[user_id] += 1
        self.total_bytes_uploaded[user_id] += file_size
        self.last_activity[user_id] = f"Uploaded: {filename}"

    def log_download(self, user_id: str, file_size: int, filename: str):
        self.download_counts[user_id] += 1
        self.total_bytes_downloaded[user_id] += file_size
        self.last_activity[user_id] = f"Downloaded: {filename}"

    def get_user_stats(self, user_id: str) -> dict:
        return {
            "uploads": self.upload_counts[user_id],
            "downloads": self.download_counts[user_id],
            "bytes_uploaded": self.total_bytes_uploaded[user_id],
            "bytes_downloaded": self.total_bytes_downloaded[user_id],
            "last_activity": self.last_activity.get(user_id, "No activity")
        }

# Initialize stats
user_stats = UserStats()

@app.post("/upload/")
async def upload_file(file: UploadFile = File(...), user_id: str = None):
    try:
        # Generate unique code
        unique_code = str(uuid.uuid4())[:8]
        
        # Save the uploaded file
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Store the mapping with user info
        file_codes[unique_code] = {"filename": file.filename, "user_id": user_id}
        filename_codes[file.filename] = {"code": unique_code, "user_id": user_id}
        
        # Log the upload
        file_size = os.path.getsize(file_path)
        user_stats.log_upload(user_id, file_size, file.filename)
        
        return {
            "filename": file.filename,
            "access_code": unique_code,
            "message": "File uploaded successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload-multiple/")
async def upload_multiple_files(files: List[UploadFile] = File(...)):
    try:
        uploaded_files = []
        for file in files:
            # Generate unique code for each file
            unique_code = str(uuid.uuid4())[:8]
            
            file_path = os.path.join(UPLOAD_DIR, file.filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Store the mapping
            file_codes[unique_code] = {"filename": file.filename, "user_id": None}
            filename_codes[file.filename] = {"code": unique_code, "user_id": None}
            
            uploaded_files.append({
                "filename": file.filename,
                "access_code": unique_code
            })
        return {"files": uploaded_files, "message": "Files uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/files/{user_id}")
async def list_files(user_id: str):
    """List files for a specific user"""
    try:
        files = []
        for filename in os.listdir(UPLOAD_DIR):
            file_info = filename_codes.get(filename, {})
            if file_info.get("user_id") == user_id:
                files.append({
                    "filename": filename,
                    "access_code": file_info["code"]
                })
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{access_code}")
async def download_file(access_code: str):
    try:
        if access_code not in file_codes:
            raise HTTPException(status_code=404, detail="Invalid access code")
        
        filename = file_codes[access_code]["filename"]
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        if os.path.exists(file_path):
            return FileResponse(file_path, filename=filename)
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete/{access_code}")
async def delete_file(access_code: str, user_id: str = None):
    try:
        if access_code not in file_codes:
            raise HTTPException(status_code=404, detail="Invalid access code")
        
        # Check if user owns the file
        if user_id and file_codes[access_code]["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't have permission to delete this file")
        
        filename = file_codes[access_code]["filename"]
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        if os.path.exists(file_path):
            os.remove(file_path)
            # Remove from both mappings
            del filename_codes[filename]
            del file_codes[access_code]
            return {"message": f"File {filename} deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/{access_code}")
async def direct_download(access_code: str):
    """Direct download route using just the access code in the URL"""
    try:
        if access_code not in file_codes:
            # If not a valid access code, return 404 or redirect to home
            raise HTTPException(status_code=404, detail="Invalid access code")
        
        filename = file_codes[access_code]["filename"]
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        if os.path.exists(file_path):
            # Determine content disposition based on file type
            content_disposition = "inline"  # For viewing in browser
            # For certain file types, force download
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.pdf', '.txt')):
                content_disposition = "attachment"
            
            return FileResponse(
                file_path, 
                filename=filename,
                headers={"Content-Disposition": f"{content_disposition}; filename={filename}"}
            )
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Add new endpoint for stats
@app.get("/stats/{user_id}")
async def get_user_stats(user_id: str):
    try:
        stats = user_stats.get_user_stats(user_id)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/log_download")
async def log_download(user_id: str, file_size: int, filename: str):
    try:
        user_stats.log_download(user_id, file_size, filename)
        return {"message": "Download logged successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860) 
    #uvicorn.run(app, host="127.0.0.1", port=7860) 
