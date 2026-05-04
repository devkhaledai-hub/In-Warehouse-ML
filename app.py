import datetime as dt
import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google.api_core.exceptions import Forbidden, GoogleAPIError, NotFound
from google.cloud import bigquery


load_dotenv()

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = os.getenv("BQ_MODEL_DATASET", "bmAI")
PUBLIC_TABLE = "`bigquery-public-data.google_analytics_sample.ga_sessions_*`"
DATE_MIN = dt.date(2016, 8, 1)
DATE_MAX = dt.date(2017, 8, 1)

PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BQ_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

GROUP_FIELDS = {
    "country": "country",
    "os": "os",
    "is_mobile": "is_mobile",
}


def normalize_credentials_path() -> None:
    credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials:
        return

    path = Path(credentials.strip("\"'"))
    if not path.is_absolute():
        path = APP_DIR / path

    if path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)


def project_from_credentials() -> str:
    credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials:
        return ""

    path = Path(credentials.strip("\"'"))
    if not path.is_absolute():
        path = APP_DIR / path

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    return data.get("project_id", "")


def default_project() -> str:
    return (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GOOGLE_PROJECT_ID")
        or project_from_credentials()
    )


def validate_project(project_id: str) -> str:
    project_id = project_id.strip()
    if not PROJECT_RE.match(project_id):
        raise ValueError("Project ID can only contain letters, numbers, hyphens, and underscores.")
    return project_id


def validate_bq_id(value: str, label: str) -> str:
    value = value.strip()
    if not BQ_ID_RE.match(value):
        raise ValueError(f"{label} must be a valid BigQuery identifier.")
    return value


def model_path(project_id: str, dataset_id: str, model_id: str) -> str:
    return (
        f"`{validate_project(project_id)}."
        f"{validate_bq_id(dataset_id, 'Dataset ID')}."
        f"{validate_bq_id(model_id, 'Model ID')}`"
    )


@st.cache_resource(show_spinner=False)
def get_client(project_id: str) -> bigquery.Client:
    normalize_credentials_path()
    return bigquery.Client(project=project_id or None)


@st.cache_data(ttl=300, show_spinner=False)
def list_models(project_id: str, dataset_id: str) -> list[dict]:
    client = get_client(project_id)
    dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
    models = client.list_models(dataset_ref)
    return [
        {
            "model_id": model.model_id,
            "model_type": getattr(model, "model_type", "UNKNOWN"),
            "created": getattr(model, "created", None),
        }
        for model in models
    ]


def suffix(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def build_where_clause() -> str:
    return """
      _TABLE_SUFFIX BETWEEN @start_suffix AND @end_suffix
      AND (@country = "" OR IFNULL(geoNetwork.country, "") = @country)
      AND (@os = "" OR IFNULL(device.operatingSystem, "") = @os)
      AND (
        @mobile_filter = "all"
        OR device.isMobile = IF(@mobile_filter = "mobile", TRUE, FALSE)
      )
      AND IFNULL(totals.pageviews, 0) BETWEEN @min_pageviews AND @max_pageviews
    """


def input_query_sql() -> str:
    return f"""
    SELECT
      IF(totals.transactions IS NULL, 0, 1) AS actual_label,
      IFNULL(device.operatingSystem, "") AS os,
      device.isMobile AS is_mobile,
      IFNULL(geoNetwork.country, "") AS country,
      IFNULL(totals.pageviews, 0) AS pageviews
    FROM
      {PUBLIC_TABLE}
    WHERE
      {build_where_clause()}
    LIMIT @row_limit
    """


def prediction_cte_sql(full_model_path: str) -> str:
    return f"""
    WITH input_data AS (
      {input_query_sql()}
    ),
    predictions AS (
      SELECT
        *,
        (
          SELECT prob
          FROM UNNEST(predicted_label_probs)
          WHERE CAST(label AS STRING) = "1"
          LIMIT 1
        ) AS purchase_probability
      FROM ML.PREDICT(
        MODEL {full_model_path},
        (
          SELECT
            os,
            is_mobile,
            country,
            pageviews,
            actual_label
          FROM input_data
        )
      )
    )
    """


def build_summary_sql(full_model_path: str, group_fields: list[str]) -> str:
    selected_fields = [GROUP_FIELDS[field] for field in group_fields]
    select_group = ",\n      ".join(selected_fields)
    group_by = ", ".join(selected_fields)

    return f"""
    {prediction_cte_sql(full_model_path)}
    SELECT
      {select_group},
      COUNT(*) AS rows_scored,
      SUM(IF(CAST(predicted_label AS STRING) = "1", 1, 0)) AS total_predicted_purchases,
      SAFE_DIVIDE(
        SUM(IF(CAST(predicted_label AS STRING) = "1", 1, 0)),
        COUNT(*)
      ) AS predicted_purchase_rate,
      AVG(purchase_probability) AS avg_purchase_probability,
      SUM(actual_label) AS actual_purchases
    FROM predictions
    GROUP BY {group_by}
    ORDER BY total_predicted_purchases DESC, rows_scored DESC
    LIMIT @top_n
    """


def build_detail_sql(full_model_path: str) -> str:
    return f"""
    {prediction_cte_sql(full_model_path)}
    SELECT
      country,
      os,
      is_mobile,
      pageviews,
      actual_label,
      predicted_label,
      purchase_probability
    FROM predictions
    ORDER BY purchase_probability DESC
    LIMIT @top_n
    """


def query_parameters(
    start_date: dt.date,
    end_date: dt.date,
    country: str,
    os_name: str,
    mobile_filter: str,
    min_pageviews: int,
    max_pageviews: int,
    row_limit: int,
    top_n: int,
) -> list[bigquery.ScalarQueryParameter]:
    return [
        bigquery.ScalarQueryParameter("start_suffix", "STRING", suffix(start_date)),
        bigquery.ScalarQueryParameter("end_suffix", "STRING", suffix(end_date)),
        bigquery.ScalarQueryParameter("country", "STRING", country.strip()),
        bigquery.ScalarQueryParameter("os", "STRING", os_name.strip()),
        bigquery.ScalarQueryParameter("mobile_filter", "STRING", mobile_filter),
        bigquery.ScalarQueryParameter("min_pageviews", "INT64", int(min_pageviews)),
        bigquery.ScalarQueryParameter("max_pageviews", "INT64", int(max_pageviews)),
        bigquery.ScalarQueryParameter("row_limit", "INT64", int(row_limit)),
        bigquery.ScalarQueryParameter("top_n", "INT64", int(top_n)),
    ]


def run_query(client: bigquery.Client, sql: str, params: list[bigquery.ScalarQueryParameter]) -> pd.DataFrame:
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    job = client.query(sql, job_config=job_config)
    return job.result().to_dataframe(create_bqstorage_client=False)


def render_results(df: pd.DataFrame, mode: str) -> None:
    if df.empty:
        st.warning("No rows matched the selected filters.")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)

    if mode == "Grouped summary":
        total = int(df["total_predicted_purchases"].sum())
        rows_scored = int(df["rows_scored"].sum())
        rate = total / rows_scored if rows_scored else 0

        metric_cols = st.columns(3)
        metric_cols[0].metric("Rows scored", f"{rows_scored:,}")
        metric_cols[1].metric("Predicted purchases", f"{total:,}")
        metric_cols[2].metric("Predicted rate", f"{rate:.2%}")

        if "country" in df.columns:
            chart_df = df[["country", "total_predicted_purchases"]].set_index("country")
            st.bar_chart(chart_df)


def main() -> None:
    st.set_page_config(
        page_title="BigQuery ML Predictor",
        layout="wide",
    )

    st.title("BigQuery ML Purchase Predictor")

    with st.sidebar:
        st.header("Model")
        project_id = st.text_input("Project ID", value=default_project())
        dataset_id = st.text_input("Model dataset", value=DEFAULT_DATASET)

        selected_model = ""
        models: list[dict] = []
        model_error = ""

        if project_id and dataset_id:
            try:
                models = list_models(validate_project(project_id), validate_bq_id(dataset_id, "Dataset ID"))
            except (Forbidden, NotFound, GoogleAPIError, ValueError) as exc:
                model_error = str(exc)

        if model_error:
            st.error(model_error)
        elif models:
            labels = [
                f"{model['model_id']} ({model['model_type']})"
                for model in sorted(models, key=lambda item: item["model_id"])
            ]
            label_to_model = {
                f"{model['model_id']} ({model['model_type']})": model["model_id"]
                for model in models
            }
            selected_label = st.selectbox("BigQuery ML model", labels)
            selected_model = label_to_model[selected_label]
        else:
            selected_model = st.text_input("Model name", value="sample_model")

        st.header("Source data")
        date_range = st.date_input(
            "Prediction date range",
            value=(dt.date(2017, 7, 1), dt.date(2017, 8, 1)),
            min_value=DATE_MIN,
            max_value=DATE_MAX,
        )
        country = st.text_input("Country filter", value="")
        os_name = st.text_input("Operating system filter", value="")
        mobile_choice = st.selectbox("Device type", ["All", "Mobile only", "Desktop only"])
        mobile_filter = {
            "All": "all",
            "Mobile only": "mobile",
            "Desktop only": "desktop",
        }[mobile_choice]

        st.header("Query")
        min_pageviews = st.number_input("Min pageviews", min_value=0, value=0, step=1)
        max_pageviews = st.number_input("Max pageviews", min_value=1, value=500, step=1)
        row_limit = st.number_input("Rows to score", min_value=10, max_value=1_000_000, value=100_000, step=1_000)
        top_n = st.number_input("Rows to return", min_value=1, max_value=1_000, value=10, step=1)
        mode = st.selectbox("Result view", ["Grouped summary", "Scored rows"])
        group_fields = st.multiselect(
            "Group summary by",
            options=list(GROUP_FIELDS.keys()),
            default=["country"],
            disabled=mode != "Grouped summary",
        )

        run = st.button("Run prediction", type="primary", use_container_width=True)

    if not project_id or not dataset_id or not selected_model:
        st.info("Enter your project, dataset, and model to start.")
        return

    if not isinstance(date_range, tuple) or len(date_range) != 2:
        st.info("Choose a start and end date.")
        return

    start_date, end_date = date_range
    if start_date > end_date:
        st.error("Start date must be before end date.")
        return

    if min_pageviews > max_pageviews:
        st.error("Min pageviews must be less than or equal to max pageviews.")
        return

    if mode == "Grouped summary" and not group_fields:
        st.error("Select at least one group field.")
        return

    try:
        full_model_path = model_path(project_id, dataset_id, selected_model)
    except ValueError as exc:
        st.error(str(exc))
        return

    params = query_parameters(
        start_date=start_date,
        end_date=end_date,
        country=country,
        os_name=os_name,
        mobile_filter=mobile_filter,
        min_pageviews=min_pageviews,
        max_pageviews=max_pageviews,
        row_limit=row_limit,
        top_n=top_n,
    )

    sql = (
        build_summary_sql(full_model_path, group_fields)
        if mode == "Grouped summary"
        else build_detail_sql(full_model_path)
    )

    with st.expander("SQL"):
        st.code(sql, language="sql")

    if run:
        try:
            client = get_client(validate_project(project_id))
            with st.spinner("Running BigQuery ML prediction..."):
                df = run_query(client, sql, params)
            render_results(df, mode)
        except (Forbidden, NotFound, GoogleAPIError, ValueError) as exc:
            st.error(str(exc))
    else:
        st.info("Set your filters, then run the prediction.")


if __name__ == "__main__":
    main()
