"""Write the OpenAPI schema to frontend/lib/openapi.json (input for `npm run gen:types`)."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.main import app  # noqa: E402

out = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "lib" / "openapi.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(app.openapi(), indent=2))
print(f"wrote {out}")
