"""Generate the request schema for the later native client; no device imports execute."""
import json
from pathlib import Path
from openlocation_backend.models import Request
from openlocation_backend.protocol import PARAMETERS

defs = {}
variants = []
for op, model in PARAMETERS.items():
    params = model.model_json_schema()
    defs.update(params.pop("$defs", {}))
    schema = Request.model_json_schema()
    defs.update(schema.pop("$defs", {}))
    schema["properties"]["op"] = {"const": op}
    schema["properties"]["params"] = params
    schema["required"] = ["id", "op", "params"]
    variants.append(schema)
result = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "OpenLocation IPC v1 request",
          "$defs": defs, "oneOf": variants}
Path(__file__).resolve().parent.parent.joinpath("docs/protocol.schema.json").write_text(json.dumps(result, indent=2) + "\n")
