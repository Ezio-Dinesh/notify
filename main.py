import os
import json
import uuid
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict
from dotenv import load_dotenv
from scraper import GSTNoticesDownloader

load_dotenv()

# ---- Environment variables ----
API_KEY_2CAPTCHA = os.getenv("API_KEY_2CAPTCHA")
if not API_KEY_2CAPTCHA:
    raise ValueError("❌ Missing API_KEY_2CAPTCHA in .env file.")

API_KEY = os.getenv("P-Key")
if not API_KEY:
    raise ValueError("❌ Missing P-Key in .env file.")

MAX_WORKERS = int(os.getenv("MAX_WORKERS", 4))

# ---- FastAPI app ----
app = FastAPI(title="GST Notice Scraper API")

# ---- API Key security ----
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key or api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API Key")
    return api_key

# ---- In-memory job store ----
jobs: Dict[str, dict] = {}

# ---- Concurrency ----
semaphore = asyncio.Semaphore(MAX_WORKERS)
verify_semaphore = asyncio.Semaphore(2)  # separate pool for /verify

# ---- Pydantic models ----
class ScrapeRequest(BaseModel):
    username: str
    password: str
    target_date: Optional[str] = None

class VerifyRequest(BaseModel):
    username: str
    password: str

class ScrapeResponse(BaseModel):
    job_id: str
    status: str
    message: str

class VerifyResponse(BaseModel):
    valid: bool
    message: str

# ---- Background scraper ----
async def run_scraper(job_id: str, username: str, password: str, target_date: Optional[str]):
    try:
        await semaphore.acquire()
        jobs[job_id]["status"] = "running"

        def scrape_sync():
            downloader = GSTNoticesDownloader(
                username=username,
                password=password,
                api_key=API_KEY_2CAPTCHA,
                target_date=target_date,
                job_id=job_id
            )
            return downloader.run_and_collect()

        headers, rows, file_paths = await asyncio.to_thread(scrape_sync)

        download_urls = []
        for fp in file_paths:
            filename = os.path.basename(fp)
            download_urls.append(f"/download/{job_id}/{filename}")

        jobs[job_id].update({
            "status": "completed",
            "headers": headers,
            "data": rows,
            "total_records": len(rows),
            "download_urls": download_urls,
            "message": "Scraping completed successfully."
        })
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["message"] = str(e)
    finally:
        semaphore.release()

# ---- /verify endpoint ----
async def verify_login_async(username: str, password: str) -> dict:
    await verify_semaphore.acquire()
    try:
        def verify_sync():
            downloader = GSTNoticesDownloader(
                username=username,
                password=password,
                api_key=API_KEY_2CAPTCHA,
                target_date=None,
                job_id=None
            )
            return downloader.verify_login()
        valid, message = await asyncio.to_thread(verify_sync)
        return {"valid": valid, "message": message}
    finally:
        verify_semaphore.release()

@app.post("/verify", response_model=VerifyResponse, dependencies=[Depends(verify_api_key)])
async def verify_credentials(request: VerifyRequest):
    result = await verify_login_async(request.username, request.password)
    return VerifyResponse(valid=result["valid"], message=result["message"])

# ---- /scrape endpoint ----
@app.post("/scrape", response_model=ScrapeResponse, dependencies=[Depends(verify_api_key)])
async def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "message": "Job queued.",
        "username": request.username,
        "target_date": request.target_date
    }
    background_tasks.add_task(run_scraper, job_id, request.username, request.password, request.target_date)
    return ScrapeResponse(job_id=job_id, status="pending", message="Scraping started.")

# ---- /status endpoint ----
@app.get("/status/{job_id}", dependencies=[Depends(verify_api_key)])
async def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    response = {
        "job_id": job_id,
        "status": job["status"],
        "message": job.get("message", ""),
        "username": job.get("username"),
        "target_date": job.get("target_date")
    }
    if job["status"] == "completed":
        response.update({
            "headers": job.get("headers", []),
            "data": job.get("data", []),
            "total_records": job.get("total_records", 0),
            "download_urls": job.get("download_urls", [])
        })
    return JSONResponse(content=response)

# ---- /download endpoint ----
@app.get("/download/{job_id}/{filename}", dependencies=[Depends(verify_api_key)])
async def download_file(job_id: str, filename: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    base_dir = os.path.join("data", job_id, "downloaded_files")
    file_path = os.path.join(base_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(file_path, media_type="application/pdf", filename=filename)

# ---- Health check (no API key required) ----
@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)