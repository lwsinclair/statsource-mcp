"""
MCP (Message Control Protocol) Server for Statistical Analysis

This module implements an MCP server that acts as a middleware between
clients (like Claude Desktop app) and our existing API. It runs independently
and forwards requests to the API whose URL is configurable via environment variables.
"""

import os
import json
import requests
import sys
import time
from typing import Dict, List, Any, Optional, Union
from pydantic import BaseModel, Field, field_validator
from mcp.server.fastmcp import FastMCP
from datetime import datetime

# Read API location from environment variable with a default fallback
API_URL = "https://api.statsource.me"
API_KEY = os.getenv("API_KEY", None)  # Optional API key for authentication

# Database connection string from environment variable
DB_CONNECTION_STRING = os.getenv("DB_CONNECTION_STRING", None)
DB_SOURCE_TYPE = os.getenv("DB_SOURCE_TYPE", "database")  # Default to database if not specified

# Initialize MCP server with specific protocol version
mcp = FastMCP("ai_mcp_server", protocol_version="2024-11-05")

# Define input models for data validation
class StatisticsRequest(BaseModel):
    """Request model for statistical operations."""
    operation: str = Field(..., description="Statistical operation to perform (mean, median, sum, etc.)")
    data: List[float] = Field(..., description="List of numeric data points")
    
    @field_validator('operation')
    @classmethod
    def validate_operation(cls, v):
        valid_operations = ['mean', 'median', 'sum', 'min', 'max', 'std', 'var', 'count']
        if v.lower() not in valid_operations:
            raise ValueError(f"Operation must be one of {valid_operations}")
        return v.lower()
    
    @field_validator('data')
    @classmethod
    def validate_data(cls, v):
        if not v:
            raise ValueError("Data list cannot be empty")
        return v

# Helper function to check if API is available
def is_api_available() -> bool:
    """
    Check if the API is available.
    
    Returns:
        bool: True if API is available, False otherwise
    """
    try:
        # Try to connect to the base URL
        response = requests.get(API_URL, timeout=5)
        return response.status_code < 500  # Consider 2xx, 3xx, 4xx as "available"
    except requests.RequestException:
        return False

# Helper function to make API calls
def call_api(endpoint: str, data: Dict[str, Any], params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Make a request to the API.
    
    Args:
        endpoint: API endpoint path (without base URL)
        data: Request payload
        params: URL query parameters
        
    Returns:
        API response as dictionary
    
    Raises:
        Exception: If the API request fails
    """
    # Check if API is available first
    if not is_api_available():
        raise Exception(f"API at {API_URL} is not available")
    
    headers = {"Content-Type": "application/json"}
    
    # Add authentication if API key is provided
    if API_KEY:
        headers["API-Key"] = API_KEY
    
    full_url = f"{API_URL}{endpoint}"
    try:
        response = requests.post(full_url, json=data, headers=headers, params=params)
        response.raise_for_status()  # Raise exception for 4XX/5XX responses
        
        response_data = response.json()
        return response_data
    except requests.RequestException as e:
        if hasattr(e, 'response') and e.response is not None:
            error_text = e.response.text
            status_code = e.response.status_code
            return {"error": f"API request failed with status {status_code}: {error_text}"}
        else:
            error_text = str(e)
            return {"error": f"API request failed: {error_text}"}

# Define MCP tools
@mcp.tool()
def suggest_feature(description: str, use_case: str, priority: str = "medium") -> str:
    """
    Suggest a new feature or improvement for the StatSource analytics platform.
    
    ### What this tool does:
    This tool allows you to submit feature suggestions or enhancement requests for 
    the StatSource platform. Suggestions are logged and reviewed by the development team.
    
    ### When to use this tool:
    - When a user asks for functionality that doesn't currently exist
    - When you identify gaps or limitations in the current analytics capabilities
    - When a user expresses frustration about missing capabilities
    - When you think of enhancements that would improve the user experience
    
    ### Required inputs:
    - description: A clear, detailed description of the suggested feature
    - use_case: Explanation of how and why users would use this feature
    
    ### Optional inputs:
    - priority: Suggested priority level ("low", "medium", "high")
    
    ### Returns:
    A confirmation message and reference ID for the feature suggestion.
    """
    try:
        # Format the request
        suggestion_data = {
            "description": description,
            "use_case": use_case,
            "priority": priority,
            "source": "ai_agent"
        }
        
        # Call the feature suggestion endpoint
        endpoint = "/api/v1/feature_suggestions"
        response = call_api(endpoint, suggestion_data)
        
        if "error" in response:
            return f"Error: {response['error']}"
        
        # Format the response
        suggestion_id = response.get("id", "unknown")
        return json.dumps({
            "status": "received",
            "message": "Thank you for your feature suggestion. Our team will review it.",
            "suggestion_id": f"FEAT-{suggestion_id}"
        }, indent=2)
    except Exception as e:
        return f"Error submitting feature suggestion: {str(e)}"

# Internal helper function to handle common logic for statistics/prediction API calls
def _invoke_statistics_api(
    query_type: str,
    columns: List[str],
    statistics: Optional[List[str]] = None,
    periods: Optional[int] = None,
    anomaly_options: Optional[Dict[str, Any]] = None,
    data_source: Optional[str] = None,
    source_type: Optional[str] = None,
    table_name: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    groupby: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
    date_column: Optional[str] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    aggregation: Optional[str] = None
) -> str:
    """Internal helper to call the statistics API endpoint."""
    # Determine the final data source and type, considering environment variables/config
    final_data_source = data_source
    final_source_type = source_type

    # Use default DB connection if no source is specified and DB_CONNECTION_STRING is set
    if not final_data_source and DB_CONNECTION_STRING:
        final_data_source = DB_CONNECTION_STRING
        final_source_type = DB_SOURCE_TYPE  # Use configured DB type
    elif not final_source_type and final_data_source:
        # Infer source type if not explicitly provided (basic inference)
        if "://" in final_data_source or final_data_source.lower().startswith("http"):
            if any(db_protocol in final_data_source.lower() for db_protocol in ["postgresql://", "mysql://", "sqlite://"]):
                final_source_type = "database"
            else:
                 final_source_type = "api" # Assume API if it looks like a URL but not a DB string
        else:
            final_source_type = "csv" # Assume CSV otherwise (filename)

    # Basic validation based on context
    if not columns:
        return json.dumps({"error": "The 'columns' parameter is required and cannot be empty."})
        
    if final_source_type == "database" and not table_name:
        return json.dumps({"error": "The 'table_name' parameter is required when source_type is 'database'."})
        
    if not final_data_source and not DB_CONNECTION_STRING:
         return json.dumps({"error": "No data_source provided and no default database connection configured. Please provide a data_source (filename, DB connection string, or API URL)."})

    # Prepare request payload and parameters for the API call
    api_request_data = {
        "data_source": final_data_source,
        "source_type": final_source_type,
        "columns": columns,
        "table_name": table_name,
        "filters": filters,
        "groupby": groupby,
        "options": options,
        "date_column": date_column,
        # Convert datetime objects to ISO strings for JSON serialization if necessary
        "start_date": start_date.isoformat() if isinstance(start_date, datetime) else start_date,
        "end_date": end_date.isoformat() if isinstance(end_date, datetime) else end_date,
    }
    
    api_params = {"query_type": query_type}

    if query_type == "statistics":
        if not statistics:
             return json.dumps({"error": "The 'statistics' parameter is required for calculate_statistics."})
        api_request_data["statistics"] = statistics
        # Groupby only makes sense for statistics
        if groupby:
             api_request_data["groupby"] = groupby
        else:
             if "groupby" in api_request_data: del api_request_data["groupby"] # Ensure it's not sent if None
             
    elif query_type == "ml_prediction":
        if periods is None:
            return json.dumps({"error": "The 'periods' parameter is required for predict_trends."})
        api_params["periods"] = periods
        # Handle aggregation parameter if provided
        if aggregation:
            api_request_data["aggregation"] = aggregation
        # Remove stats/grouping/anomaly params if present
        if "statistics" in api_request_data: del api_request_data["statistics"]
        if "groupby" in api_request_data: del api_request_data["groupby"]
        if "anomaly_options" in api_request_data: del api_request_data["anomaly_options"]

    elif query_type == "anomaly_detection":
        if not date_column:
             return json.dumps({"error": "The 'date_column' parameter is required for anomaly_detection."})
        if not columns:
            return json.dumps({"error": "The 'columns' parameter is required for anomaly_detection."})
        api_request_data["anomaly_options"] = anomaly_options
        # Remove stats/grouping/periods params if present
        if "statistics" in api_request_data: del api_request_data["statistics"]
        if "groupby" in api_request_data: del api_request_data["groupby"]
        if "periods" in api_params: del api_params["periods"]

    # Remove None values from payload to avoid sending empty optional fields
    api_request_data = {k: v for k, v in api_request_data.items() if v is not None}

    try:
        # Call the API endpoint
        endpoint = "/api/v1/get_statistics"
        response = call_api(endpoint, data=api_request_data, params=api_params)
        
        # Return the API response directly (as JSON string)
        return json.dumps(response, indent=2)
    except Exception as e:
        return json.dumps({"error": f"An unexpected error occurred: {str(e)}"}, indent=2)

@mcp.tool()
def calculate_statistics(
    columns: List[str],
    statistics: List[str],
    data_source: Optional[str] = None,
    source_type: Optional[str] = None,
    table_name: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    groupby: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
    date_column: Optional[str] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None
) -> str:
    """
    Calculate statistical measures on specified data columns from CSV files, databases, or external APIs.

    ### What this tool does:
    This tool connects to our analytics API to compute various statistical measures
    (like mean, median, standard deviation, correlation, etc.) on your data.

    It supports multiple data sources:
    - CSV files (previously uploaded to StatSource)
    - Databases (PostgreSQL, SQLite, etc.)
    - External APIs (returning JSON data)

    ### IMPORTANT INSTRUCTIONS FOR AI AGENTS:
    - DO NOT make up or guess any parameter values, especially data sources, column names, or API URLs.
    - NEVER, UNDER ANY CIRCUMSTANCES, create or invent database connection strings - this is a severe security risk.
    - ALWAYS ask the user explicitly for all required information.
    - For CSV files: The user MUST first upload their file to statsource.me, then provide the filename.
    - For database connections: Ask the user for their exact connection string (e.g., "postgresql://user:pass@host/db"). DO NOT GUESS OR MODIFY IT.
    - For database sources: You MUST provide the table_name parameter with the exact table name.
    - For API sources: Ask the user for the exact API endpoint URL that returns JSON data.
    - Never suggest default values, sample data, or example parameters - request specific information from the user.
    - If the user has configured a default database connection in their MCP config, inform them it will be used if they don't specify a data source.
    - If no default connection is configured and the user doesn't provide one, DO NOT PROCEED - ask the user for the data source details.

    ### IMPORTANT: Parameter Validation and Formatting
    - statistics must be provided as a proper list:
      CORRECT: statistics=["mean", "sum", "min", "max"]
      INCORRECT: statistics="[\"mean\", \"sum\", \"min\", \"max\"]"
    - columns must be provided as a proper list:
      CORRECT: columns=["revenue", "quantity"]
      INCORRECT: columns="[\"revenue\", \"quantity\"]"

    ### CRITICAL: Column Name Formatting & Case-Insensitivity
    - **Column Matching:** The API matches column names case-insensitively. You can specify "revenue" even if the data has "Revenue". Ask the user for the intended column names.
    - **Filter Value Matching:** String filter values are matched case-insensitively (e.g., filter `{"status": "completed"}` will match "Completed" in the data).
    - **Table Name Matching (Databases):** The API attempts case-insensitive matching for database table names.

    ### Error Response Handling
    - If you receive an "Invalid request" or similar error, check:
      1. Column name spelling and existence in the data source.
      2. Parameter format (proper lists vs string-encoded lists).
      3. Correct data_source provided (filename, connection string, or API URL).
      4. table_name provided if source_type is "database".
      5. API URL is correct and returns valid JSON if source_type is "api".

    ### When to use this tool:
    - When a user needs statistical analysis of their data (means, medians, correlations, distributions, etc.).
    - When analyzing patterns or summarizing datasets from files, databases, or APIs.

    ### Required inputs:
    - columns: List of column names to analyze (ask user for exact column names in their data).
    - statistics: List of statistics to calculate.

    ### Optional inputs:
    - data_source: Identifier for the data source.
      * For CSV: Filename of a previously uploaded file on statsource.me (ask user to upload first).
      * For Database: Full connection string (ask user for exact string).
      * For API: The exact URL of the API endpoint returning JSON data (ask user for the URL).
      * If not provided, will use the connection string from MCP config if available (defaults to database type).
    - source_type: Type of data source ('csv', 'database', or 'api').
      * Determines how `data_source` is interpreted.
      * If not provided, will use the source type from MCP config if available (defaults to 'database'). Ensure this matches the provided `data_source`.
    - table_name: Name of the database table to use (REQUIRED for database sources).
      * Must be provided when source_type is 'database'.
      * Ask user for the exact table name in their database.
    - filters: Dictionary of column-value pairs to filter data *before* analysis.
      * Format: {"column_name": "value"} or {"column_name": ["val1", "val2"]}
      * **API Source Behavior:** For 'api' sources, data is fetched *first*, then filters are applied to the resulting data.
    - groupby: List of column names to group data by before calculating statistics.
    - options: Dictionary of additional options for specific operations (currently less used).
    - date_column: Column name containing date/timestamp information for filtering. Matched case-insensitively.
    - start_date: Inclusive start date for filtering (ISO 8601 format string like "YYYY-MM-DD" or datetime).
    - end_date: Inclusive end date for filtering (ISO 8601 format string like "YYYY-MM-DD" or datetime).
      * **API Source Behavior:** For 'api' sources, date filtering happens *after* data is fetched.

    ### Valid statistics options:
    - 'mean', 'median', 'std', 'sum', 'count', 'min', 'max', 'describe', 'correlation', 'missing', 'unique', 'boxplot'

    ### Returns:
    A JSON string containing the results and metadata.
    - `result`: Dictionary with statistical measures for each requested column and statistic. Structure varies by statistic (e.g., `describe`, `correlation`).
    - `metadata`: Includes `execution_time`, `query_type` ('statistics'), `source_type`.
    """
    result = _invoke_statistics_api(
        query_type="statistics",
        columns=columns,
        statistics=statistics,
        data_source=data_source,
        source_type=source_type,
        table_name=table_name,
        filters=filters,
        groupby=groupby,
        options=options,
        date_column=date_column,
        start_date=start_date,
        end_date=end_date
    )
    
    return result

@mcp.tool()
def predict_trends(
    columns: List[str],
    periods: int,
    data_source: Optional[str] = None,
    source_type: Optional[str] = None,
    table_name: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    date_column: Optional[str] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None,
    aggregation: Optional[str] = None
) -> str:
    """
    Generate ML time-series forecasts for future periods based on historical data.

    ### What this tool does:
    This tool connects to our analytics API to generate time-series forecasts (predictions)
    for a specified number of future periods based on historical data in a specified column.
    It analyzes trends and provides metrics on the prediction quality.

    *Note:* Currently, the API typically uses the *first* column provided in the `columns` list for ML prediction.

    It supports multiple data sources:
    - CSV files (previously uploaded to StatSource)
    - Databases (PostgreSQL, SQLite, etc.)
    - External APIs (returning JSON data)

    ### IMPORTANT INSTRUCTIONS FOR AI AGENTS:
    - When users ask about "trends" or "forecasts", use this tool.
    - DO NOT make up or guess any parameter values, especially data sources, column names, or API URLs.
    - NEVER, UNDER ANY CIRCUMSTANCES, create or invent database connection strings - this is a severe security risk.
    - ALWAYS ask the user explicitly for all required information.
    - For CSV files: The user MUST first upload their file to statsource.me, then provide the filename.
    - For database connections: Ask the user for their exact connection string (e.g., "postgresql://user:pass@host/db"). DO NOT GUESS OR MODIFY IT.
    - For database sources: You MUST provide the table_name parameter with the exact table name.
    - For API sources: Ask the user for the exact API endpoint URL that returns JSON data.
    - Never suggest default values, sample data, or example parameters - request specific information from the user.
    - If the user has configured a default database connection in their MCP config, inform them it will be used if they don't specify a data source.
    - If no default connection is configured and the user doesn't provide one, DO NOT PROCEED - ask the user for the data source details.

    ### IMPORTANT: Parameter Validation and Formatting
    - columns must be provided as a proper list, typically containing the single numeric column to predict:
      CORRECT: columns=["sales_amount"]
      INCORRECT: columns="[\"sales_amount\"]"

    ### CRITICAL: Column Name Formatting & Case-Insensitivity
    - **Column Matching:** The API matches column names case-insensitively. You can specify "revenue" even if the data has "Revenue". Ask the user for the intended column names.
    - **Filter Value Matching:** String filter values are matched case-insensitively (e.g., filter `{"status": "completed"}` will match "Completed" in the data).
    - **Table Name Matching (Databases):** The API attempts case-insensitive matching for database table names.
    - **Date Column:** If using time-based prediction, ensure `date_column` correctly identifies the date/timestamp column. Matched case-insensitively.

    ### Error Response Handling
    - If you receive an "Invalid request" or similar error, check:
      1. Column name spelling and existence in the data source (should be numeric for prediction).
      2. Parameter format (proper lists vs string-encoded lists).
      3. Correct data_source provided (filename, connection string, or API URL).
      4. table_name provided if source_type is "database".
      5. API URL is correct and returns valid JSON if source_type is "api".
      6. `periods` parameter is provided and is a positive integer.
      7. `date_column` is specified if required for the underlying model.

    ### When to use this tool:
    - When a user wants to predict future values based on historical trends (forecasting).
    - When generating forecasts for business planning or decision-making.
    - When analyzing the likely future direction of a time-series metric.

    ### Required inputs:
    - columns: List containing the name of the (usually single) numeric column to predict trends for.
    - periods: Number of future periods to predict.

    ### Optional inputs:
    - data_source: Identifier for the data source.
      * For CSV: Filename of a previously uploaded file on statsource.me (ask user to upload first).
      * For Database: Full connection string (ask user for exact string).
      * For API: The exact URL of the API endpoint returning JSON data (ask user for the URL).
      * If not provided, will use the connection string from MCP config if available (defaults to database type).
    - source_type: Type of data source ('csv', 'database', or 'api').
      * Determines how `data_source` is interpreted.
      * If not provided, will use the source type from MCP config if available (defaults to 'database'). Ensure this matches the provided `data_source`.
    - table_name: Name of the database table to use (REQUIRED for database sources).
      * Must be provided when source_type is 'database'.
      * Ask user for the exact table name in their database.
    - filters: Dictionary of column-value pairs to filter data *before* analysis.
      * Format: {"column_name": "value"} or {"column_name": ["val1", "val2"]}
      * **API Source Behavior:** For 'api' sources, data is fetched *first*, then filters are applied to the resulting data.
    - options: Dictionary of additional options for specific operations (currently less used, might include model tuning params in future).
    - date_column: Column name containing date/timestamp information.
      * Used for date filtering and essential for time-based trend analysis/predictions. Matched case-insensitively.
    - start_date: Inclusive start date for filtering historical data (ISO 8601 format string like "YYYY-MM-DD" or datetime).
    - end_date: Inclusive end date for filtering historical data (ISO 8601 format string like "YYYY-MM-DD" or datetime).
      * **API Source Behavior:** For 'api' sources, date filtering happens *after* data is fetched.
    - aggregation (str, Optional, default: "auto"): Specifies how time-series data should be aggregated before forecasting. Ask the user for their preference if unsure, or default to 'auto'/'monthly'.
      * 'auto': Automatically selects 'weekly' or 'monthly' based on data density and timeframe. Defaults to 'monthly' if unsure. A safe default choice.
      * 'weekly': Aggregates data by week. Use for forecasting short-term trends (e.g., predicting next few weeks/months) or when weekly patterns are important.
      * 'monthly': Aggregates data by month. Recommended for most business forecasting (e.g., predicting quarterly or annual trends) as it smooths out daily/weekly noise.
      * 'daily': Uses daily data. Choose only if the user needs very granular forecasts and understands the potential for noise. Requires sufficient daily data points.

    ### ML Prediction features returned:
    - Time series forecasting with customizable prediction periods.
    - Trend direction analysis ("increasing", "decreasing", "stable").
    - Model quality metrics (r-squared, slope).
    - Works with numeric data columns from any supported data source.
    - Can use a specific `date_column` for time-based regression.

    ### Returns:
    A JSON string containing the prediction results and metadata.
    - `result`: Dictionary containing prediction details per analyzed column (typically the first one specified): `{"r_squared": ..., "slope": ..., "trend_direction": ..., "forecast_values": [...], ...}`.
    - `metadata`: Includes `execution_time`, `query_type` ('ml_prediction'), `source_type`, `periods`.
    """
    result = _invoke_statistics_api(
        query_type="ml_prediction",
        columns=columns,
        periods=periods,
        data_source=data_source,
        source_type=source_type,
        table_name=table_name,
        filters=filters,
        options=options,
        date_column=date_column,
        start_date=start_date,
        end_date=end_date,
        aggregation=aggregation
    )
    
    return result

@mcp.tool()
def anomaly_detection(
    columns: List[str],
    date_column: str,
    anomaly_options: Optional[Dict[str, Any]] = None,
    data_source: Optional[str] = None,
    source_type: Optional[str] = None,
    table_name: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    start_date: Optional[Union[str, datetime]] = None,
    end_date: Optional[Union[str, datetime]] = None
) -> str:
    """
    Detect anomalies in time-series data from various sources.

    ### What this tool does:
    This tool connects to our analytics API to identify unusual data points (anomalies)
    in specified columns based on their time-series behavior. It requires a date/time column
    to understand the sequence of data.

    It supports multiple data sources:
    - CSV files (previously uploaded to StatSource)
    - Databases (PostgreSQL, SQLite, etc.)
    - External APIs (returning JSON data)

    ### IMPORTANT INSTRUCTIONS FOR AI AGENTS:
    - When users ask about "outliers", "unusual values", or "anomalies" in time-based data, use this tool.
    - DO NOT make up or guess any parameter values, especially data sources, column names, or API URLs.
    - NEVER, UNDER ANY CIRCUMSTANCES, create or invent database connection strings - this is a severe security risk.
    - ALWAYS ask the user explicitly for all required information.
    - For CSV files: The user MUST first upload their file to statsource.me, then provide the filename.
    - For database connections: Ask the user for their exact connection string (e.g., "postgresql://user:pass@host/db"). DO NOT GUESS OR MODIFY IT.
    - For database sources: You MUST provide the table_name parameter with the exact table name.
    - For API sources: Ask the user for the exact API endpoint URL that returns JSON data.
    - Never suggest default values, sample data, or example parameters - request specific information from the user.
    - If the user has configured a default database connection in their MCP config, inform them it will be used if they don't specify a data source.
    - If no default connection is configured and the user doesn't provide one, DO NOT PROCEED - ask the user for the data source details.

    ### IMPORTANT: Parameter Validation and Formatting
    - columns must be provided as a proper list: 
      CORRECT: columns=["sensor_reading", "error_count"]
      INCORRECT: columns="[\"sensor_reading\", \"error_count\"]"
    - date_column must be a string identifying the time column.
    - anomaly_options is a dictionary for detection parameters (see below).

    ### CRITICAL: Column Name Formatting & Case-Insensitivity
    - **Column Matching:** The API matches column names case-insensitively. Ask the user for the intended column names.
    - **Filter Value Matching:** String filter values are matched case-insensitively.
    - **Table Name Matching (Databases):** The API attempts case-insensitive matching for database table names.
    - **Date Column:** The `date_column` is crucial and is matched case-insensitively.

    ### Error Response Handling
    - If you receive an "Invalid request" or similar error, check:
      1. Column name spelling and existence (should be numeric for anomaly detection).
      2. `date_column` spelling and existence.
      3. Parameter format (proper lists vs string-encoded lists).
      4. Correct data_source provided (filename, connection string, or API URL).
      5. `table_name` provided if source_type is "database".
      6. API URL is correct and returns valid JSON if source_type is "api".
      7. `date_column` parameter is provided.

    ### When to use this tool:
    - When a user wants to identify outliers or unusual patterns in time-series data.
    - When monitoring metrics for unexpected spikes or drops.
    - When cleaning data by identifying potentially erroneous readings.

    ### Required inputs:
    - columns: List of numeric column names to check for anomalies.
    - date_column: Name of the column containing date/timestamp information.

    ### Optional inputs:
    - data_source: Identifier for the data source.
      * For CSV: Filename of a previously uploaded file on statsource.me.
      * For Database: Full connection string.
      * For API: The exact URL of the API endpoint returning JSON data.
      * If not provided, uses the default connection from MCP config if available.
    - source_type: Type of data source ('csv', 'database', or 'api').
      * Determines how `data_source` is interpreted.
      * Defaults based on MCP config if available.
    - table_name: Name of the database table (REQUIRED for database sources).
    - filters: Dictionary of column-value pairs to filter data *before* analysis.
    - options: Dictionary of additional options (less common for anomaly detection currently).
    - start_date: Inclusive start date for filtering historical data (ISO 8601 string or datetime).
    - end_date: Inclusive end date for filtering historical data (ISO 8601 string or datetime).
    - anomaly_options: Dictionary to configure the detection method and parameters.
      * `method` (str, Optional, default: "iqr"): The anomaly detection method to use. Must be one of:
        - 'iqr': Interquartile Range - Identifies outliers based on distribution quartiles
        - 'zscore': Z-score - Identifies outliers based on standard deviations from the mean
        - 'isolation_forest': Machine learning approach that isolates anomalies using random forest
      * `sensitivity` (float, Optional, default: 1.5): For 'iqr' method, the multiplier for the IQR to define outlier bounds.
        - Higher values are less sensitive (1.5 is standard, 3.0 would detect only extreme outliers)
      * `threshold` (float, Optional, default: 3.0): For 'zscore' method, the threshold for Z-scores to define outliers.
        - Higher values are less sensitive (3.0 is standard, 2.0 would detect more outliers)
      * `window_size` (int, Optional, default: 20): Size of rolling window for detection methods.
        - If not provided, uses global statistics
        - Smaller windows (e.g., 7-14) detect local anomalies, larger windows detect global anomalies
      * `contamination` (float, Optional, default: 0.05): For 'isolation_forest' method, the expected proportion of anomalies.
        - Values typically range from 0.01 (1%) to 0.1 (10%)

    ### Returns:
    A JSON string containing the anomaly detection results and metadata.
    - `result`: Dictionary with structure for each analyzed column:
      ```
      {
        column_name: {
          "timestamps": [...],  # List of datetime values
          "values": [...],      # List of numeric values
          "is_anomaly": [...],  # Boolean flags indicating anomalies
          "anomaly_score": [...], # Scores indicating degree of deviation
          "summary": {
            "total_points": int,
            "anomaly_count": int,
            "percentage": float,
            "method": str      # Method used for detection
          }
        }
      }
      ```
    - `metadata`: Includes `execution_time`, `query_type` ('anomaly_detection'), `source_type`, `anomaly_method`.
    """
    result = _invoke_statistics_api(
        query_type="anomaly_detection",
        columns=columns,
        anomaly_options=anomaly_options,
        data_source=data_source,
        source_type=source_type,
        table_name=table_name,
        filters=filters,
        options=options,
        date_column=date_column,
        start_date=start_date,
        end_date=end_date
    )
    
    return result

def run_server():
    """Run the MCP server."""
    try:
        # Run the server
        mcp.run()
    except Exception as e:
        sys.exit(1)

if __name__ == "__main__":
    run_server() 