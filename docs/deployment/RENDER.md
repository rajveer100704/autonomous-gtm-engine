# Render Deployment Guide

Follow this step-by-step path to deploy the Autonomous GTM Engine platform on Render.

---

## 🏗 Phase 1: Database & Cache Provisioning

### 1. Render PostgreSQL
1. Navigate to **Render Dashboard** → **New +** → **PostgreSQL**.
2. Configure settings:
   - **Name:** `gtm-postgres`
   - **Region:** Same region as your services (e.g., `Oregon (US West)`)
   - **Database Name:** `gtm`
   - **User:** `gtm`
3. Click **Create Database**.
4. Once active, copy the **Internal Database URL** (used for services on Render) and **External Database URL** (for local migrations/debug).

### 2. Upstash Redis (Free-Tier Stream Broker)
1. Register/Login at [Upstash Console](https://console.upstash.com/).
2. Click **Create Database**.
3. Choose:
   - **Type:** Redis
   - **Name:** `gtm-streams`
   - **Region:** Closest to your Render region.
4. Copy the **Redis URL** in the format `redis://default:token@host:port`.

---

## 🚀 Phase 2: Deploying Services

### 1. FastAPI Web Service (API Gateway)
1. Go to **Render Dashboard** → **New +** → **Web Service**.
2. Connect your GitHub repository `rajveer100704/autonomous-gtm-engine`.
3. Configure the following parameters:
   - **Name:** `gtm-api`
   - **Runtime:** `Docker` (Render reads the root `Dockerfile` automatically)
   - **Branch:** `main`
4. Add the **Environment Variables** (see below).
5. Click **Create Web Service**.

### 2. Background Worker Service
1. Go to **Render Dashboard** → **New +** → **Background Worker**.
2. Connect the same GitHub repository.
3. Configure settings:
   - **Name:** `gtm-worker`
   - **Runtime:** `Docker`
   - **Docker Command:** `python -m gtm_engine.queues.worker`
4. Add the exact same **Environment Variables** as the Web Service.
5. Click **Create Background Worker**.

---

## 🔑 Environment Variables Configuration

Add these in the **Environment** settings page for both services:

| Key | Value | Notes |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+psycopg2://...` | Render Internal Database URL |
| `REDIS_URL` | `redis://default:...` | Upstash Redis connection URL |
| `USE_REDIS` | `true` | Enables distributed stream queue mode |
| `GEMINI_API_KEY` | `your-gemini-key` | Google LLM credentials |
| `TAVILY_API_KEY` | `your-tavily-key` | Tavily search API credentials |
| `APOLLO_API_KEY` | `your-apollo-key` | Apollo.io credentials |
| `GTM_MOCK_MODE` | `true` | Set to `false` for real API calls |

---

## 🔍 Phase 3: Post-Deployment Verification

Verify the API endpoints return `200 OK`:
- `https://gtm-api.onrender.com/api/v1/health`
- `https://gtm-api.onrender.com/api/v1/ready` (checks PostgreSQL connectivity)
- `https://gtm-api.onrender.com/metrics`
