# User Interaction Tracking API Documentation

## Overview

General idea: we have special interactions=events like a recall, which may have multiple associated subsequent "normal" interactions (peels from search results, marking results good/bad, ..), which generally involve artifacts. Note: There is a better way of phrasing all this I'm sure. Maybe the word "event" is not the best choice, we could use something like "interaction group"..

The external API allows frontend clients (sidecar, webapp, ..) to track user interactions e.g. with the recall results = surfaced artifacts via an update the graph database that puts these interactions in the user's graph. The interactions are also saved in PostgreSQL table `interaction` to be able to reconstruct the graph in case we need to, and do simple analysis/stats for all users using SQL. The API uses an SQS queue to process requests asynchronously. Each interaction is submitted on its own, with a timestamp when the interaction happened. If an interaction has no associated event ID, a new one is created and returned, which then can be referenced by later interactions.  This way, we can flexibly define interaction groupings under the same event ID via the requests the frontend is sending.

If the API request to create a new interaction is successfully queued, the API will return a `PostResponse` object with a status of "ok". If the request fails to be queued, the API will return a `PostResponse` object with a status of "retry" and an error message.

# Interactions

The following kinds (.. type is a reserved word in Python) of interactions are supported:

*    `recall`/`workspace`: Creates a node in the graph database with a label of `RecallEvent` or `WorkspaceEvent`
*    `peel`: Creates an edge in the graph database with a label of `PEEL` between a node with label `RecallEvent` or `WorkspaceEvent` and an artifact
*    `keep`: Creates an edge in the graph database with a label of `KEEP` between a node with label `RecallEvent` or `WorkspaceEvent` and an artifact
*    `mark_good` | `mark_bad`: Creates an edge in the graph database with a label of `MARK_GOOD` or `MARK_BAD` between a node with label `RecallEvent` or `WorkspaceEvent` and an artifact

## Backend API Endpoint

### POST /interaction

### Request Body
The request body should contain a `PostPayload` object with the following properties:

* `artifact_id`: The ID of the artifact associated with the interaction (optional)
* `event_id`: The ID of the event associated with the interaction (optional)
* `kind`: The type of interaction (e.g. "recall", "workspace", "peel", "keep", "mark_good", "mark_bad")
* `metadata`: Additional metadata associated with the interaction (optional)
* `timestamp`: The timestamp of the interaction

### Example Request

To create a new event to add interactions to, submit a POST request with e.g. the following payload like so:

```python
import requests

from datetime import datetime, timezone

#  poetry get-bearer-token -e dev alice+dev1@re-collect.ai *****
bearer_token = 'token'
url = f"https://api.dev.recollect.cloud/interaction"


headers = {
    "Authorization": f"Bearer {bearer_token}"
}

payload = {
    'kind': 'recall',
    'metadata': {'query': 'test recall'},
    'timestamp': datetime.now(timezone.utc).timestamp()
}

response = requests.post(url, headers=headers, json=payload)
```

### Example Response

```json
{
    'status': 'ok',
    'message': None,
    'event_id': 'd3aac781-5229-5aa4-997e-6705b4971007'
}
```

### Adding Interactions to an Event
---------------------------------

The `event_id` returned in the response above can be used to add interactions to the same event. For example, to add a "peel" event to the same event:

```python
url = f"https://api.dev.recollect.cloud/interaction"

payload = {
    'artifact_id': '92074329-d7e2-4bc7-a1cb-4a9c62477b4d',  # wikipedia: united states
    'kind': 'peel',
    'event_id': 'd3aac781-5229-5aa4-997e-6705b4971007',  # the recall event above
    'metadata': None,
    'timest
