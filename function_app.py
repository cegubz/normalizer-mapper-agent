"""Azure Functions (Python v2 model) HTTP trigger.

This is the endpoint Azure Logic Apps calls (HTTP action). It is a thin wrapper over
agent.run_agent — no business logic lives here.

Logic Apps usage:
  - Action: "HTTP"
  - Method: POST
  - URI:    https://<funcapp>.azurewebsites.net/api/schema-map?code=<function key>
  - Body:   the request contract documented in agent.py
The response body is the mapping_report + output locations, which downstream Logic Apps
actions can branch on (e.g. route low-confidence runs for review).
"""
import json
import azure.functions as func

from agent import run_agent

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="schema-map", methods=["POST"])
def schema_map(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"status": "failed", "error": "Body must be valid JSON"}),
            status_code=400, mimetype="application/json",
        )

    result = run_agent(payload)
    status_code = 200 if result.get("status") == "succeeded" else 500
    return func.HttpResponse(
        json.dumps(result), status_code=status_code, mimetype="application/json"
    )


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"status": "ok"}), status_code=200, mimetype="application/json"
    )
