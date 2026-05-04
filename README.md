# In-Warehouse ML

A simple Streamlit application that connects to Google BigQuery, finds a BigQuery ML logistic regression model, runs predictions against the public Google Analytics sample dataset, and displays the predicted purchase results in an interactive dashboard.

The app was built for a Google Cloud / BigQuery ML workflow where the model is trained inside BigQuery and then reused from a lightweight Python UI.

## Table of Contents

- [Project Overview](#project-overview)
- [What the App Does](#what-the-app-does)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Google Cloud Setup](#google-cloud-setup)
- [Local Setup](#local-setup)
- [Environment Variables](#environment-variables)
- [Run the App](#run-the-app)
- [How to Use the App](#how-to-use-the-app)
- [BigQuery ML Query Logic](#bigquery-ml-query-logic)
- [Expected Model Features](#expected-model-features)
- [Validation](#validation)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)

## Project Overview

This project uses:

- **Streamlit** for the web interface.
- **Google Cloud BigQuery** for querying public data.
- **BigQuery ML** for serving a trained logistic regression model.
- **Google Analytics Sample Dataset** from `bigquery-public-data`.

The main goal is to let a user choose a trained BigQuery ML model, select a date range and filters, and run predictions without manually writing SQL each time.

Example use case:

> Predict which Google Analytics sessions are likely to result in purchases, then summarize predicted purchases by country, operating system, or device type.

## What the App Does

The Streamlit app can:

- Connect to a Google Cloud project using local application credentials.
- List available BigQuery ML models from a selected dataset.
- Use a selected model with `ML.PREDICT`.
- Query the public table:

```sql
bigquery-public-data.google_analytics_sample.ga_sessions_*
```

- Let the user filter prediction input by:
  - Date range
  - Country
  - Operating system
  - Mobile or desktop device
  - Pageview range
  - Number of rows to score
  - Number of rows to return
- Display either:
  - A grouped prediction summary
  - Individual scored rows
- Show the generated SQL query inside the app.
- Display a bar chart for country-level prediction summaries.

## Architecture

The project has a simple flow:

```text
Streamlit UI
    |
    v
Python BigQuery Client
    |
    v
BigQuery ML.PREDICT
    |
    v
Public Google Analytics Sample Data
    |
    v
Prediction Results in Streamlit
```

The model stays inside BigQuery. The app does not download or serialize the ML model locally. Instead, it sends a SQL query to BigQuery and receives the prediction output as a dataframe.

## Project Structure

```text
In-Warehouse ML/
|-- app.py
|-- requirements.txt
|-- README.md
|-- .env.example               # Safe template for local configuration
|-- .gitignore
|-- .env                       # Local only, ignored by git
`-- service-account.json        # Local only, ignored by git
```

Important files:

- `app.py` contains the Streamlit application and BigQuery query logic.
- `requirements.txt` lists the Python packages needed to run the app.
- `.env.example` shows the environment variables needed to configure the app.
- `.gitignore` prevents local secrets, generated files, and video files from being committed.
- `.env` should contain your local Google Cloud configuration.

## Prerequisites

Before running the app, make sure you have:

- Python 3.10 or newer.
- A Google Cloud project with billing enabled.
- BigQuery API enabled.
- A BigQuery dataset that contains a trained BigQuery ML model.
- A service account JSON key or another valid Google Application Default Credentials setup.
- Access to the public dataset `bigquery-public-data.google_analytics_sample`.

The app was tested with:

- Python 3.11
- Streamlit 1.52.2
- `google-cloud-bigquery` 3.41.0

## Google Cloud Setup

### 1. Enable BigQuery

In Google Cloud Console:

1. Open your project.
2. Go to **APIs & Services**.
3. Enable **BigQuery API**.

### 2. Create or Use a BigQuery Dataset

The example app defaults to this dataset:

```text
bmAI
```

You can change the dataset in the app sidebar or by setting:

```env
BQ_MODEL_DATASET=bmAI
```

### 3. Train a BigQuery ML Model

This app expects a BigQuery ML logistic regression model similar to:

```sql
CREATE OR REPLACE MODEL `bmAI.sample_model`
OPTIONS(model_type = 'logistic_reg') AS
SELECT
  IF(totals.transactions IS NULL, 0, 1) AS label,
  IFNULL(device.operatingSystem, '') AS os,
  device.isMobile AS is_mobile,
  IFNULL(geoNetwork.country, '') AS country,
  IFNULL(totals.pageviews, 0) AS pageviews
FROM
  `bigquery-public-data.google_analytics_sample.ga_sessions_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20160801' AND '20170630'
LIMIT 100000;
```

The model learns from historical session data and predicts whether a session is likely to include a purchase.

### 4. Service Account Permissions

The service account used by the app should have enough permission to:

- Run BigQuery jobs in your project.
- Read the dataset that contains the BigQuery ML model.
- Query the public Google Analytics sample dataset.

Common roles for a learning/demo project:

- `BigQuery Job User`
- `BigQuery Data Viewer`

For production, use least-privilege permissions instead of broad project-level access.

## Local Setup

### 1. Clone the Repository

```powershell
git clone https://github.com/devkhaledai-hub/In-Warehouse-ML.git
cd In-Warehouse-ML
```

### 2. Create a Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```powershell
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root from the provided template.

```powershell
copy .env.example .env
```

Example:

```env
GOOGLE_CLOUD_PROJECT=your-google-cloud-project-id
GOOGLE_PROJECT_ID=your-google-cloud-project-id
GOOGLE_APPLICATION_CREDENTIALS=your-service-account-file.json
BQ_MODEL_DATASET=bmAI
```

Replace the placeholder values with your own project ID and service account JSON filename. Do not commit `.env` or service account JSON files to GitHub.

## Run the App

From the project folder, run:

```powershell
streamlit run app.py
```

Then open the local URL shown by Streamlit, usually:

```text
http://localhost:8501
```

## How to Use the App

### 1. Select the Model

In the sidebar:

1. Confirm the Google Cloud project ID.
2. Confirm the BigQuery dataset that contains your model.
3. Choose a BigQuery ML model from the dropdown.

If the app cannot list models, it will let you type the model name manually.

### 2. Select Source Data

Choose the date range from the public Google Analytics sample dataset.

The sample dataset contains tables in this range:

```text
2016-08-01 to 2017-08-01
```

You can also filter by:

- Country
- Operating system
- Device type
- Pageviews

### 3. Select Result Mode

The app supports two result views.

#### Grouped summary

Aggregates prediction results by one or more fields:

- `country`
- `os`
- `is_mobile`

The summary includes:

- Rows scored
- Total predicted purchases
- Predicted purchase rate
- Average purchase probability
- Actual purchases from the sample data

#### Scored rows

Shows individual scored sessions with:

- Country
- Operating system
- Device type
- Pageviews
- Actual label
- Predicted label
- Purchase probability

### 4. Run Prediction

Click **Run prediction**.

The app sends a parameterized SQL query to BigQuery, runs `ML.PREDICT`, and returns the results to Streamlit.

## BigQuery ML Query Logic

The app builds a query similar to this:

```sql
SELECT
  country,
  COUNT(*) AS rows_scored,
  SUM(IF(CAST(predicted_label AS STRING) = '1', 1, 0)) AS total_predicted_purchases,
  SAFE_DIVIDE(
    SUM(IF(CAST(predicted_label AS STRING) = '1', 1, 0)),
    COUNT(*)
  ) AS predicted_purchase_rate,
  AVG(purchase_probability) AS avg_purchase_probability,
  SUM(actual_label) AS actual_purchases
FROM (
  SELECT
    *,
    (
      SELECT prob
      FROM UNNEST(predicted_label_probs)
      WHERE CAST(label AS STRING) = '1'
      LIMIT 1
    ) AS purchase_probability
  FROM ML.PREDICT(
    MODEL `your-project.bmAI.sample_model`,
    (
      SELECT
        IF(totals.transactions IS NULL, 0, 1) AS actual_label,
        IFNULL(device.operatingSystem, '') AS os,
        device.isMobile AS is_mobile,
        IFNULL(geoNetwork.country, '') AS country,
        IFNULL(totals.pageviews, 0) AS pageviews
      FROM
        `bigquery-public-data.google_analytics_sample.ga_sessions_*`
      WHERE
        _TABLE_SUFFIX BETWEEN '20170701' AND '20170801'
    )
  )
)
GROUP BY country
ORDER BY total_predicted_purchases DESC
LIMIT 10;
```

The actual app query is parameterized to avoid directly injecting user input into SQL filters.

## Expected Model Features

The prediction query uses these input fields:

| Field | Type | Description |
| --- | --- | --- |
| `os` | STRING | Visitor operating system |
| `is_mobile` | BOOL | Whether the visitor used a mobile device |
| `country` | STRING | Visitor country |
| `pageviews` | INT64 | Number of pageviews in the session |
| `actual_label` | INT64 | Actual purchase label, included only for comparison in results |

The training query usually uses the label field:

```sql
IF(totals.transactions IS NULL, 0, 1) AS label
```

The app uses `actual_label` during prediction so the results can show the real historical outcome next to the model prediction.

## Validation

Useful local checks:

```powershell
python -m py_compile app.py
```

```powershell
python -c "import app; print('app import ok')"
```

During development, the BigQuery model listing found:

```text
sample_model
```

A small prediction query also returned country-level prediction results successfully.

## Troubleshooting

### `ModuleNotFoundError: No module named 'google.cloud.bigquery'`

Install dependencies:

```powershell
pip install -r requirements.txt
```

### `DefaultCredentialsError`

Check that your `.env` file has:

```env
GOOGLE_APPLICATION_CREDENTIALS=your-service-account-file.json
```

Also confirm the JSON file exists in the project folder or use an absolute path.

### `403 Forbidden`

The service account probably does not have enough BigQuery permissions.

Check that it can:

- Run query jobs.
- Read the dataset containing the model.
- Read public BigQuery data.

### `404 Not Found`

Check:

- Project ID
- Dataset ID
- Model name
- BigQuery location

The model path should look like:

```text
project_id.dataset_id.model_id
```

Example:

```text
linen-mason-495011-b4.bmAI.sample_model
```

### No Rows Returned

Try:

- Expanding the date range.
- Removing country or OS filters.
- Increasing the row limit.
- Increasing the maximum pageviews filter.

## Security Notes

The repository intentionally ignores:

- `.env`
- `*.json`
- `*.mp4`
- Other common video formats
- Python cache files
- Virtual environments

This prevents uploading:

- Google Cloud service account keys
- Local environment secrets
- Demo videos
- Generated Python cache files

Never commit service account credentials to GitHub. If a key is accidentally pushed, revoke it immediately in Google Cloud IAM and create a new key.
