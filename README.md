# Agentic FinOps / Cloud Cost Optimizer

An AI-powered multi-agent system that monitors cloud billing & usage telemetry, detects anomalies, and autonomously proposes (and optionally executes) cost-saving actions with human-in-the-loop approval gates.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Supervisor Agent                        │
│  (LangGraph orchestrator – Observe/Diagnose/Recommend)   │
└───────┬──────────┬──────────┬──────────┬────────────────┘
        │          │          │          │
   ┌────▼───┐ ┌───▼────┐ ┌──▼───┐ ┌───▼─────┐
   │Observer│ │Diagnosr│ │Recom │ │Executor │
   │ Agent  │ │ Agent  │ │Agent │ │ Agent   │
   └────┬───┘ └───┬────┘ └──┬───┘ └───┬─────┘
        │          │          │          │
   AWS Cost   Anomaly     GenAI      Terraform/
   Explorer   Detection   Reports    CloudFormation
   + CW       (Prophet)              (with approval)
```

## Key Features

- **Observe:** Continuous ingestion of cloud billing data (AWS Cost Explorer, GCP BigQuery Billing Export)
- **Diagnose:** Time-series anomaly detection (Prophet, IsolationForest) on spend patterns per service/tag
- **Recommend:** LLM-grounded natural-language optimization reports with $ savings estimates
- **Act:** Terraform/IaC execution for approved changes (right-sizing, RI purchases, idle resource cleanup)
- **Human-in-the-Loop:** Slack/email approval gates before any destructive action
- **"Why did my bill spike?"** — Conversational Q&A grounded in Cost Explorer data

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Orchestration | LangGraph (StateGraph with supervisor) |
| LLM | AWS Bedrock (Claude) / Azure OpenAI |
| Anomaly Detection | Prophet, scikit-learn (IsolationForest), statsmodels |
| Forecasting | Prophet / NeuralProphet for monthly bill forecasting |
| Cloud APIs | boto3 (Cost Explorer, CloudWatch, EC2, RDS), google-cloud-billing |
| IaC Execution | python-terraform, Pulumi Automation API |
| Streaming | AWS EventBridge → SQS for real-time cost alerts |
| Storage | PostgreSQL (cost history), Redis (agent state), S3 (reports) |
| Observability | LangSmith for agent traces, Prometheus + Grafana for metrics |
| Deployment | Docker + AWS ECS Fargate / Lambda for ingestion |
| Approval UI | Streamlit dashboard + Slack bot |

## Project Structure

```
├── agents/               # LangGraph agent definitions
│   ├── supervisor.py     # Top-level orchestrator (StateGraph)
│   ├── observer.py       # Billing data ingestion agent
│   ├── diagnoser.py      # Anomaly detection & root-cause agent
│   ├── recommender.py    # LLM-powered recommendation agent
│   ├── executor.py       # IaC execution agent (with approval gate)
│   ├── conversational.py # Natural-language Q&A agent
│   └── state.py          # Shared TypedDict state definition
├── analytics/            # Data science / ML modules
│   ├── anomaly.py        # IsolationForest + Prophet anomaly detection
│   ├── forecast.py       # Spend forecasting (Prophet/NeuralProphet)
│   └── clustering.py     # Workload cost-profile clustering (KMeans)
├── cloud/                # Cloud provider integrations
│   ├── aws/
│   │   ├── cost_explorer.py   # Cost & Usage API + Rightsizing
│   │   ├── cloudwatch.py      # EC2 CPU/memory utilization
│   │   └── ec2_advisor.py     # Idle/oversized/unattached resource finder
│   └── gcp/
│       └── billing.py         # BigQuery billing export
├── iac/                  # Infrastructure-as-Code
│   ├── actions.py        # Terraform execution wrapper (sandboxed)
│   └── terraform/
│       ├── rightsize_ec2.tf   # Instance resize template
│       ├── cleanup_idle.tf    # Terminate/delete idle resources
│       └── deploy_ecs.tf      # ECS Fargate deployment for the app
├── api/                  # FastAPI backend
│   ├── main.py           # Routes: /analyze, /ask, /approve, /forecast
│   ├── schemas.py        # Pydantic request/response models
│   └── models.py         # SQLAlchemy ORM + DB session management
├── dashboard/            # Streamlit UI
│   └── app.py            # Ask, Analysis, Forecast, Approvals, History tabs
├── notifications/        # Alert handlers
│   ├── slack.py          # Slack webhook + interactive approval
│   └── email.py          # SMTP email alerts & weekly reports
├── alembic/              # Database migrations
│   ├── env.py
│   └── versions/
├── config/
│   └── settings.py       # Pydantic-settings config
├── tests/                # Comprehensive test suite
│   ├── conftest.py       # Shared fixtures
│   ├── test_anomaly.py
│   ├── test_forecast.py
│   ├── test_clustering.py
│   ├── test_agents.py
│   ├── test_cloud.py
│   └── test_api.py
├── scheduler.py          # Cron runner for automated daily/weekly cycles
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── pytest.ini
├── requirements.txt
└── README.md
```

## Getting Started

```bash
# 1. Clone and setup
git clone <repo-url>
cd Agentic-FinOps-Optimizer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure cloud credentials
cp .env.example .env
# Edit .env with AWS/GCP credentials and LLM API keys

# 3. Start services
docker-compose up -d  # PostgreSQL, Redis

# 4. Run database migrations
alembic upgrade head

# 5. Start the API server
uvicorn api.main:app --reload

# 6. Launch dashboard (separate terminal)
streamlit run dashboard/app.py

# 7. Run a one-off analysis
python -c "import asyncio; from agents.supervisor import run_finops_cycle; print(asyncio.run(run_finops_cycle('Full scan')))"

# 8. Run scheduled analysis (or configure via cron)
python scheduler.py
```

## Running Tests

```bash
pytest --cov=agents --cov=analytics --cov=api -v
```

## Cloud Deployment

The project includes a full Terraform module (`iac/terraform/deploy_ecs.tf`) for AWS ECS Fargate:

```bash
cd iac/terraform
terraform init
terraform plan -var="vpc_id=vpc-xxx" -var='subnet_ids=["subnet-xxx"]'
terraform apply
```

Additional deployment options:
- **EventBridge** rules trigger the observer on daily/hourly cadence
- **SQS** queues for async agent communication
- **S3** for generated reports (PDF/HTML)
- **Secrets Manager** for API keys
- **LangSmith** for agent tracing and observability

## Metrics & Evaluation

- **Savings identified** ($): Total potential savings surfaced by recommender
- **Savings executed** ($): Actual savings from approved + executed actions
- **Anomaly precision/recall**: Accuracy of spend anomaly detection
- **Agent latency**: End-to-end time from observation to recommendation
- **False positive rate**: Recommendations rejected by humans
# Finopy
