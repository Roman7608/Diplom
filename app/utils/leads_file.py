from pathlib import Path
import json
from datetime import datetime
from typing import Dict, Any


LEADS_FILE = Path("leads.json")


def append_lead(lead: Dict[str, Any]) -> None:
    """
    Append lead to leads.json file.
    Adds created_at timestamp automatically.
    """
    if LEADS_FILE.exists():
        try:
            data = json.loads(LEADS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            data = []
    else:
        data = []
    
    # Add timestamp
    lead["created_at"] = datetime.utcnow().isoformat()
    data.append(lead)
    
    # Write back to file
    LEADS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

