Here is a complete, step-by-step implementation plan to containerize and deploy **Munger** to **Google Cloud Run** in a secure demo mode.

---

### Step 1: Add Demo Mode Guardrails to the Application

In your FastAPI entry point (`main.py` or configuration module), support a `DEMO_MODE` environment variable to ensure demo users only interact with sanitized data:

```python
import os
from fastapi import FastAPI, HTTPException

DEMO_MODE = os.getenv("MUNGER_DEMO_MODE", "false").lower() in ("true", "1", "yes")

app = FastAPI(title="Munger (Demo)" if DEMO_MODE else "Munger")

# Example middleware / hook to intercept live syncs in demo mode
@app.middleware("http")
async def demo_mode_guard(request, call_next):
    if DEMO_MODE:
        # Block attempts to connect external credentials or mutate persistent storage
        if request.url.path in ("/api/sync/monarch", "/api/auth/google"):
            raise HTTPException(status_code=403, detail="External sync is disabled in Demo Mode.")
    return await call_next(request)

```

In your data loading logic (e.g. in `data/` or `core/`):

```python
def load_default_portfolio():
    if DEMO_MODE:
        # Always fallback to the bundled demo dataset
        return load_csv("demo_portfolio.csv")
    ...

```

---

### Step 2: Create Container Files

#### 1. `Dockerfile`

Place this in the repository root:

```dockerfile
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and demo data
COPY . .

# Run as non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8080

# Cloud Run injects $PORT (defaults to 8080)
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}

```

#### 2. `.dockerignore`

Ensure no local secrets, cache files, or git metadata are baked into the image:

```text
.git
.gitignore
.env
.env.*
*.pyc
__pycache__/
.pytest_cache/
tests/
venv/
.venv/
screenshots/

```

---

### Step 3: Build and Deploy via `gcloud`

Run these commands from your local repository root:

#### 1. Set Google Cloud Project and Region

```bash
PROJECT_ID="your-gcp-project-id"
REGION="us-west1" # or your preferred region

gcloud config set project $PROJECT_ID

```

#### 2. Enable Required Cloud APIs

```bash
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com

```

#### 3. Create an Artifact Registry Docker Repository

```bash
gcloud artifacts repositories create munger-repo \
    --repository-format=docker \
    --location=$REGION \
    --description="Docker repository for Munger demo"

```

#### 4. Build and Push Using Cloud Build

```bash
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/munger-repo/munger-demo:latest"

gcloud builds submit --tag $IMAGE_URI

```

#### 5. Deploy to Cloud Run

**For a Public Demo (evaluators can access directly via public HTTPS URL):**

```bash
gcloud run deploy munger-demo \
    --image=$IMAGE_URI \
    --region=$REGION \
    --platform=managed \
    --allow-unauthenticated \
    --set-env-vars="MUNGER_DEMO_MODE=true" \
    --memory=512Mi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=3 \
    --timeout=60s

```

**For an Authenticated / Private Evaluation (restricted to specific Google accounts):**

```bash
# 1. Deploy without public access
gcloud run deploy munger-demo \
    --image=$IMAGE_URI \
    --region=$REGION \
    --platform=managed \
    --no-allow-unauthenticated \
    --set-env-vars="MUNGER_DEMO_MODE=true" \
    --memory=512Mi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=3

# 2. Grant access only to specific reviewer email addresses:
gcloud run services add-iam-policy-binding munger-demo \
    --region=$REGION \
    --member="user:evaluator@example.com" \
    --role="roles/run.invoker"

```

---

### Step 4: Verification

1. After deployment completes, Cloud Run outputs the service URL:
```text
Service URL: https://munger-demo-xyz-uw.a.run.app

```


2. Navigate to the URL and verify:
* The dashboard loads with the demo portfolio data from `demo_portfolio.csv`.
* No sensitive local `.env` keys or live credentials are exposed.
* Sync buttons or credential update forms are disabled or rejected with HTTP 403.
* When idle, instances scale down to 0 so there is no ongoing compute cost.