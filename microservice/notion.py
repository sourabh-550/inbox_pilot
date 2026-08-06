import os
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

notion = Client(auth=os.environ.get("NOTION_TOKEN"))

SOURCES_DB_ID = os.environ.get("NOTION_SOURCES_DB_ID")
ITEMS_DB_ID = os.environ.get("NOTION_ITEMS_DB_ID")


def get_data_source_id(database_id: str) -> str:
    db = notion.databases.retrieve(database_id=database_id)
    return db["data_sources"][0]["id"]


SOURCES_DATA_SOURCE_ID = get_data_source_id(SOURCES_DB_ID)
ITEMS_DATA_SOURCE_ID = get_data_source_id(ITEMS_DB_ID)


def find_or_create_source(name: str) -> str:
    existing = notion.data_sources.query(
        data_source_id=SOURCES_DATA_SOURCE_ID,
        filter={
            "property": "Name",
            "title": {"equals": name}
        }
    )

    if existing["results"]:
        return existing["results"][0]["id"]

    new_source = notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": SOURCES_DATA_SOURCE_ID},
        properties={
            "Name": {"title": [{"text": {"content": name}}]},
            "Category": {"select": {"name": "Other"}},
            "Status": {"select": {"name": "Active"}}
        }
    )
    return new_source["id"]


def create_item(
    title: str,
    source_page_id: str,
    category: str,
    event_date: str = None,
    priority: str = "Medium",
    location_or_link: str = "",
    source_email: str = "",
    attachment_summary: str = "",
    confidence_score: float = 0.0,
    notes: str = ""
):
    category_map = {
        "job_opportunity": "Job Opportunity",
        "meeting": "Meeting",
        "deadline": "Deadline",
        "irrelevant": "Other"
    }

    status = "Scheduled" if event_date else "Logged only"

    properties = {
        "Title": {"title": [{"text": {"content": title}}]},
        "source": {"relation": [{"id": source_page_id}]},
        "category": {"select": {"name": category_map.get(category, "Other")}},
        "status": {"select": {"name": status}},
        "priority": {"select": {"name": priority.capitalize()}},
        "location_or_link": {"rich_text": [{"text": {"content": location_or_link or ""}}]},
        "source_email": {"rich_text": [{"text": {"content": source_email or ""}}]},
        "attachment_summary": {"rich_text": [{"text": {"content": attachment_summary or ""}}]},
        "confidence_score": {"number": confidence_score},
        "notes": {"rich_text": [{"text": {"content": notes or ""}}]}
    }

    if event_date:
        properties["event_date"] = {"date": {"start": event_date}}

    new_item = notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": ITEMS_DATA_SOURCE_ID},
        properties=properties
    )
    return new_item["id"]


def get_todays_items(start_iso: str, end_iso: str) -> list:
    """
    Fetches all Items created between start_iso and end_iso (both ISO 8601
    strings with timezone offset, e.g. "2026-08-06T00:00:00+05:30").
    Uses the "Created time" property (Notion-native timestamp), which is
    filtered differently from a regular Date property — it needs the
    top-level "timestamp": "created_time" filter shape, not a
    property-name-based filter.
    """
    results = []
    cursor = None

    while True:
        query_args = {
            "data_source_id": ITEMS_DATA_SOURCE_ID,
            "filter": {
                "and": [
                    {
                        "timestamp": "created_time",
                        "created_time": {"on_or_after": start_iso}
                    },
                    {
                        "timestamp": "created_time",
                        "created_time": {"before": end_iso}
                    }
                ]
            }
        }
        if cursor:
            query_args["start_cursor"] = cursor

        response = notion.data_sources.query(**query_args)
        results.extend(response["results"])

        if response.get("has_more"):
            cursor = response.get("next_cursor")
        else:
            break

    items = []
    for page in results:
        props = page["properties"]

        def get_title(p):
            arr = p.get("Title", {}).get("title", [])
            return arr[0]["plain_text"] if arr else "Untitled"

        def get_select(p, name):
            sel = p.get(name, {}).get("select")
            return sel["name"] if sel else None

        def get_date(p, name):
            d = p.get(name, {}).get("date")
            return d["start"] if d else None

        items.append({
            "title": get_title(props),
            "category": get_select(props, "category"),
            "status": get_select(props, "status"),
            "priority": get_select(props, "priority"),
            "event_date": get_date(props, "event_date"),
        })

    return items