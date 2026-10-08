# Agentic FinOps Optimizer — Setup & Run Guide

## Prerequisites

- Python 3.9+
- Node.js 18+
- npm 9+

## 1. Clone & Enter the Project

```bash
cd /path/to/Agentic-FinOps-Optimizer
```

## 2. Backend Setup

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## 3. Frontend Setup

```bash
cd dashboard
npm install
cd ..
```

## 4. Configuration (Optional)

To connect to real AWS billing data, create a `.env` file in the project root:

```env
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=us-east-1
OPENAI_API_KEY=your_openai_key
```

> **Note:** Without AWS credentials the app runs in **demo mode** with realistic mock data.

## 5. Run the Application

### Start the API server (terminal 1):

```bash
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### Start the React dashboard (terminal 2):

```bash
cd dashboard
npm run dev
```

## 6. Access the Dashboard

Open your browser at: **http://localhost:3000**

(If port 3000 is in use, Vite will pick the next available port — check terminal output.)

## 7. Run Tests

```bash
source .venv/bin/activate
pytest
```

All 24 tests should pass.

## Project Structure

```
├── agents/          # LangGraph multi-agent orchestration
├── api/             # FastAPI backend + demo data
├── cloud/           # AWS cost fetchers & resource scanners
├── config/          # Settings & environment config
├── dashboard/       # React + Vite frontend
├── ml/              # Prophet forecasting, anomaly detection, clustering
├── db/              # SQLAlchemy models & DB layer
└── tests/           # Pytest test suite
```
